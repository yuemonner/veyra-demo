"""Self-contained HTML investigation report (English). Every value from imported data is escaped."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

STATUS_TONE = {"delay_observed": "bad", "anomaly_observed": "warn", "no_delay_observed_in_measured_part": "ok", "not_observed": "muted",
               "ready_automatic": "ok", "ready_remote": "ok", "needs_context": "warn", "needs_data": "warn", "needs_authorization": "warn",
               "onsite_required": "muted", "answered_in_run": "muted", "not_ready": "warn", "ready_for_engineer_decision": "ok"}

CSS = """
:root{--ink:#16202b;--muted:#5b6876;--line:#d9dee4;--bg:#ffffff;--panel:#f5f7f9;--ok:#1d7a4f;--warn:#a35f00;--bad:#b42318;--accent:#1f5fa8}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
main{max-width:1100px;margin:0 auto;padding:28px 20px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:14px;margin:16px 0 6px}.sub{color:var(--muted)}.banner{background:#fff4e5;border:1px solid #f0c48a;padding:8px 12px;border-radius:6px;margin:12px 0;font-weight:600}
table{width:100%;border-collapse:collapse;margin:6px 0 12px;font-size:13px}th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{background:var(--panel);font-weight:600}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;background:var(--panel);border:1px solid var(--line)}
.ok{color:var(--ok);border-color:#9fd3b8}.warn{color:var(--warn);border-color:#f0c48a}.bad{color:var(--bad);border-color:#f2a8a2}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--panel)}
ul{margin:4px 0 8px 18px;padding:0}li{margin:2px 0}.boundary{color:var(--muted);font-size:12px}
@media print{h2{break-after:avoid}table{break-inside:auto}}
"""


def _e(value: Any) -> str:
    return escape("—" if value is None or value == "" else str(value))


def _pill(value: str) -> str:
    return f'<span class="pill {STATUS_TONE.get(value, "")}">{_e(value.replace("_", " "))}</span>'


def _tag(label: str, tone: str) -> str:
    return f'<span class="pill {tone}">{_e(label)}</span>'


def _ts(t: Optional[float]) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if t is not None else "—"


def _num(value: Any, digits: int = 1) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "—"


def _table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return '<p class="sub">None.</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_STRENGTH = {"delay_observed": 3, "anomaly_observed": 2, "not_observed": 1, "no_delay_observed_in_measured_part": 0}
_INV = {"cause_confirmed": "Cause confirmed", "cause_probable": "Probable cause (not confirmed)", "cause_unknown": "Cause still unknown", "not_reproduced": "Could not reproduce"}
_EVID = {"direct": "direct evidence", "indirect": "indirect evidence", "circumstantial": "circumstantial evidence", "judgement": "engineer's judgement"}


def _debrief(case, run, ledger, imports, label) -> str:
    """The one-screen summary at the top: what happened, where to look first, what to check next, what is known."""
    out = ["<section class='debrief'><h2 style='border-top:0;margin-top:8px'>Debrief</h2>"]
    report = next((e["payload"] for e in ledger if e["kind"] == "intervention_report"), None)
    if report:
        sym = f" · reported symptom: {_e(report.get('symptom', '').replace('_', ' '))}{' (' + _e(report['affected_function']) + ')' if report.get('affected_function') else ''}" if report.get("symptom") else ""
        out.append(f"<p><b>What was reported:</b> “{_e(report['stated_reason'])}” — {_e(report['operator'])}, {_e(report['occurred_at'][:19].replace('T', ' '))} UTC{sym}</p>")
    closure = next((e for e in reversed(ledger) if e["kind"] == "case_closure"), None)
    if closure:
        c = closure["payload"]
        what = f"{_e(label(c.get('cause_domain')))} — {_e(c.get('cause'))}" if c.get("cause") else "no cause established"
        out.append(f"<p><b>{_e(_INV.get(c.get('investigation_status') or 'cause_confirmed'))}:</b> {what}"
                   f"{' (' + _e(_EVID.get(c.get('evidence_strength'), '')) + ')' if c.get('evidence_strength') else ''} · machine: {_e((c.get('resolution_status') or '—').replace('_', ' '))}"
                   f" · closed by {_e(closure['actor'])}, {_e(closure['recorded_at'][:10])}</p>")
    flagged = sorted(((d, a) for d, a in run["domains"].items() if a["status"] in ("delay_observed", "anomaly_observed")),
                     key=lambda kv: (-_STRENGTH.get(kv[1]["status"], 0), -len(kv[1]["observations"])))
    if flagged:
        d, a = flagged[0]
        order = ["R-OUTPUT-STUCK", "R-OUTPUT-MIRROR", "R-TRACKING", "R-SEGMENT-RELATIVE", "R-SEGMENT", "R-IO", "R-REFERENCE", "R-BRIEF", "R-SCREEN", "R-EVENT", "R-HEARTBEAT"]
        rank = lambda o: next((i for i, r in enumerate(order) if o["rule"].startswith(r)), 99)  # noqa: E731  strongest kind of evidence first
        obs = sorted((o for o in a["observations"] if o.get("kind") in ("anomaly", "delay")), key=rank)[:3]
        out.append(f"<p><b>Look here first:</b> {_e(label(d))}</p><ul>" + "".join(f"<li>{_e(o['text'])}</li>" for o in obs) + "</ul>")
        if len(flagged) > 1:
            out.append("<p><b>Also flagged:</b> " + "; ".join(f"{_e(label(x))} ({len([o for o in y['observations'] if o.get('kind') in ('anomaly', 'delay')])})" for x, y in flagged[1:]) + "</p>")
    else:
        out.append("<p><b>Look here first:</b> nothing in the recorded data departs from the known-good reference.</p>")
    quiet = [label(d) for d, a in run["domains"].items() if a["status"] == "no_delay_observed_in_measured_part"]
    if quiet:
        out.append(f"<p><b>Nothing found in the measured part of:</b> {_e(', '.join(quiet))}</p>")
    checks = [c for c in run["checks"] if c["availability"]["state"] not in ("answered_in_run", "not_applicable")][:3]
    if checks:
        out.append("<p><b>Next checks:</b></p><ol>" + "".join(f"<li>{_e(c['question'])} <span class='sub'>({_e(c['who'])}; {_e(c['availability']['state'].replace('_', ' '))})</span></li>" for c in checks) + "</ol>")
    for m in (run.get("precedents") or {}).get("matches", [])[:2]:
        cl = m.get("closure") or {}
        basis = "same reported symptom, different evidence" if m.get("match_basis") == "reported symptom only" else f"{round(m['similarity'] * 100)}% similar findings"
        out.append(f"<p><b>Similar earlier case:</b> {_e(m['title'])} ({_e(basis)}) — then: {_e(_INV.get(cl.get('investigation_status'), ''))}"
                   f"{', ' + _e(cl.get('cause')) if cl.get('cause') else ''}. <span class='sub'>A lead, not a diagnosis: its cause is not applied here.</span></p>")
    if run.get("reference", {}).get("status") == "declared" or any(e["kind"] == "reference_declared" for e in ledger):
        ref = next(e["payload"] for e in reversed(ledger) if e["kind"] == "reference_declared")
        out.append(f"<p class='sub'>Compared against a known-good period {_e(ref['window_from'][:19].replace('T', ' '))} → {_e(ref['window_to'][:19].replace('T', ' '))} UTC: “{_e(ref['basis'])}” ({_e(next(e['actor'] for e in reversed(ledger) if e['kind'] == 'reference_declared'))}).</p>")
    out.append("<p class='sub'>Root cause stays <b>unconfirmed</b> until a person confirms it. Every finding below links to its rule and data.</p></section>")
    return "".join(out)


def render(store, case_id: str, authenticated: bool = False) -> str:
    case = store.case(case_id)
    run = store.latest_run(case_id)
    imports = store.imports(case_id, include_rows=False)
    ledger = store.ledger(case_id)
    verify = store.verify(case_id)
    parts: List[str] = []
    parts.append(f"<h1>{_e(case['title'])}</h1><div class='sub'>Case <span class='mono'>{_e(case['id'])}</span> · machine <b>{_e(case['machine_id'])}</b> · "
                 f"{'known-good run · ' if case['fault_type'] == 'known_good_run' else ''}opened {_e(case['created_at'][:19])} by {_e(case['created_by'])}</div>")
    if case["synthetic"]:
        parts.append("<div class='banner'>SYNTHETIC DATA — generated for testing. not private data and not field evidence.</div>")
    window = f"{_ts(case['symptom_start'])} → {_ts(case['symptom_end'])} (source: {_e(case['symptom_source'])})" if case["symptom_start"] else "No symptom window recorded."
    parts.append(f"<p><b>Symptom window:</b> {window}</p>")

    if run is None:
        parts.append("<p>No investigation run yet.</p>")
    else:
        names = {k: v.get("label", k) for k, v in ((run.get("machine_profile") or {}).get("domains") or {}).items()}
        label = lambda d: names.get(d, (d or "—").replace("_", " "))  # noqa: E731
        parts.append(_debrief(case, run, ledger, imports, label))
        parts.append(f"<p class='sub'>Latest run <span class='mono'>{_e(run['id'])}</span> · {_e(run['created_at'][:19])} by {_e(run['actor'])} · goal {_e(run['goal'])} · "
                     f"engine {_e(run['versions']['engine'])} · run hash <span class='mono'>{_e(run['run_hash'][:16])}…</span> · external model calls: 0</p>")
        parts.append("<h2>Fault domains</h2><p class='sub'>Observations from executed calculations. A domain status is not a root cause; root cause: <b>unconfirmed</b>.</p><div class='grid'>")
        for name, d in run["domains"].items():
            items = "".join(f"<li><span class='mono'>{_e(o['rule'])}</span> {_e(o['text'])}</li>" for o in d["observations"]) or "<li class='sub'>No relevant records.</li>"
            parts.append(f"<div class='card'><h3>{_e(label(name))} {_pill(d['status'])}</h3><div class='sub'>{_e(d['meaning'])}</div><ul>{items}</ul></div>")
        parts.append("</div>")
        for o in run.get("unscoped_observations", []):
            parts.append(f"<p class='sub'>Unscoped: {_e(o['text'])}</p>")

        since: Dict[str, str] = {}
        for e in ledger:
            if e["kind"] in ("check_review", "check_execution") and e["payload"].get("run_id") == run["id"]:
                p = e["payload"]
                label = f"review: {p['verdict']}" if e["kind"] == "check_review" else f"execution: {p['status'].replace('_', ' ')}"
                since[p["check_id"]] = (since.get(p["check_id"], "") + "; " if p["check_id"] in since else "") + label
        parts.append("<h2>Next checks</h2><p class='sub'>Ordered by documented ordinal rules (" + _e(run["versions"]["check_policy"]) + "); no probabilities or calibrated information gain. Cost and duration unknown unless recorded.</p>")
        rows = []
        for c in run["checks"]:
            done = f"<br><span class='sub'>performed earlier: {_e(c['previously_performed']['entry_id'])}</span>" if c["previously_performed"] else ""
            rows.append([_e(c["rank"]), f"<b>{_e(c['check_id'])}</b><br>{_e(c['question'])}{done}", _pill(c["availability"]["state"]) + f"<br><span class='sub'>{_e(c['availability']['detail'])}</span>",
                         _e(c["trigger"] or "generic"), f"{_e(c['who'])}<br><span class='sub'>{_e(', '.join(c['tools']))}</span>", _e(c["disruption"]),
                         _e(since.get(c["check_id"], "—")), f"<span class='sub'>{_e(c['rank_explanation'])}</span>"])
        parts.append(_table(["#", "Check", "Availability", "Why now", "Who / tools", "Disruption", "Since this run", "Ranking basis"], rows))

        d = run["dispatch"]
        parts.append(f"<h2>Dispatch readiness {_pill(d['status'])}</h2><p class='boundary'>{_e(d['boundary'])}</p>")
        def blocker_since(item: str) -> str:
            check = next((cid for cid in since if cid in item), None)
            return _e(since[check]) if check else "—"
        parts.append("<h3>Blockers before dispatch (as of this run)</h3>" + _table(["Item", "Status at run", "Since this run"],
                                                                 [[_e(b["item"]), _e(b["status"]), blocker_since(b["item"])] for b in d["blockers"]]))
        parts.append("<h3>Candidate preparation (to confirm)</h3>" + _table(["Item", "Basis"], [[_e(t["item"]), _e(t["basis"])] for t in d["candidate_preparation"]]))
        parts.append(f"<p><b>Parts:</b> {_e(d['parts']['status'])} — {_e(d['parts']['basis'])}</p>")

        f = run["findings"]
        parts.append("<h2>Findings</h2>")
        chain = f.get("command_chain") or {}
        if chain.get("segments"):
            parts.append("<h3>Command chain (same command IDs)</h3>")
            rows = []
            for key, s in chain["segments"].items():
                if "relative_change_ms" in s:
                    detail = f"Relative change {_num(s['relative_change_ms'], 0)} ms ({s['commands_inside']} inside / {s['commands_outside']} outside). Assumption: {_e(s['assumption'])}"
                elif "inside_median_ms" in s:
                    detail = f"Median {_num(s['inside_median_ms'], 0)} ms inside vs {_num(s['outside_median_ms'], 0)} ms outside (p95 outside {_num(s['outside_p95_ms'], 0)} ms); {s['commands_inside']}/{s['commands_outside']} commands"
                else:
                    detail = _e(s.get("detail"))
                rows.append([_e(key), _e(s["domain"]), _pill(s["status"]), detail])
            parts.append(_table(["Segment", "Domain", "Result", "Detail"], rows) + f"<p class='boundary'>{_e(chain.get('window_note'))} {_e(chain.get('boundary'))}</p>")
        io = f.get("input_response") or {}
        if io.get("channels"):
            parts.append("<h3>Input–Response Baseline (optional module)</h3>")
            rows = []
            for ch in io["channels"]:
                def model(m):
                    if not m or m.get("status") != "identified":
                        return _e((m or {}).get("status")) + (f"<br><span class='sub'>{_e(m.get('reason'))}</span>" if m and m.get("reason") else "")
                    x = m["model"]
                    return f"delay {_num(x['delay_s']*1000, 0)} ms (±{_num(x['delay_resolution_s']*1000, 0)}), gain {_num(x['gain'], 3)} deg/s per %, r {_num(x['correlation'], 3)}"
                obs = "".join(f"<li>{_e(o['detail'])}</li>" for o in ch.get("observations", [])) or _e(ch.get("reason"))
                rows.append([_e(ch["channel"]), _pill(ch["status"]), f"{_e(' → '.join(ch.get('baseline_window', [])))}<br>{model(ch.get('baseline'))}",
                             f"{_e(' → '.join(ch.get('incident_window', [])))}<br>{model(ch.get('incident'))}", f"<ul>{obs}</ul>" if ch.get("observations") else obs])
            parts.append(_table(["Channel", "Result", "Baseline window / model", "Incident window / model", "Observations"], rows) + f"<p class='boundary'>{_e(io.get('threshold_provenance'))}</p>")
        trk = f.get("tracking") or {}
        if trk.get("findings"):
            parts.append("<h3>Commanded vs measured</h3><ul>" + "".join(f"<li>{_e(x.get('text'))}</li>" for x in trk["findings"][:12]) + "</ul>")

        parts.append("<h3>Measurement screening</h3>")
        rows = []
        for m in f.get("measurements") or []:
            rule = f"&gt; {_e(m['threshold']['above'])} {_e(m['threshold']['unit'])}<br><span class='sub'>{_e(m['threshold']['provenance'])}</span>" if m.get("threshold") else "no rule"
            rows.append([_e(m["signal"]), _e(m["scope"]), _e(m["unit"]), _e(m["clock_domain"]), f"max {_num(m['in_window'].get('max'))}, median {_num(m['in_window'].get('median'))} ({m['in_window'].get('count', 0)})",
                         f"max {_num((m.get('outside_window') or {}).get('max'))}, median {_num((m.get('outside_window') or {}).get('median'))}", rule, (_tag("crossing in window", "warn") if m["crossing_in_window"] else _tag("no crossing in window", "ok"))])
        parts.append(_table(["Signal", "Path / channel", "Unit", "Clock", "In window", "Outside window", "Screening rule", "Result"], rows))

        parts.append("<h3>Heartbeats and events near the window</h3>")
        for hb in f.get("heartbeats") or []:
            gaps = ", ".join(f"{_ts(g['start'])} (+{g['duration_s']:.0f} s)" for g in hb.get("gaps", [])) or "none"
            parts.append(f"<p>Heartbeats ({_e(hb['clock_domain'])}): {_e(hb.get('rule') or hb.get('reason'))}; gaps: {_e(gaps)}</p>")
        events = (f.get("events") or {}).get("events", [])
        parts.append(_table(["Time", "Signal", "Value / code", "Description", "Source system", "Clock"],
                            [[_e(e["time_utc"]), _e(e["signal"]), _e(e["event_value"] or e["code"]), _e(e["code_description"]), _e(e["code_source"]), _e(e["clock_domain"])] for e in events]))

        ctx = run["context"]["incident"]
        parts.append(f"<h2>Machine context at the incident</h2><p class='sub'>Effective {_e(ctx['effective_at'])}, as known at {_e(ctx['known_at'])} · schema {_e(ctx['schema'])} · hash <span class='mono'>{_e(ctx['hash'][:16])}…</span></p>")
        rows = []
        for key, a in ctx["attributes"].items():
            source = a.get("confirmation_source") or a.get("source")
            conflict = f"<br><b class='bad'>conflict:</b> {_e(', '.join(a['conflicting_values']))}" if a.get("conflicting_values") else ""
            rows.append([_e(key), _e(a.get("value")), _pill(a["status"]) + conflict, _e(source), _e((a.get("effective_from") or "")[:19])])
        parts.append(_table(["Attribute", "Value", "Status", "Source", "Effective from"], rows))
        changes = run["context"].get("changes_since_incident") or []
        if changes:
            parts.append("<h3>Context recorded since the incident</h3>" + _table(["Attribute", "At incident", "Now"], [[_e(c["key"]), _e(c["first"]), _e(c["second"])] for c in changes]))

        if run["missing_information"]:
            parts.append("<h2>Missing information</h2><ul>" + "".join(f"<li>{_e(m)}</li>" for m in run["missing_information"]) + "</ul>")

    parts.append("<h2>Evidence</h2>")
    parts.append(_table(["Import", "File", "Status", "Clock", "Rows", "SHA-256", "Integrity"],
                        [[f"<span class='mono'>{_e(i['id'])}</span>", _e(i["filename"]) + (f"<br><span class='sub'>{_e(i.get('review_reason'))}</span>" if i.get("review_reason") else ""),
                          _e(i["status"]), _e(i["clock_domain"]), _e(i["report"].get("accepted_row_count")),
                          f"<span class='mono'>{_e(i['sha256'][:20])}…</span>", (_tag("verified", "ok") if i["integrity_ok"] else _tag("FAILED", "bad"))] for i in imports]))

    parts.append("<h2>Intervention ledger</h2>")
    chain = verify["ledger_chain"]
    parts.append(f"<p>Hash chain: <b class='{'ok' if chain['valid'] else 'bad'}'>{'valid' if chain['valid'] else 'BROKEN'}</b> · {len(ledger)} entries · imports intact: {_e(verify['imports_intact'])} · runs intact: {_e(verify['runs_intact'])}</p>")
    rows = []
    for e in ledger:
        p = e["payload"]
        summary = {"investigation_run": lambda: f"Run {p['run_id']} · dispatch {p.get('dispatch_status')}",
                   "check_review": lambda: f"{p['check_id']}: {p['verdict']} — {p['reason']}",
                   "check_execution": lambda: f"{p['check_id']}: {p['status']}" + (f" — {p.get('observations')}" if p.get("observations") else ""),
                   "decision": lambda: f"Chose: {p['chosen_option']} — {p['rationale']}",
                   "dispatch_preparation": lambda: f"Remote resolution {p['remote_resolution']}; blockers {len(p['blockers'])}",
                   "decision_proposal": lambda: f"Proposed: {p['action'].replace('_', ' ')} ({p['purpose'].replace('_', ' ')})" + (f", part {p['target_part']}" if p.get("target_part") else ""),
                   "decision_check": lambda: f"Decision check: {p.get('alert')} · coverage {p.get('coverage', {}).get('status')} · {p.get('headline')}",
                   "next_action": lambda: (f"Proposed checks: {', '.join(c['check_id'] for c in p.get('checks', []))}" if p.get("stage") == "plan"
                                           else f"Dispatch readiness: {p.get('headline')}"),
                   "action": lambda: f"{p['action_type']} ({p['status']}): {p['description']}",
                   "outcome": lambda: f"{p['result']} ({p['verification']}){' — SIMULATED' if p.get('simulated') else ''}: {p['observations']}",
                   "outcome_review": lambda: f"{p['verdict']} — {p['reason']}",
                   "recurrence_check": lambda: f"recurred: {p['recurred']}",
                   "support_milestone": lambda: f"{p['milestone']} at {p['observed_at'][:19]}"}.get(e["kind"], lambda: "")()
        rows.append([_e(e["recorded_at"][:19]), _e(e["kind"]), _e(e["actor"]), _e(summary), f"<span class='mono'>{_e(e['hash'][:10])}</span>"])
    parts.append(_table(["Recorded", "Entry", "Actor", "Summary", "Hash"], rows))
    who = ("People signed in to this private instance; every record carries the signed-in person's name. " if authenticated
           else "Identities are not authenticated in the local tool. ")
    parts.append("<p class='boundary'>Human decisions, actual actions, observations and verification are separate records. " + who +
                 "Nothing in this report authorizes dispatch or actuation. Generated by Veyra (deterministic engine, no external model calls).</p>")
    title = escape(f"Veyra · {case['title']}")
    css = CSS + ".debrief{border:1px solid var(--line);border-radius:10px;padding:4px 16px 8px;background:#fbfcfd;margin-top:14px}@media print{.debrief{break-inside:avoid}}"
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>{css}</style></head><body><main>{''.join(parts)}</main></body></html>"
