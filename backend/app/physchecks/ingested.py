"""Recordings ingested through Veyra's upload path (ROS 1 bag, ROS 2 bag as .zip, MCAP, PX4 ULog, decoded CAN/signal
tables as a .zip of CSVs) -> Physical Check
Engine, and the automatic scenario scan that runs when one arrives.

Nothing is converted or copied: the uploaded file stays where the recordings API stored it (<store>/recordings/rec-*/)
and is read with Veyra's own readers (`ingest.bags`, `ingest.ulog`) at its full rate, without the decimation the
debrief import applies. A signal profile says which topic fields are which roles:

  "recording": {
    "formats": ["ros1_bag", "ros2_bag", "mcap"] | ["px4_ulog"],
    "bind": {"battery": {"types": ["sensor_msgs/msg/BatteryState"], "topic": "/optional/exact/topic"},
             "fault":   {"topic": "/bms/fault_level"}},          # ULog: {"topics": ["battery_status"]}
    "parameters_from_log": {"capacity_ah": {"ulog_param": "BAT_CAPACITY", "unit": "mAh", "min": 1}}
  },
  "roles": {"pack_current": {"field": "battery:current", ...}}   # alias:field path inside the message

A profile is COMPATIBLE when the format is listed and every alias binds to exactly one topic of the recording. Two
candidate topics for one alias is not guessed: the profile is reported as ambiguous. Built-in profiles cover the
vendor-neutral ROS BatteryState and PX4 battery_status messages plus a SYNTHETIC demo profile; private profiles (a
customer's own topics, machine class, conditions) live in <private root>/profiles/*.json and are tried first.

Evidence rows from a recording: file = the uploaded file name, row = the message's index on its topic (1-based),
source = the topic, t = seconds from the recording's first message (log/receive time for bags and MCAP).
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import casedir, scenarios as SC
from app.physchecks.profile import Profile, convert
from app.physchecks.recording import Recording, sha256_file
from app.physchecks.store import Ledger

BUILTIN_PROFILES = Path(__file__).resolve().parent / "profiles"
WINDOW = "recording"
LOADER_VERSION = "ingested-recording-v1"
_RID = re.compile(r"rec-[0-9a-f]{16}")


# ---------------------------------------------------------------- where recordings and profiles live
def recordings_root() -> Path:
    """The folder the recordings API stores uploads in (the parent of the engine store, as recordings_api._folder)."""
    from app.engine_api import DEFAULT_DB
    return Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB).parent / "recordings"


def is_recording_id(value: str) -> bool:
    return bool(_RID.fullmatch(value or ""))


def recording_meta(rid: str) -> Dict[str, Any]:
    if not is_recording_id(rid):
        raise KeyError(rid)
    path = recordings_root() / rid / "recording.json"
    if not path.is_file():
        raise KeyError(rid)
    return json.loads(path.read_text(encoding="utf-8"))


def list_recordings() -> List[Dict[str, Any]]:
    root = recordings_root()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if is_recording_id(d.name) and (d / "recording.json").is_file():
                try:
                    out.append(json.loads((d / "recording.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    out.append({"id": d.name, "filename": "(unreadable recording.json)", "inspection": {"format": "unknown", "topics": []}})
    out.sort(key=lambda m: m.get("uploaded_at") or 0, reverse=True)
    return out


def profile_sources() -> List[Tuple[str, Path]]:
    """(origin, path): private profiles first (most specific to a customer's machines), then built-in ones."""
    out: List[Tuple[str, Path]] = []
    private = casedir.private_root() / "profiles"
    if private.is_dir():
        out += [("private", p) for p in sorted(private.glob("*.json"))]
    out += [("built-in", p) for p in sorted(BUILTIN_PROFILES.glob("*.json"))]
    return out


def recording_profiles() -> List[Tuple[str, Dict[str, Any]]]:
    out = []
    for origin, p in profile_sources():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data.get("recording"), dict):
            out.append((origin, data))
    return out


# ---------------------------------------------------------------- compatibility
def match(meta: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Can this profile read this recording? {status: compatible|incompatible|ambiguous, bindings, reasons}."""
    spec = data["recording"]
    insp = meta.get("inspection") or {}
    fmt = insp.get("format")
    res = {"profile": data["id"], "title": data.get("title") or data["id"], "synthetic": bool(data.get("synthetic")),
           "machine_class": data.get("machine_class"), "bindings": {}, "reasons": [],
           "specificity": sum(1 for b in (spec.get("bind") or {}).values() if b.get("topic") or b.get("topics"))}
    if fmt not in spec.get("formats", []):
        res.update(status="incompatible", reasons=[f"format {fmt or 'unknown'} is not one this profile reads ({', '.join(spec.get('formats', []))})"])
        return res
    topics = insp.get("topics") or []
    status = "compatible"
    for alias, b in (spec.get("bind") or {}).items():
        cands = topics
        if b.get("topic"):
            cands = [t for t in cands if t["topic"] == b["topic"]]
        if b.get("topics"):
            cands = [t for t in cands if t["topic"] in b["topics"]]
        if b.get("types"):
            cands = [t for t in cands if t.get("type") in b["types"] or fmt == "px4_ulog"]
        names = sorted({t["topic"] for t in cands})
        if not names:
            want = b.get("topic") or ", ".join(b.get("topics") or []) or "any topic"
            res["reasons"].append(f"{alias}: no {want}{' of type ' + ' / '.join(b['types']) if b.get('types') else ''} in the recording")
            status = "incompatible"
        elif len(names) > 1:
            res["reasons"].append(f"{alias}: {len(names)} candidate topics ({', '.join(names[:4])}); a private profile must name one")
            if status == "compatible":
                status = "ambiguous"
        else:
            res["bindings"][alias] = names[0]
    if fmt == "decoded_csv_package" and status == "compatible":
        # a table has fixed columns: every role the profile needs must be a column of the group it binds
        cols = {t["topic"]: {f["path"] for f in t.get("fields") or []} for t in topics}
        for role, rs in data.get("roles", {}).items():
            alias, _, col = rs["field"].partition(":")
            group = res["bindings"].get(alias)
            if group is not None and col not in cols.get(group, set()) and not rs.get("optional"):
                res["reasons"].append(f"role {role}: no column {col!r} in the {group} tables")
                status = "incompatible"
    res["status"] = status
    return res


def matches(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every recording profile's verdict, the chosen one first: private before built-in, then the most specific."""
    rows = []
    for i, (origin, data) in enumerate(recording_profiles()):
        m = match(meta, data)
        m["origin"] = origin
        m["_order"] = (0 if m["status"] == "compatible" else 1, 0 if origin == "private" else 1, -m["specificity"], i)
        rows.append(m)
    rows.sort(key=lambda m: m["_order"])
    for m in rows:
        m.pop("_order", None)
    return rows


def chosen(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ms = matches(meta)
    return ms[0] if ms and ms[0]["status"] == "compatible" else None


# ---------------------------------------------------------------- loading
def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _concrete(data: Dict[str, Any], bindings: Dict[str, str]) -> Dict[str, Any]:
    d = copy.deepcopy(data)
    for spec in d["roles"].values():
        alias, _, path = spec["field"].partition(":")
        if alias in bindings:
            spec["field"] = f"{bindings[alias]}:{path}"
    d["bound_topics"] = bindings
    return d


@lru_cache(maxsize=6)
def _load_cached(rid: str, profile_id: str, size: int, mtime: float) -> Tuple[Recording, Profile]:
    meta = recording_meta(rid)
    m = next((x for x in matches(meta) if x["profile"] == profile_id), None)
    if m is None or m["status"] != "compatible":
        raise ValueError(f"profile {profile_id} cannot read {rid}: " + "; ".join((m or {}).get("reasons") or ["unknown profile"]))
    data = next(d for _, d in recording_profiles() if d["id"] == profile_id)
    data = _concrete(data, m["bindings"])
    path = recordings_root() / rid / meta["stored_as"]
    fmt = meta["inspection"]["format"]
    wanted: Dict[str, List[str]] = {}
    for spec in data["roles"].values():
        topic, _, fpath = spec["field"].partition(":")
        wanted.setdefault(topic, [])
        if fpath not in wanted[topic]:
            wanted[topic].append(fpath)
    rec = Recording(rid, synthetic=bool(data.get("synthetic")), label=meta.get("filename", rid),
                    provenance={"loader": f"{LOADER_VERSION}/{fmt}", "file": meta["stored_as"], "sha256": sha256_file(path),
                                "bindings": m["bindings"], "time": data["recording"].get("time", ""), "profile": profile_id})
    notes: List[str] = []
    if fmt == "decoded_csv_package":
        from app.ingest import decoded_csv
        rec.provenance["manifest_check"] = meta["inspection"].get("manifest_check")
        for x in decoded_csv.read(path, wanted):
            rec.add(x["window"], f"{x['group']}:{x['column']}", x["t"], x["value"], row=x["row"], segment=x["segment"], source=x["source"],
                    file=x["file"])
    elif fmt == "px4_ulog":
        from app.ingest import ulog
        log = ulog.read(path, set(wanted))
        first = min((s[0][0] for (tp, mi), s in log["data"].items() if tp in wanted and mi == 0 and s), default=None)
        for (topic, multi), samples in log["data"].items():
            if topic not in wanted or multi != 0:
                continue
            lay = log["layouts"][topic]
            for n, (t_us, raw) in enumerate(samples, start=1):
                for fpath in wanted[topic]:
                    if fpath not in lay.fields:
                        continue
                    v = _number(lay.get(raw, fpath))
                    if v is not None:
                        rec.add(WINDOW, f"{topic}:{fpath}", (t_us - first) / 1e6, v, row=n, source=topic, file=meta["stored_as"])
        for name, p in (data["recording"].get("parameters_from_log") or {}).items():
            raw = log["params"].get(p["ulog_param"])
            if raw is not None and not (isinstance(raw, float) and math.isnan(raw)) and raw >= p.get("min", -math.inf):
                data["parameters"][name] = {"value": round(convert(float(raw), p["unit"], "Ah"), 6) if name.endswith("_ah") else raw,
                                            "source": f"logged parameter {p['ulog_param']} = {raw:g} {p['unit']} (as configured, not measured)"}
            else:
                notes.append(f"logged parameter {p['ulog_param']} = {raw!r}: not usable, so {name} is not stated")
    else:
        from app.ingest import bags
        r = bags.open_recording(path)
        try:
            counts: Dict[str, int] = {}
            start = None
            for msg in bags.iter_messages(r, list(wanted)):
                start = msg.t_ns if start is None else start
                counts[msg.topic] = counts.get(msg.topic, 0) + 1
                for fpath in wanted[msg.topic]:
                    v = _number(bags.get_field(msg.data, fpath))
                    if v is not None:
                        rec.add(WINDOW, f"{msg.topic}:{fpath}", (msg.t_ns - start) / 1e9, v, row=counts[msg.topic], source=msg.topic,
                                file=meta["stored_as"])
        finally:
            r.close()
    rec.provenance["notes"] = notes
    return rec.finalize(), Profile(data)


def load(rid: str, profile_id: Optional[str] = None) -> Tuple[Recording, Profile, Dict[str, Any]]:
    meta = recording_meta(rid)
    if profile_id is None:
        c = chosen(meta)
        if c is None:
            raise ValueError("no signal profile can read this recording: " + "; ".join(
                f"{m['profile']}: {'; '.join(m['reasons'])}" for m in matches(meta)))
        profile_id = c["profile"]
    st = (recordings_root() / rid / meta["stored_as"]).stat()
    rec, prof = _load_cached(rid, profile_id, st.st_size, st.st_mtime)
    return rec, prof, meta


def as_case(rid: str):
    """(recording, profile, case meta, check parameters) for an uploaded recording, as the investigation takes them."""
    rec, prof, meta = load(rid)
    return rec, prof, case_meta(rid, rec, prof, meta), {}


def case_meta(rid: str, rec: Recording, prof: Profile, meta: Dict[str, Any]) -> Dict[str, Any]:
    name = meta.get("filename", rid)
    tag = "SYNTHETIC · " if rec.synthetic and not name.upper().startswith("SYNTHETIC") else ""
    return {"case_id": rid, "title": f"{tag}{name} ({prof.data.get('title', prof.id)})",
            "synthetic": rec.synthetic, "confidential": not rec.synthetic,
            "owner": "Uploaded recording" + (" (generated SYNTHETIC data)" if rec.synthetic else "; treat as the uploader's confidential data"),
            "historical_reports": [], "historical_claims": [], "excluded_files": {}, "recording": True,
            "uploaded_by": meta.get("uploaded_by"), "uploaded_at": meta.get("uploaded_at")}


def integrity(rec: Recording, prof: Optional[Profile] = None) -> Dict[str, Any]:
    return {"manifest": "the stored upload (no manifest supplied)", "verified": [], "mismatched": [], "not_in_manifest": [],
            "file": rec.provenance.get("file"), "sha256": rec.provenance.get("sha256"), "bindings": rec.provenance.get("bindings"),
            "profile": prof.id if prof else None, "profile_note": prof.data.get("note") if prof else None,
            "notes": rec.provenance.get("notes") or [], "time": rec.provenance.get("time")}


# ---------------------------------------------------------------- automatic scanning
def targets(exclude: Optional[str] = None) -> List[Tuple[Recording, Profile]]:
    """Every stored recording that a profile can read, loaded for scanning."""
    out = []
    for meta in list_recordings():
        if meta.get("id") == exclude or chosen(meta) is None:
            continue
        try:
            rec, prof, _ = load(meta["id"])
            out.append((rec, prof))
        except Exception:  # a recording that cannot be read is reported by its own auto-scan record, not here
            continue
    return out


def auto_scan(led: Ledger, rid: str, *, actor: str = "veyra-auto-scan") -> Dict[str, Any]:
    """Run every approved scenario on a newly ingested recording and record what happened (also when nothing could run)."""
    meta = recording_meta(rid)
    verdicts = matches(meta)
    base = {"recording": rid, "filename": meta.get("filename"), "format": (meta.get("inspection") or {}).get("format"),
            "profiles": [{k: m[k] for k in ("profile", "status", "reasons", "bindings", "synthetic", "machine_class", "origin")} for m in verdicts]}
    c = verdicts[0] if verdicts and verdicts[0]["status"] == "compatible" else None
    if c is None:
        payload = base | {"state": "no_compatible_profile", "profile": None, "scenarios": [], "results": []}
        return led.append("ingest_scan", f"recording:{rid}", payload, actor=actor, actor_kind="system")
    try:
        rec, prof, _ = load(rid, c["profile"])
        out = SC.scan(led, [(rec, prof)], actor=actor, trigger="ingest")
        results = [{k: r.get(k) for k in ("scenario_id", "scenario_version", "scenario_title", "outcome", "applicability", "summary",
                                           "matched_original", "reasons")} for r in out["results"]]
        payload = base | {"state": "scanned" if out["scenarios"] else "no_approved_scenarios", "profile": prof.id, "synthetic": rec.synthetic,
                          "scenarios": out["scenarios"], "counts": out["counts"], "results": results}
    except Exception as error:  # a reader or decode failure is recorded with the recording, never swallowed
        payload = base | {"state": "failed", "profile": c["profile"], "error": f"{type(error).__name__}: {error}", "scenarios": [], "results": []}
    return led.append("ingest_scan", f"recording:{rid}", payload, actor=actor, actor_kind="system")


def auto_watch(led: Ledger, rid: str, *, actor: str = "veyra-anticipate") -> Dict[str, Any]:
    """Anticipate on arrival: watch every comparable group of a newly ingested recording for a deteriorating relationship
    (app.physchecks.watch; deterministic, no model, no event needed) and record the result with the recording, also when
    nothing could be assessed."""
    meta = recording_meta(rid)
    c = chosen(meta)
    if c is None:
        return led.append("anticipate_watch", f"recording:{rid}", {"recording": rid, "state": "no_compatible_profile", "groups": []},
                          actor=actor, actor_kind="system")
    try:
        from app.physchecks import compute as _C, watch as _W
        rec, prof, _ = load(rid, c["profile"])
        out = _W.watch(_C.Context(rec, prof))
        payload = {"recording": rid, "state": "watched", "profile": prof.id, "synthetic": rec.synthetic, **out}
    except Exception as error:  # recorded with the recording, never swallowed
        payload = {"recording": rid, "state": "failed", "error": f"{type(error).__name__}: {error}", "groups": []}
    return led.append("anticipate_watch", f"recording:{rid}", payload, actor=actor, actor_kind="system")


def backfill(led: Ledger, scenario_id: str, *, actor: str = "veyra-auto-scan") -> Dict[str, Any]:
    """A scenario was just approved: run it on every stored recording a profile can read."""
    return SC.scan(led, targets(), actor=actor, scenario_ids=[scenario_id], trigger="approval-backfill")


def latest_scan(led: Ledger, rid: str) -> Optional[Dict[str, Any]]:
    rows = led.records(subject=f"recording:{rid}", kinds=["ingest_scan"])
    return rows[-1] if rows else None


def results_for(led: Ledger, rid: str) -> List[Dict[str, Any]]:
    """Latest stored result per scenario for this recording (from automatic, back-fill and manual scans)."""
    latest: Dict[str, Dict[str, Any]] = {}
    for r in led.records(kinds=["scan_result"]):
        p = r["payload"]
        if p.get("recording") == rid:
            latest[p["scenario_id"]] = p | {"at": r["created_at"], "by": r["actor"]}
    return list(latest.values())


def _engineering_on() -> bool:
    return os.environ.get("VEYRA_ENGINEERING_LOCAL") == "true"


def auto_debrief(rid: str) -> Optional[str]:
    """One investigator: when VEYRA_AUTO_DEBRIEF=true, a decoded-table package or a PX4 log that a profile can read is
    debriefed into an engine case on arrival (case, imports, rule run) and linked to the recording, so the arrival
    investigation runs Veyra's main investigator (evidence sheet + physical layer). Bags need a reviewed topic mapping
    and are not debriefed automatically. The case title is neutral: the file name only."""
    if os.environ.get("VEYRA_AUTO_DEBRIEF", "false") != "true":
        return None
    meta = recording_meta(rid)
    if meta.get("case_ids") or (meta.get("inspection") or {}).get("format") not in ("decoded_csv_package", "px4_ulog"):
        return (meta.get("case_ids") or [None])[-1]
    c = chosen(meta)
    if c is None:
        return None
    from app.agent import physical_layer
    from app.physchecks import investigation
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    store = CaseStore(investigation.engine_store_path())
    try:
        out = debrief_recording(store, recordings_root() / rid / meta["stored_as"], {}, machine_id="UPLOAD-" + rid[4:12].upper(),
                                title=f"Uploaded recording {meta.get('filename', rid)}", actor="veyra-auto-debrief",
                                synthetic=bool(c.get("synthetic")), source_name=meta.get("filename"))
    finally:
        store.close()
    physical_layer.link_case(recordings_root() / rid, out["case_id"])
    return out["case_id"]


def on_ingested(rid: str) -> bool:
    """Called by the recordings API after an upload is stored and inspected. Starts the automatic scan in the background
    when the local engineering workflow is enabled; returns whether it started."""
    if not _engineering_on() or not is_recording_id(rid):
        return False
    from app.physchecks_api import ledger

    def run():
        try:
            auto_scan(ledger(), rid)
            if os.environ.get("VEYRA_AUTO_WATCH", "true") != "false":
                auto_watch(ledger(), rid)
            auto_debrief(rid)
            # then investigate it (deterministic checks + earlier knowledge + scenario results + the AI investigator where
            # it may run); stored with the recording, visible in the Recordings view and the replay
            if os.environ.get("VEYRA_AUTO_INVESTIGATE", "true") != "false" and chosen(recording_meta(rid)) is not None:
                from app.physchecks import investigation
                investigation.start(ledger(), rid, lambda: as_case(rid), trigger="ingest")
        except Exception as error:  # recorded, so the page can show it
            try:
                ledger().append("ingest_scan", f"recording:{rid}", {"recording": rid, "state": "failed", "error": str(error),
                                                                   "scenarios": [], "results": []}, actor="veyra-auto-scan", actor_kind="system")
            except Exception:
                pass
    threading.Thread(target=run, daemon=True, name=f"auto-scan-{rid}").start()
    return True
