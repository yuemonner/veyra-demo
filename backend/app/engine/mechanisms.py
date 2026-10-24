"""The causal layer: where in the machine's signal chain each anomaly was measured, which mechanisms can produce an
anomaly at that point, and which recorded evidence discriminates between them.

A signal is not a cause. "rudder output held 1900 µs" is measured at the flight controller's OUTPUT, before the servo:
a jammed servo cannot change that number except by making the aircraft move wrongly first, so that the controller reacts.
And signals that move together are not independent evidence: the roll departure 0.7 s after the rudder hold is what a
rudder hold does to the aircraft.

The machine profile describes the chain (`signal_chain`): ordered measurement POINTS (operator input → link →
controller → output command → actuator → motion), which signals and recording roles measure each point, and the
MECHANISMS that can act between them (operator command, link fault, controller behaviour, output path, physical
response, sensor error), each with the evidence that would discriminate it. This module:

1. collects the run's anomalies with their onset times and places each at its measurement point;
2. groups anomalies whose onsets fall within the chain's lag of the first one into one EVENT; in an event, the earliest
   anomaly LEADS and later anomalies downstream of it are listed as expected consequences, not independent evidence;
3. for the lead, checks every mechanism:
   - forward: a mechanism acting at or upstream of the lead's point can produce it;
   - feedback: a mechanism downstream can produce it only through the controller reacting to measured motion, which
     needs the motion to depart FIRST; if the recording shows the motion departing after the lead, that path is unlikely;
   - discriminating evidence from the evidence-discovery record (mode / failsafe / configuration / controller messages at
     the onset, the operator's inputs, actuator feedback) supports, weakens or leaves each mechanism open;
4. says what record would separate the mechanisms that remain.

Statuses: supported (recorded evidence points to it), possible (consistent; nothing discriminates yet), unlikely (the
recorded order or a recorded input argues against it), cannot_tell (the record that would decide is not in the case).
Never "confirmed": confirming a cause needs an inspection, a reproduction or a fix that holds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MECHANISMS_VERSION = "signal-chain-v1.2"
DEFAULT_LAG_S = 3.0
NEAR_S = 5.0
RARE_P = 0.2          # a record supports a cause only if a record like it would land this close to the onset by chance less often than this
CAUSE_LATEST_S = 1.0   # a record can support a cause only if it comes before the onset (1 s allowance: state topics are sampled at ~1 Hz)


def _iso_t(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S.%f")[:-4]


def point_of(chain: Dict[str, Any], signal: Optional[str]) -> Optional[str]:
    for p in chain["points"]:
        if signal in p.get("signals", []):
            return p["id"]
    if signal:
        for p in chain["points"]:
            if any(signal.startswith(prefix.rstrip("*")) for prefix in p.get("signals", []) if prefix.endswith("*")):
                return p["id"]
    return None


def anomalies(run: Dict[str, Any], chain: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The run's anomalies with signal, scope, onset (epoch s, None when only window-level) and measurement point."""
    f = run.get("findings") or {}
    out: List[Dict[str, Any]] = []

    def add(key, signal, scope, t, text, rule):
        out.append({"key": key, "signal": signal, "scope": scope, "t": t, "text": text, "rule": rule, "point": point_of(chain, signal)})

    for x in (f.get("actuator_outputs") or {}).get("findings") or []:
        add(f"{x['rule']}:{x['channel']}", "actuator.output", x["channel"], x.get("t_start"), x.get("text", ""), x["rule"])
    for x in (f.get("tracking") or {}).get("findings") or []:
        add(f"TRACKING:{x.get('axis')}", "vehicle.attitude", x.get("axis"), x.get("t_start"), x.get("text", ""), "TRACKING")
    for s in (f.get("reference_comparison") or {}).get("signals") or []:
        if s.get("status") in ("higher_than_reference", "lower_than_reference"):
            add(f"REFERENCE:{s['signal']}:{s.get('scope')}", s["signal"], s.get("scope"), None,
                f"{s['signal']} ({s.get('scope')}) {s['status'].replace('_', ' ')}: median {s['incident']['median']:g} vs {s['reference']['median']:g}", "REFERENCE")
    for m in f.get("measurements") or []:
        for c in m.get("crossings") or []:
            if m.get("crossing_in_window"):
                add(f"THRESHOLD:{m['signal']}:{m.get('scope')}", m["signal"], m.get("scope"), _iso_t(c.get("start")),
                    f"{m['signal']} ({m.get('scope')}) above its screening limit (peak {c.get('peak')})", "THRESHOLD")
                break
    for s in (f.get("transients") or {}).get("signals") or []:
        for e in s.get("events") or []:
            if e.get("in_symptom_window"):
                add(f"TRANSIENT:{s.get('signal')}:{s.get('scope')}", s.get("signal"), s.get("scope"), e.get("t_start"),
                    f"{s.get('signal')} ({s.get('scope')}) brief departure", "TRANSIENT")
                break
    return out


def _index(chain: Dict[str, Any]) -> Dict[str, int]:
    return {p["id"]: i for i, p in enumerate(chain["points"])}


def _incident_facts(discovery: Optional[Dict[str, Any]], t: Optional[float]) -> Optional[Dict[str, Any]]:
    if not discovery or t is None:
        return None
    best = None
    for inc in discovery.get("incidents") or []:
        it = (inc.get("incident") or {}).get("t")
        if it is not None and abs(float(it) - t) <= 1.0 and (best is None or abs(float(it) - t) < abs(float(best["incident"]["t"]) - t)):
            best = inc
    return best


def _status(entry: Dict[str, Any], status: str, evidence: str, kind: str = "evidence"):
    order = {"supported": 3, "unlikely": 2, "possible": 1, "cannot_tell": 0, "open": -1}
    entry.setdefault("evidence", []).append({"kind": kind, "text": evidence, "bearing": status})
    if order[status] > order[entry["status"]]:
        entry["status"] = status


def redundancy(rows: Optional[List[Dict[str, Any]]], signal: Optional[str], scope: Optional[str], onset: float) -> Optional[Dict[str, Any]]:
    """Independent measurements of the same quantity (same signal and unit, other scopes, e.g. altitude from barometer,
    GNSS and the estimator): how far each moved from its level in the 30 s before the onset to the 5 s after it.
    A sensor error moves one measurement and not the others; real motion moves them all."""
    if not rows or not signal or scope is None:
        return None
    by_scope: Dict[str, List[Any]] = {}
    for r in rows:
        if r.get("signal") == signal and isinstance(r.get("value"), (int, float)) and onset - 30.0 <= r["t"] <= onset + 5.0:
            by_scope.setdefault(str(r.get("scope")), []).append(r)
    if str(scope) not in by_scope or len(by_scope) < 2:
        return None
    from statistics import median
    moved, first = {}, {}
    for sc, items in by_scope.items():
        items.sort(key=lambda r: r["t"])
        pre = [r["value"] for r in items if r["t"] < onset - 1.0]
        post = [r for r in items if onset - 5.0 <= r["t"] <= onset + 5.0]
        if len(pre) >= 5 and len(post) >= 3:
            level = median(pre)
            band = max(5 * 1.4826 * median(abs(v - level) for v in pre), 1e-6)
            moved[sc] = max(abs(r["value"] - level) for r in post if r["t"] >= onset) if any(r["t"] >= onset for r in post) else 0.0
            run = 0
            for i, r in enumerate(post):
                run = run + 1 if abs(r["value"] - level) > band else 0
                if run == 3:
                    if i > 2:  # outside the band from the first sample scanned: already departed, time not readable
                        first[sc] = post[i - 2]["t"]
                    break
    if str(scope) not in moved or len(moved) < 2:
        return None
    own = moved[str(scope)]
    others = {k: round(v, 3) for k, v in moved.items() if k != str(scope)}
    unit = next((r.get("unit") for r in by_scope[str(scope)] if r.get("unit")), "")
    if own > 0 and all(v < own / 3 for v in others.values()):
        verdict = "alone"
    elif all(v >= own / 2 for v in others.values()):
        verdict = "together"
    else:
        verdict = "mixed"
    order = sorted(first.items(), key=lambda kv: kv[1])
    led = order[0][0] if order else None
    lead_by = round(order[1][1] - order[0][1], 2) if len(order) > 1 else None
    return {"signal": signal, "scope": scope, "moved": round(own, 3), "others": others, "unit": unit, "verdict": verdict,
            "first_to_depart": led, "led_by_s": lead_by, "departure_times": {k: round(v - onset, 2) for k, v in first.items()}}


def assess(run: Dict[str, Any], chain: Optional[Dict[str, Any]], discovery: Optional[Dict[str, Any]] = None,
           rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if not chain or not chain.get("points"):
        return {"version": MECHANISMS_VERSION, "applicable": False, "reason": "No signal chain in the machine profile"}
    idx = _index(chain)
    lag = float(chain.get("lag_s") or DEFAULT_LAG_S)
    items = anomalies(run, chain)
    if not items:
        return {"version": MECHANISMS_VERSION, "applicable": False, "reason": "No anomalies in this investigation run"}
    timed = sorted([a for a in items if a["t"] is not None and a["point"]], key=lambda a: a["t"])
    untimed = [a for a in items if a["t"] is None or not a["point"]]
    events: List[Dict[str, Any]] = []
    for a in timed:
        if events and a["t"] - events[-1]["lead"]["t"] <= lag:
            events[-1]["members"].append(a)
        else:
            events.append({"lead": a, "members": [a]})

    out_events = []
    for ev in events:
        lead = ev["lead"]
        li = idx[lead["point"]]
        facts_rec = _incident_facts(discovery, lead["t"])
        facts = (facts_rec or {}).get("facts") or []
        roles_present = set((discovery or {}).get("inventory", {}).get("roles_present") or [])
        members = []
        for m in ev["members"]:
            if m is lead:
                members.append({**m, "relation": "lead", "dt_s": 0.0})
                continue
            dt = round(m["t"] - lead["t"], 2)
            mi = idx[m["point"]]
            relation = ("consequence" if mi > li else "same_point" if mi == li else "upstream_later")
            members.append({**m, "relation": relation, "dt_s": dt,
                            "note": {"consequence": f"{dt:g} s after the lead and downstream of it in the chain: what the lead would cause; not independent evidence",
                                     "same_point": f"{dt:g} s after the lead at the same point: part of the same event",
                                     "upstream_later": f"{dt:g} s after the lead but upstream of it: possibly the controller reacting to the lead"}[relation]})
        # Discovery departures (outputs, feedback, power) placed on the same timeline.
        timeline = [{"t_rel_s": 0.0, "point": lead["point"], "text": lead["text"], "source": "investigation"}]
        timeline += [{"t_rel_s": m["dt_s"], "point": m["point"], "text": m["text"], "source": "investigation"} for m in members if m is not members[0] and m["relation"] != "lead"]
        timeline += [{"t_rel_s": f["t_rel_s"], "point": f["point"], "text": f["text"], "source": f"recording: {f['stream']}"}
                     for f in facts if f.get("t_rel_s") is not None and abs(f["t_rel_s"]) <= NEAR_S * 2]
        timeline.sort(key=lambda x: x["t_rel_s"])

        motion_first = [m for m in members if m["point"] and idx[m["point"]] > li and m["t"] < lead["t"]]
        motion_after = [m for m in members if m["point"] and idx[m["point"]] > li and m["t"] >= lead["t"]]
        downstream_measured = any(set(p.get("roles", [])) & roles_present or any(a["point"] == p["id"] for a in items)
                                  for p in chain["points"][li + 1:])
        mech_out = []
        for mech in chain.get("mechanisms", []):
            acts = idx.get(mech["acts_on"], 0)
            entry = {"id": mech["id"], "label": mech["label"], "domain": mech.get("domain"), "acts_on": mech["acts_on"], "status": "open", "short": mech.get("short") or mech["label"],
                     "evidence": [], "needs": mech.get("needs"), "check_id": mech.get("check_id")}
            if acts > li or mech.get("measurement_only"):
                # Downstream of where the lead was measured: only through the controller reacting to measured motion.
                if lead["point"] == mech["acts_on"] and mech.get("measurement_only"):
                    red = (redundancy(rows, lead["signal"], lead["scope"], lead["t"])
                           if lead["signal"] in (chain.get("redundant_signals") or []) else None)
                    if red is None:
                        _status(entry, "cannot_tell", "No second, independent measurement of the same quantity is in the case to compare with.")
                    else:
                        others = ", ".join(f"{k} {v:g}" for k, v in red["others"].items())
                        text = (f"{red['signal']} ({red['scope']}) moved {red['moved']:g} {red['unit']} within 5 s of the onset; the other measurements of it moved {others}")
                        if red["verdict"] == "alone":
                            _status(entry, "supported", text + ": only this measurement moved.", "redundancy")
                        elif red["verdict"] == "together" and red["first_to_depart"] not in (None, str(lead["scope"])):
                            own_t = red["departure_times"].get(str(lead["scope"]))
                            _status(entry, "unlikely", text + f": the motion was real, and {red['first_to_depart']} departed first "
                                                              f"({red['departure_times'][red['first_to_depart']]:+g} s"
                                                              + (f"; this measurement at {own_t:+g} s)." if own_t is not None else "; this one never left its band)."),
                                    "redundancy")
                        elif red["verdict"] == "together":
                            _status(entry, "possible", text + ": the motion was real. A wrong reading can still cause real motion if the controller "
                                                              "acted on it; " + (f"this measurement departed first, {red['led_by_s']:g} s before the next."
                                                                                  if red["first_to_depart"] == str(lead["scope"]) and red["led_by_s"] is not None
                                                                                  else "the order of departure could not be read."), "redundancy")
                        else:
                            _status(entry, "possible", text + ": they partly agree.", "redundancy")
                elif motion_first:
                    _status(entry, "possible", f"{motion_first[0]['text']} came first; the controller could have been reacting to it.", "order")
                elif motion_after:
                    m = motion_after[0]
                    _status(entry, "unlikely", f"Measured at the {chain['points'][li]['label'].lower()}, upstream of where this mechanism acts. "
                                               f"It could only show there through the controller reacting to the motion, but the motion departed "
                                               f"{m['dt_s'] if 'dt_s' in m else round(m['t'] - lead['t'], 2):g} s AFTER it ({m['scope'] or m['signal']}).", "order")
                elif downstream_measured:
                    _status(entry, "unlikely", f"Measured upstream of where this mechanism acts, and no downstream departure came before it.", "order")
                else:
                    _status(entry, "cannot_tell", "Nothing downstream of the lead is measured, so the order cannot be read.", "order")
                mech_out.append(entry)
                continue
            # At or upstream of the lead: can produce it directly. Look for discriminating records.
            point_facts = [f for f in facts if f.get("point") == mech["acts_on"]]
            oi = idx.get("output_command")
            if oi is not None and acts <= oi < li:
                # The lead is downstream of the output commands: anything acting through the outputs must change them first.
                out_before = [f for f in facts if f["kind"] == "departure" and f.get("point") == "output_command" and f["t_rel_s"] is not None and f["t_rel_s"] <= 0.5]
                if out_before:
                    _status(entry, "possible", "Outputs changed before the motion departed: " + "; ".join(f"{f['text']} ({f['t_rel_s']:+g} s)" for f in out_before[:3]) + ".", "order")
                elif "actuator_command" in roles_present and facts_rec is not None:
                    # v1.1: not 'unlikely'. An output frozen by the controller also keeps its pre-onset level, so a steady output
                    # does not show that nothing upstream acted (ALFA elevator flight 2018-09-11-14-41-51, see DISCOVERY_CAUSAL_EVAL.md).
                    _status(entry, "possible", "No output command left its pre-onset level before the motion departed; an output frozen at its "
                                               "last value also looks like this, so this does not exclude it.", "order")
            if mech["acts_on"] == "operator":
                steady = next((f for f in point_facts if f["kind"] == "operator_steady"), None)
                vs = next((f for f in point_facts if f["kind"] == "operator_vs_output"), None)
                moved = [f for f in facts if f["kind"] == "departure" and f.get("role") == "operator_input" and f.get("near_onset")]
                before = [f for f in moved if f["t_rel_s"] <= CAUSE_LATEST_S]
                after = [f for f in moved if f["t_rel_s"] > CAUSE_LATEST_S]
                if after and not before:
                    entry["evidence"].append({"kind": "after_onset", "bearing": "none", "text": "Operator input moved only AFTER the onset (a reaction, not a cause): "
                                              + "; ".join(f["text"] + f" ({f['t_rel_s']:+g} s)" for f in after[:3])})
                    moved = []
                    steady = steady or {"text": f"No operator input moved before the onset"}
                else:
                    moved = before
                if moved:
                    _status(entry, "supported", "Operator input moved at the onset: " + "; ".join(f["text"] + f" ({f['t_rel_s']:+g} s)" for f in moved[:3]))
                elif steady:
                    _status(entry, "unlikely", steady["text"] + (f"; {vs['text']}" if vs else "") + ".")
                elif "operator_input" not in roles_present:
                    _status(entry, "cannot_tell", "The operator's inputs are not in the recording.")
            elif mech["acts_on"] == "controller":
                recorded = [f for f in point_facts if f.get("near_onset") and f["kind"] in ("state_change", "parameter_reported", "controller_text", "health_change")]
                before = [f for f in recorded if f["t_rel_s"] <= CAUSE_LATEST_S]
                events_near = [f for f in before if (f.get("chance_near_onset") or 0.0) < RARE_P]
                routine = [f for f in before if (f.get("chance_near_onset") or 0.0) >= RARE_P]
                later = [f for f in recorded if f["t_rel_s"] > CAUSE_LATEST_S]
                if routine:
                    entry["evidence"].append({"kind": "routine", "bearing": "none", "text": f"Not counted: records that change often in this recording, so one this close to the onset is expected by chance: "
                                              + "; ".join(f"{f['text']} ({f['t_rel_s']:+g} s; {f['occurrences_in_recording']} in the recording)" for f in routine[:3])
                                              + (f" and {len(routine) - 3} more" if len(routine) > 3 else "")})
                if later:
                    entry["evidence"].append({"kind": "after_onset", "bearing": "none", "text": "After the onset (a reaction or consequence, not a cause): "
                                              + "; ".join(f"{f['text']} ({f['t_rel_s']:+g} s)" for f in later[:3])})
                if events_near:
                    _status(entry, "supported", "At the onset the controller recorded: " + "; ".join(f"{f['text']} ({f['t_rel_s']:+g} s)" for f in events_near[:4]) + ".")
                elif facts_rec is not None and roles_present & {"controller_mode", "controller_text", "controller_parameters", "command_link", "controller_status"}:
                    _status(entry, "possible", "No mode, failsafe, configuration or controller message change within "
                                               f"{NEAR_S:g} s of the onset in the recording; a control-law output is not a recorded event, so this does not exclude it.")
                elif discovery and facts_rec is None:
                    _status(entry, "cannot_tell", "The recording was not read around this event (evidence discovery reads the first events of a run).")
                else:
                    _status(entry, "cannot_tell", "The controller's own mode, configuration and message records are not in the case.")
            elif mech["acts_on"] in ("actuator", "motion"):
                fb = [f for f in facts if f["kind"] == "departure" and f.get("role") in ("actuator_feedback",)]
                drive = [f for f in facts if f["point"] == "actuator" and f["kind"] == "state_change" and f.get("near_onset")
                         and f["t_rel_s"] <= CAUSE_LATEST_S and (f.get("chance_near_onset") or 0.0) < RARE_P]
                if mech["acts_on"] == "actuator" and drive:
                    _status(entry, "supported", "Before the onset the actuator reported: " + "; ".join(f"{f['text']} ({f['t_rel_s']:+g} s)" for f in drive[:3]) + ".")
                elif fb:
                    _status(entry, "possible", "Actuator feedback changed: " + "; ".join(f["text"] for f in fb[:3]))
                elif "actuator_feedback" not in roles_present:
                    _status(entry, "cannot_tell", "No actuator feedback (position, effort) is recorded, so whether the actuator did what was commanded cannot be read.")
            elif mech["acts_on"] == "link":
                if not any(a["signal"] and str(a["signal"]).startswith("network.") for a in items):
                    _status(entry, "cannot_tell", "The link between operator and machine is not measured in this case.")
            mech_out.append(entry)

        for m in mech_out:
            if m["status"] == "open":
                m["status"] = "possible"
                m["evidence"].append({"kind": "order", "text": "Can produce an anomaly at this point; nothing recorded discriminates it.", "bearing": "possible"})
            params = [f for f in facts if f["kind"] == "parameter_reported" and f.get("near_onset") and f["t_rel_s"] <= CAUSE_LATEST_S]
            if m["id"] == "controller_behaviour" and m["status"] == "supported" and params:
                names = ", ".join(sorted({f["parameter"] for f in params}))
                m["needs"] = (f"What {names} does in this firmware, and who or what set it at that moment: the ground-station log and the "
                              "flight-controller's own log (parameter, mode and event records)")
        rank = {"supported": 0, "possible": 1, "cannot_tell": 2, "unlikely": 3}
        mech_out.sort(key=lambda m: rank[m["status"]])
        remaining = [m for m in mech_out if m["status"] in ("supported", "possible", "cannot_tell")]
        supported = [m for m in mech_out if m["status"] == "supported"]
        # What would separate the mechanisms still open.
        open_needs = [{"mechanism": m["label"], "needs": m["needs"], "check_id": m.get("check_id")} for m in remaining if m.get("needs")]
        summary = (f"Measured at the {chain['points'][li]['label'].lower()}. "
                   + (f"Recorded evidence points to: {supported[0]['short']}." if supported else
                      "Nothing recorded discriminates yet.")
                   + (f" Unlikely from the recorded order or inputs: {', '.join(m['short'] for m in mech_out if m['status'] == 'unlikely')}."
                      if any(m["status"] == "unlikely" for m in mech_out) else ""))
        doms: Dict[str, List[str]] = {}
        for m in mech_out:
            if m.get("domain"):
                doms.setdefault(m["domain"], []).append(m["status"])
        points_to = sorted({m["domain"] for m in supported if m.get("domain")})
        against = sorted(d for d, sts in doms.items() if all(x == "unlikely" for x in sts))
        triage_domains = (((run.get("triage") or {}).get("proposed") or {}).get("owner") or {}).get("domains") or []
        routing = {"points_to": points_to, "argues_against": against, "triage_domains": triage_domains,
                   "differs": bool(points_to and not set(points_to) & set(triage_domains)) or bool(set(triage_domains) and set(triage_domains) <= set(against))}
        out_events.append({"routing": routing, "lead": {k: lead[k] for k in ("key", "signal", "scope", "point", "text")}, "onset": lead["t"], "onset_clock": _clock(lead["t"]),
                           "point_label": chain["points"][li]["label"], "members": [{k: m.get(k) for k in ("key", "point", "text", "relation", "dt_s", "note")} for m in members],
                           "timeline": timeline[:20], "mechanisms": mech_out, "summary": summary, "open": open_needs,
                           "discovery_used": facts_rec is not None})
    window_level = [{"key": a["key"], "point": a["point"], "text": a["text"],
                     "note": "Window-level comparison without an onset time: part of the same window, not counted as independent evidence"
                     if a["t"] is None else "Signal not placed in the signal chain"} for a in untimed]
    covered = {c.get("signal") for c in (run.get("findings") or {}).get("coverage") or [] if c.get("count")}
    roles_all = set((discovery or {}).get("inventory", {}).get("roles_present") or [])
    points = [{"id": p["id"], "label": p["label"],
               "in_case": bool(covered & set(p.get("signals", []))),
               "in_recording": bool(roles_all & set(p.get("roles", []))),
               "anomalies": sum(1 for a in items if a["point"] == p["id"])} for p in chain["points"]]
    return {"version": MECHANISMS_VERSION, "applicable": True, "chain": points,
            "lag_s": lag, "events": out_events, "window_level": window_level,
            "boundary": "Mechanisms are weighed against the recorded order of events and the records in the case. A status is never a confirmed cause."}
