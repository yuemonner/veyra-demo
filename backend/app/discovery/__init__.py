"""Evidence discovery: what an unfamiliar recording holds, and what it says about an incident, before anyone is asked.

An investigation usually imports a handful of signals. The original recording often holds much more: the controller's
mode and failsafe state, the operator's inputs, the controller's own messages and configuration, actuator feedback,
battery current. This module reads ANY supported recording (ROS 1 / ROS 2 bag, MCAP, PX4 ULog) and:

1. INVENTORY  lists every stream with its message type, count and rate;
2. ROLES      says what each stream measures, from standard message types only (roles.py); label / ground-truth streams
              are excluded before anything else; streams it cannot place are listed as not understood;
3. PROBES     for each incident (an onset time from the investigation), reads the streams by role:
              - controller mode, arming, failsafe and health-flag changes (any text / boolean / few-valued field),
              - the controller's text messages,
              - configuration reported by the controller (MAVLink PARAM_VALUE, PX4 in-flight parameter changes),
              - the raw MAVLink stream's HEARTBEAT system status and SYS_STATUS sensor health,
              - whether the operator's inputs moved at the onset, and the operator input vs the output on the incident channel,
              - which outputs, actuator feedback and battery readings departed from their pre-onset level, and when;
4. GAPS       says which points of the machine's signal chain this recording cannot see, and what that leaves open.

Facts only, each with its stream, time relative to the onset and the rule that produced it. No cause is named here; the
causal layer (app/engine/mechanisms.py) weighs these facts against the machine's signal chain.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from app.discovery import mavlink, roles as R

DISCOVERY_VERSION = "evidence-discovery-v1"
PRE_S = 30.0           # seconds before the onset read for the reference level and for context
POST_S = 30.0          # seconds after the onset read
NEAR_S = 5.0           # "at the onset": within this many seconds either side
MAX_LIST = 25
ARRAY_MAX = 16         # elements of a numeric array field examined
CATEGORICAL_MAX = 8    # a numeric field with at most this many distinct values is treated as a state, not a measurement
_SKIP_FIELD = re.compile(r"(^|\.)(header|seq|stamp|\w*timestamp\w*|time_boot_ms|time_usec|.*covariance.*|variance|"
                         r"magic|checksum|signature|sysid|compid|framing_status|incompat_flags|compat_flags|len|payload64)(\.|\[|$)", re.I)
# A status entry named after a drive, motor, joint, servo or ESC reports on the actuator, not on the controller.
_DRIVE = re.compile(r"(drive|motor|joint|actuator|servo|esc)", re.I)
_CHANNEL = re.compile(r"(?:rc|ch|chan|channel|servo|output|out)[_ ]?(\d{1,2})$", re.I)


class DiscoveryError(ValueError):
    pass


# ---------------------------------------------------------------- sources
class _BagSource:
    """ROS 1 / ROS 2 bags and MCAP files, through the readers in app/ingest/bags.py."""
    px4 = False

    def __init__(self, path: Path):
        from app.ingest import bags
        self._bags = bags
        self.rec = bags.open_recording(path)
        self.format = self.rec.format
        self.start_s = self.rec.start_ns / 1e9 if self.rec.start_ns is not None else None
        self.end_s = self.rec.end_ns / 1e9 if self.rec.end_ns is not None else None
        self.clock = "recording_receive_time"
        self.topics = {t: {"type": i["type"], "count": int(i["count"])} for t, i in self.rec.topics.items()}
        self._first: Dict[str, Any] = {}

    def read(self, names: Sequence[str], t0: Optional[float] = None, t1: Optional[float] = None) -> Iterator[Tuple[str, float, Any]]:
        lo = None if t0 is None else int(t0 * 1e9)
        hi = None if t1 is None else int(t1 * 1e9)
        keep = None if lo is None else (lambda topic, t: lo <= t <= hi)
        for msg in self._bags.iter_messages(self.rec, list(names), keep=keep):
            yield msg.topic, msg.t_ns / 1e9, msg.data

    def get(self, name: str, msg: Any, path: str) -> Any:
        try:
            return self._bags.get_field(msg, path)
        except Exception:  # noqa: BLE001 - a field missing in one message is simply absent
            return None

    def fields(self, name: str, sample: Any) -> List[Tuple[str, str]]:
        """(path, 'number' | 'text') leaf fields, numeric arrays expanded to their first elements."""
        out = []
        for f in self._bags.describe_fields(sample):
            if _SKIP_FIELD.search(f["path"]) or "[0]." in f["path"]:
                continue
            if f["kind"] in ("number", "text"):
                out.append((f["path"], f["kind"]))
            elif f["kind"] == "numbers":
                out += [(f"{f['path']}[{i}]", "number") for i in range(min(f["length"], ARRAY_MAX))]
        return out

    def close(self):
        self.rec.close()


class _ULogSource:
    """PX4 ULog files, through app/ingest/ulog.py. Only topics with a role that is probed are decoded."""
    px4 = True
    format = "px4_ulog"
    PROBED = {"operator_input", "command_link", "controller_mode", "controller_status", "controller_text", "controller_parameters",
              "actuator_command", "actuator_feedback", "power"}

    def __init__(self, path: Path):
        from app.ingest import ulog
        wanted = {t for t, role in R.PX4_TOPICS.items() if role in self.PROBED} | {"vehicle_gps_position", "sensor_gps"}
        self.log = ulog.read(path, wanted)
        self.offset_us, self.clock = ulog.utc_offset(self.log)
        self.start_s = (self.log["first_us"] + self.offset_us) / 1e6 if self.log["first_us"] else None
        self.end_s = (self.log["last_us"] + self.offset_us) / 1e6 if self.log["last_us"] else None
        self.topics: Dict[str, Dict[str, Any]] = {}
        for (topic, multi), n in sorted(self.log["counts"].items()):
            name = topic if multi == 0 else f"{topic}[{multi}]"
            self.topics[name] = {"type": f"px4/{topic}", "count": n, "_key": (topic, multi)}
        if self.log["messages"]:
            self.topics["(logged messages)"] = {"type": "px4/logged_message", "count": len(self.log["messages"])}
        if self.log["param_changes"]:
            self.topics["(parameter changes)"] = {"type": "px4/parameter", "count": len(self.log["param_changes"])}

    def _t(self, t_us: int) -> float:
        return (t_us + self.offset_us) / 1e6

    def read(self, names: Sequence[str], t0: Optional[float] = None, t1: Optional[float] = None) -> Iterator[Tuple[str, float, Any]]:
        items = []
        for name in names:
            key = self.topics.get(name, {}).get("_key")
            for t_us, raw in self.log["data"].get(key, []) if key else []:
                t = self._t(t_us)
                if t0 is None or t0 <= t <= t1:
                    items.append((t, name, raw))
        items.sort(key=lambda x: x[0])
        for t, name, raw in items:
            yield name, t, raw

    def _layout(self, name: str):
        key = self.topics.get(name, {}).get("_key")
        return self.log["layouts"].get(key[0]) if key else None

    def get(self, name: str, raw: bytes, path: str) -> Any:
        lay = self._layout(name)
        base, _, idx = path.partition("[")
        if lay is None or base not in lay.fields:
            return None
        v = lay.get(raw, base)
        if idx:
            i = int(idx.rstrip("]"))
            return v[i] if isinstance(v, list) and i < len(v) else None
        return v

    def fields(self, name: str, sample: Any) -> List[Tuple[str, str]]:
        lay = self._layout(name)
        out = []
        for path, (_off, code, count) in (lay.fields.items() if lay else []):
            if _SKIP_FIELD.search(path):
                continue
            if code == "c":
                out.append((path, "text"))
            elif count:
                out += [(f"{path}[{i}]", "number") for i in range(min(count, ARRAY_MAX))]
            else:
                out.append((path, "number"))
        return out

    def close(self):
        self.log = None


def open_source(path: Path):
    from app.ingest import ulog
    path = Path(path)
    if ulog.is_ulog(path):
        return _ULogSource(path)
    return _BagSource(path)


# ---------------------------------------------------------------- helpers
def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if hasattr(v, "item"):
        try:
            return _num(v.item())
        except (TypeError, ValueError):
            return None
    return None


def _text(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        return v.decode(errors="replace")
    return None


def _rel(t: float, onset: float) -> float:
    return round(t - onset, 2)


def _scalar(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    n = _num(v)
    if n is not None:
        return int(n) if float(n).is_integer() else round(n, 4)
    return _text(v)


def channel_index(scope: Optional[str]) -> Optional[int]:
    """'rudder_rc4' -> 3 (0-based). None when the channel number cannot be read from the name."""
    m = _CHANNEL.search(scope or "")
    return int(m.group(1)) - 1 if m and int(m.group(1)) >= 1 else None


# ---------------------------------------------------------------- probes
def _state_changes(src, streams: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Changes of every state-like field (text, boolean, or a number with few distinct values) over the whole recording.
    DiagnosticArray entries are tracked per status name (level and message)."""
    series: Dict[Tuple[str, str], List[Tuple[float, Any]]] = {}
    sample_dt: Dict[str, float] = {}
    for s in streams:
        name, last_t, gaps, fields = s["stream"], None, [], None
        for _n, t, msg in src.read([name]):
            if last_t is not None:
                gaps.append(t - last_t)
            last_t = t
            if R.short_type(s["type"]) == "diagnostic_msgs/DiagnosticArray":
                for st in (src.get(name, msg, "status") or []):
                    key = _text(src.get(name, st, "name")) or "?"
                    series.setdefault((name, f"{key}: level"), []).append((t, _scalar(src.get(name, st, "level"))))
                    series.setdefault((name, f"{key}: message"), []).append((t, _text(src.get(name, st, "message"))))
                continue
            if fields is None:
                fields = src.fields(name, msg)
            for path, _kind in fields:
                series.setdefault((name, path), []).append((t, _scalar(src.get(name, msg, path))))
        if gaps:
            sample_dt[name] = round(median(gaps), 3)
    changes = []
    for (name, path), vals in series.items():
        distinct = {v for _t, v in vals}
        if len(distinct) > CATEGORICAL_MAX or not distinct:
            continue
        if all(isinstance(v, float) for v in distinct):
            continue  # a continuous measurement that happened to take few values
        prev = vals[0][1]
        first = {"stream": name, "field": path, "t": vals[0][0], "value": prev}
        for t, v in vals[1:]:
            if v != prev:
                changes.append({"stream": name, "field": path, "t": t, "from": prev, "to": v})
                prev = v
        changes.append({**first, "initial": True})
    return {"changes": changes, "series": series, "sample_dt": sample_dt}


def _texts(src, streams: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in streams:
        name = s["stream"]
        if name == "(logged messages)":
            out += [{"stream": name, "t": src._t(t), "level": level, "text": text[:200]} for t, level, text in src.log["messages"]]
            continue
        for _n, t, msg in src.read([name]):
            text = _text(src.get(name, msg, "text")) or _text(src.get(name, msg, "msg")) or _text(src.get(name, msg, "message"))
            level = src.get(name, msg, "severity")
            level = src.get(name, msg, "level") if level is None else level
            if text:
                out.append({"stream": name, "t": t, "level": _scalar(level), "text": text[:200]})
    return out


def _mavlink(src, streams: List[Dict[str, Any]]) -> Dict[str, Any]:
    params, texts, status, health, acks, counts = [], [], [], [], [], {}
    last_hb: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    last_sys: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for s in streams:
        name = s["stream"]
        for _n, t, msg in src.read([name]):
            msgid = _num(src.get(name, msg, "msgid"))
            if msgid is None:
                continue
            msgid = int(msgid)
            counts[msgid] = counts.get(msgid, 0) + 1
            if msgid not in mavlink.SIZES:
                continue
            words = src.get(name, msg, "payload64")
            words = list(words.tolist() if hasattr(words, "tolist") else words or [])
            data = mavlink.decode(msgid, mavlink.payload(words, int(_num(src.get(name, msg, "len")) or 0), msgid))
            if data is None:
                continue
            who = (_scalar(src.get(name, msg, "sysid")), _scalar(src.get(name, msg, "compid")))
            if msgid == 22:
                params.append({"stream": name, "t": t, **data})
            elif msgid == 253:
                texts.append({"stream": name, "t": t, "level": data["severity"], "text": data["text"]})
            elif msgid == 0:
                prev = last_hb.get(who)
                for key in ("system_status", "armed", "custom_mode"):
                    if prev is not None and prev[key] != data[key]:
                        status.append({"stream": name, "t": t, "field": f"heartbeat {key} (system {who[0]})", "from": prev[key], "to": data[key]})
                last_hb[who] = data
            elif msgid == 1:
                prev = last_sys.get(who)
                if prev is not None:
                    for key in ("enabled", "health"):
                        lost, gained = mavlink.bits(prev[key] & ~data[key] & data["present"]), mavlink.bits(data[key] & ~prev[key])
                        if lost or gained:
                            health.append({"stream": name, "t": t, "field": key, "lost": lost, "gained": gained})
                last_sys[who] = data
            elif msgid == 77:
                acks.append({"stream": name, "t": t, **data})
    return {"params": params, "texts": texts, "status": status, "health": health, "acks": acks, "msgid_counts": counts,
            "heartbeat_at_end": {f"{k[0]}/{k[1]}": v for k, v in last_hb.items()}}


def _departures(src, streams: List[Dict[str, Any]], onset: float) -> Dict[str, Any]:
    """Which numeric fields left their pre-onset level near the onset, and when. Level = median of the PRE_S seconds before
    the onset (up to 1 s before it); band = max(5 robust SD, 2 % of the level); a departure needs 3 consecutive samples outside."""
    out, steady, values_at = [], [], {}
    for s in streams:
        name = s["stream"]
        series: Dict[str, List[Tuple[float, float]]] = {}
        fields = None
        for _n, t, msg in src.read([name], onset - PRE_S, onset + POST_S):
            if fields is None:
                fields = [p for p, k in src.fields(name, msg) if k == "number"]
            for path in fields:
                v = _num(src.get(name, msg, path))
                if v is not None:
                    series.setdefault(path, []).append((t, v))
        for path, vals in series.items():
            pre = [v for t, v in vals if t < onset - 1.0]
            near = [(t, v) for t, v in vals if onset - NEAR_S <= t <= onset + NEAR_S]
            if len(pre) < 5 or len(near) < 3:
                continue
            level = median(pre)
            mad = median(abs(v - level) for v in pre)
            band = max(5 * 1.4826 * mad, 0.02 * abs(level), 1e-6)
            values_at[(name, path)] = {"before": level, "near": [v for _t, v in near]}
            run, dep = 0, None
            for i, (t, v) in enumerate(near):
                run = run + 1 if abs(v - level) > band else 0
                if run == 3:
                    dep = near[i - 2]
                    break
            if dep is None:
                steady.append({"stream": name, "field": path, "level": _scalar(level)})
                continue
            after = [v for t, v in vals if dep[0] <= t <= dep[0] + 3.0]
            out.append({"stream": name, "field": path, "t": dep[0], "before": _scalar(level), "after": _scalar(median(after)),
                        "band": _scalar(band)})
    out.sort(key=lambda d: d["t"])
    return {"departed": out, "steady": steady, "values": values_at}


# ---------------------------------------------------------------- main entry
def inventory(src) -> Dict[str, Any]:
    duration = (src.end_s - src.start_s) if src.start_s is not None and src.end_s is not None else None
    streams, excluded, unknown = [], [], []
    for name, info in sorted(src.topics.items()):
        if R.is_label(name):
            excluded.append({"stream": name, "type": R.short_type(info["type"]), "count": info["count"],
                             "reason": "label / ground-truth stream (a dataset's answer key): never used as evidence"})
            continue
        if src.px4:
            base = name.split("[")[0]
            role, why = ({"(logged messages)": ("controller_text", "PX4 logged text messages"),
                          "(parameter changes)": ("controller_parameters", "PX4 parameters changed after logging started")}
                         .get(name) or R.role_for(base, None, px4=True))
        else:
            role, why = R.role_for(name, info["type"])
        item = {"stream": name, "type": R.short_type(info["type"]), "count": info["count"],
                "rate_hz": round(info["count"] / duration, 2) if duration else None, "role": role, "why": why}
        (streams if role else unknown).append(item)
    return {"format": src.format, "clock": src.clock, "start_s": src.start_s, "end_s": src.end_s,
            "duration_s": round(duration, 3) if duration else None, "streams": streams, "excluded": excluded, "not_understood": unknown}


def _by_role(inv: Dict[str, Any], *wanted: str) -> List[Dict[str, Any]]:
    return [s for s in inv["streams"] if s["role"] in wanted and s["count"]]


def probe(src, inv: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
    onset = float(incident["t"])
    lo, hi = onset - PRE_S, onset + POST_S
    within = lambda t: lo <= t <= hi
    near = lambda t: abs(t - onset) <= NEAR_S
    facts: List[Dict[str, Any]] = []

    duration = inv.get("duration_s") or None

    def fact(kind: str, point: str, t: Optional[float], text: str, stream: str, occurrences: Optional[int] = None, **extra):
        """occurrences: how often this same kind of record happens in the whole recording. From it, the chance that one
        falls within the 6 s before the onset (5 s) or just after it (1 s) anyway: Poisson, 1 - exp(-n * 6 s / duration)."""
        chance = None
        if occurrences is not None and duration:
            chance = round(1 - math.exp(-occurrences * (NEAR_S + 1.0) / duration), 3)
        facts.append({"kind": kind, "point": point, "t_rel_s": None if t is None else _rel(t, onset), "near_onset": t is not None and near(t),
                      "text": text, "stream": stream, "occurrences_in_recording": occurrences, "chance_near_onset": chance, **extra})

    # Controller mode / status: state changes over the whole recording.
    state_streams = _by_role(inv, "controller_mode", "controller_status")
    states = _state_changes(src, state_streams) if state_streams else {"changes": [], "sample_dt": {}}
    mode_summary = []
    n_changes_of: Dict[Tuple[str, str], int] = {}
    for c in states["changes"]:
        if not c.get("initial"):
            n_changes_of[(c["stream"], c["field"])] = n_changes_of.get((c["stream"], c["field"]), 0) + 1
    for c in states["changes"]:
        role = next((s["role"] for s in state_streams if s["stream"] == c["stream"]), "controller_mode")
        if c.get("initial"):
            continue
        if within(c["t"]):
            point = "actuator" if _DRIVE.search(c["field"]) and role == "controller_status" else "controller"
            fact("state_change", point, c["t"], f"{c['stream']} {c['field']}: {c['from']} → {c['to']}", c["stream"],
                 occurrences=n_changes_of[(c["stream"], c["field"])], role=role, field=c["field"], before=c["from"], after=c["to"])
    for s in state_streams:
        if s["role"] != "controller_mode":
            continue
        fields = {c["field"] for c in states["changes"] if c["stream"] == s["stream"]}
        for f in sorted(fields):
            seq = [c for c in states["changes"] if c["stream"] == s["stream"] and c["field"] == f]
            at = None
            for c in seq:
                if c["t"] <= onset:
                    at = c.get("value", c.get("to"))
            n_changes = sum(1 for c in seq if not c.get("initial"))
            mode_summary.append({"stream": s["stream"], "field": f, "at_onset": at, "changes_in_recording": n_changes,
                                 "changes_in_window": sum(1 for c in seq if not c.get("initial") and within(c["t"])),
                                 "sample_interval_s": states["sample_dt"].get(s["stream"])})

    # Controller text.
    text_streams = _by_role(inv, "controller_text")
    texts = _texts(src, text_streams) if text_streams else []
    # Raw MAVLink: heartbeat status, sensor health, parameters, status text.
    link_streams = [s for s in _by_role(inv, "command_link") if s["type"] == "mavros_msgs/Mavlink"]
    link = _mavlink(src, link_streams) if link_streams else None
    if link:
        texts += link["texts"]
        for c in link["status"]:
            if within(c["t"]):
                fact("state_change", "controller", c["t"], f"MAVLink {c['field']}: {c['from']} → {c['to']}", c["stream"],
                     occurrences=sum(1 for x in link["status"] if x["field"] == c["field"]), role="controller_mode",
                     field=c["field"], before=c["from"], after=c["to"])
        for h in link["health"]:
            if within(h["t"]):
                parts = ([f"lost {', '.join(h['lost'])}"] if h["lost"] else []) + ([f"regained {', '.join(h['gained'])}"] if h["gained"] else [])
                fact("health_change", "controller", h["t"], f"MAVLink SYS_STATUS sensors {h['field']}: {'; '.join(parts)}", h["stream"],
                     occurrences=len(link["health"]), role="controller_status")
    same_text: Dict[str, int] = {}
    shape = lambda text: re.sub(r"[0-9]+", "#", text)  # noqa: E731 - "Event dropped (99, 65526)" and "(65529, 101)" are the same message
    for m in texts:
        same_text[shape(m["text"])] = same_text.get(shape(m["text"]), 0) + 1
    for m in texts:
        if within(m["t"]):
            fact("controller_text", "controller", m["t"], f"[{m['level']}] {m['text']}", m["stream"], occurrences=same_text[shape(m["text"])],
                 role="controller_text")

    # Configuration reported by the controller.
    params = []
    if link:
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for p in link["params"]:
            by_name.setdefault(p["name"], []).append(p)
        for pname, reports in by_name.items():
            routine = pname.startswith(mavlink.ROUTINE_PARAM_PREFIXES)
            for i, p in enumerate(reports):
                if not within(p["t"]):
                    continue
                prev = reports[i - 1]["value"] if i else None
                params.append({"name": pname, "value": p["value"], "t_rel_s": _rel(p["t"], onset), "routine": routine,
                               "previous_report": prev, "reports_in_recording": len(reports)})
                if not routine and (i == 0 or prev != p["value"]):
                    changes = 1 + sum(1 for a, b in zip(reports, reports[1:]) if a["value"] != b["value"])
                    fact("parameter_reported", "controller", p["t"], occurrences=changes, text=
                         (f"parameter {pname} = {p['value']:g} reported by the controller"
                          + (f" (previously {prev:g})" if prev is not None else " (first report of it in this recording)")),
                         stream=p["stream"], role="controller_parameters", parameter=pname, value=p["value"], previous=prev)
    if src.px4:
        for t_us, pname, value in src.log["param_changes"]:
            t = src._t(t_us)
            params.append({"name": pname, "value": value, "t_rel_s": _rel(t, onset), "routine": False, "time_approximate": True})
            if within(t):
                fact("parameter_reported", "controller", t, f"parameter {pname} changed to {value} during the flight (time approximate)",
                     "(parameter changes)", occurrences=sum(1 for _t, n, _v in src.log["param_changes"] if n == pname),
                     role="controller_parameters", parameter=pname, value=value)

    # Operator input, outputs, actuator feedback, power: departures from the pre-onset level.
    numeric = {role: _departures(src, _by_role(inv, role), onset) for role in ("operator_input", "actuator_command", "actuator_feedback", "power")
               if _by_role(inv, role)}
    for role, d in numeric.items():
        point = R.DEFAULT_POINT[role]
        for dep in d["departed"][:MAX_LIST]:
            fact("departure", point, dep["t"], f"{dep['stream']} {dep['field']}: {dep['before']} → {dep['after']}", dep["stream"], role=role,
                 field=dep["field"], before=dep["before"], after=dep["after"])
    operator = None
    if "operator_input" in numeric:
        d = numeric["operator_input"]
        moved = [x for x in d["departed"] if near(x["t"])]
        operator = {"moved_near_onset": [{"field": x["field"], "t_rel_s": _rel(x["t"], onset), "before": x["before"], "after": x["after"]} for x in moved],
                    "steady_fields": len(d["steady"])}
        if not moved:
            fact("operator_steady", "operator", None, f"No operator input moved within {NEAR_S:g} s of the onset ({len(d['steady'])} input fields steady)",
                 ", ".join(s["stream"] for s in _by_role(inv, "operator_input")), role="operator_input")

    # The incident channel: operator input vs output (same channel number assumed to be the same function).
    channel = None
    idx = channel_index(incident.get("channel"))
    if idx is not None and "operator_input" in numeric and "actuator_command" in numeric:
        def pick(role):
            for (stream, path), v in numeric[role]["values"].items():
                if re.search(rf"\[{idx}\]$", path):
                    after = [x for x in v["near"]]
                    return {"stream": stream, "field": path, "before": _scalar(v["before"]), "near_onset": [_scalar(min(after)), _scalar(max(after))]}
            return None
        inp, outp = pick("operator_input"), pick("actuator_command")
        if inp and outp:
            channel = {"channel": incident.get("channel"), "index": idx, "operator_input": inp, "output": outp,
                       "assumption": f"Operator input {idx + 1} and output {idx + 1} are taken to be the same function (standard channel order); "
                                     "the servo function parameters that would confirm this are not checked"}
            fact("operator_vs_output", "operator", None,
                 f"channel {idx + 1}: operator input {inp['near_onset'][0]}–{inp['near_onset'][1]} vs output {outp['near_onset'][0]}–{outp['near_onset'][1]} "
                 f"within {NEAR_S:g} s of the onset", f"{inp['stream']} / {outp['stream']}", role="operator_input",
                 input_range=inp["near_onset"], output_range=outp["near_onset"])

    facts.sort(key=lambda f: (f["t_rel_s"] is None, f["t_rel_s"] if f["t_rel_s"] is not None else 0))
    return {"incident": {**incident, "window_s": [-PRE_S, POST_S], "near_s": NEAR_S}, "facts": facts[:4 * MAX_LIST],
            "controller_mode": mode_summary, "texts_in_recording": len(texts),
            "parameters_in_window": params[:MAX_LIST * 2], "operator": operator, "channel": channel,
            "mavlink_messages": ({str(k): v for k, v in sorted(link["msgid_counts"].items())} if link else None),
            "departures": {role: {"departed": [{**x, "t_rel_s": _rel(x["t"], onset)} for x in d["departed"][:MAX_LIST]],
                                  "steady": len(d["steady"])} for role, d in numeric.items()}}


def gaps(inv: Dict[str, Any], chain: Optional[Dict[str, Any]], link_counts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Points of the signal chain this recording cannot see, and specific records that are absent."""
    present = {s["role"] for s in inv["streams"] if s["count"]}
    out = []
    for p in (chain or {}).get("points", []):
        if not set(p.get("roles", [])) & present:
            out.append({"point": p["id"], "missing": p["label"], "means": p.get("if_missing") or f"Nothing in this recording measures {p['label'].lower()}."})
    if "controller_text" not in present and not (link_counts or {}).get("253"):
        out.append({"point": "controller", "missing": "Controller text messages", "means": "No status or log text from the controller is in this recording."})
    if "controller_parameters" not in present and not (link_counts or {}).get("22"):
        out.append({"point": "controller", "missing": "Controller configuration", "means": "No parameter values reported by the controller are in this recording."})
    return out


def discover(path: Path, incidents: Iterable[Dict[str, Any]], chain: Optional[Dict[str, Any]] = None, *, source_name: Optional[str] = None,
             sha256: Optional[str] = None) -> Dict[str, Any]:
    """Inventory, roles, probes for each incident ({'id', 't' epoch s, 'label', optional 'channel'}) and gaps."""
    path = Path(path)
    src = open_source(path)
    try:
        inv = inventory(src)
        results = []
        for inc in incidents:
            if inc.get("t") is None:
                continue
            if inv["start_s"] is not None and not (inv["start_s"] - PRE_S <= float(inc["t"]) <= inv["end_s"] + POST_S):
                results.append({"incident": inc, "facts": [], "outside_recording": True})
                continue
            results.append(probe(src, inv, inc))
        link_counts = next((r.get("mavlink_messages") for r in results if r.get("mavlink_messages")), None)
        used_roles = {"operator_input", "command_link", "controller_mode", "controller_status", "controller_text", "controller_parameters",
                      "actuator_command", "actuator_feedback", "power"}
        return {"version": DISCOVERY_VERSION,
                "source": {"name": source_name or path.name, "sha256": sha256, "format": inv["format"], "clock": inv["clock"],
                           "start_s": inv["start_s"], "end_s": inv["end_s"], "duration_s": inv["duration_s"]},
                "inventory": {"streams": inv["streams"], "excluded": inv["excluded"], "not_understood": inv["not_understood"],
                              "read_by_probes": sorted(s["stream"] for s in inv["streams"] if s["role"] in used_roles and s["count"]),
                              "roles_present": sorted({s["role"] for s in inv["streams"] if s["count"]})},
                "incidents": results, "gaps": gaps(inv, chain, link_counts),
                "rules": {"window_s": [-PRE_S, POST_S], "near_onset_s": NEAR_S,
                          "departure": "median of the 30 s before the onset (to 1 s before it); outside max(5 robust SD, 2 % of that level) for 3 consecutive samples within 5 s of the onset",
                          "state_field": f"text, boolean, or integer field with at most {CATEGORICAL_MAX} distinct values in the recording",
                          "parameters": "MAVLink PARAM_VALUE as reported by the controller (sent when a parameter is set or requested; the sender is not in this stream); ArduPilot STAT_* counters are routine",
                          "labels": "streams named like failure_status, ground_truth, labels or annotations are excluded before any probe"},
                "boundary": "Facts read from the original recording, each with its stream and time relative to the onset. No cause is named."}
    finally:
        src.close()
