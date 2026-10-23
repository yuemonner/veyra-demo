"""veyra — Forward Deployed Engineer command line for the Veyra Investigation Engine.

Runs the full loop on a laptop with Python 3.9+ and no other dependencies:
import -> machine context -> investigate -> review/execute checks -> decision -> action -> outcome -> report/export.
All data stays in a local workspace (one SQLite file). Nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.context import model as context_model
from app.context.model import ContextError
from app.workflows import report as report_html
from app.ingest.profile import ProfileError, parse_profile
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore, StoreError

RUN_KINDS = ("check_execution", "decision", "dispatch_preparation")
LIST_FIELDS = ("options_considered", "evidence", "prepared", "blockers")


def _store(args) -> CaseStore:
    workspace = Path(args.workspace or os.environ.get("VEYRA_WORKSPACE") or "veyra-workspace")
    return CaseStore(workspace / "veyra.db")


def _emit(args, data: Any, text: str) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False) if args.json else text)


def _ts(t: Optional[float]) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if t is not None else "—"


def _case_line(c: Dict[str, Any]) -> str:
    window = f"{_ts(c['symptom_start'])} → {_ts(c['symptom_end'])}" if c["symptom_start"] else "no symptom window"
    return f"{c['id']}  {c['machine_id']}  {c['title']}{'  [SYNTHETIC]' if c['synthetic'] else ''}  ({window})"


def _run_text(run: Dict[str, Any]) -> str:
    lines = [f"Run {run['id']}  goal: {run['goal']}  root cause: {run['root_cause']}{'  [SYNTHETIC]' if run['synthetic'] else ''}", "", "Fault domains (observations, not causes):"]
    for name, d in run["domains"].items():
        lines.append(f"  {name:<18} {d['status']}")
        for o in d["observations"][:6]:
            lines.append(f"      - [{o['rule']}] {o['text']}")
    for o in run.get("unscoped_observations", []):
        lines.append(f"  unscoped: {o['text']}")
    lines += ["", "Next checks (ordinal policy " + run["versions"]["check_policy"] + "):"]
    for c in run["checks"]:
        lines.append(f"  {c['rank']:>2}. {c['check_id']:<20} {c['availability']['state']:<20} {c['question']}")
        if c["trigger"]:
            lines.append(f"      why now: {c['trigger']}")
    lines += ["", f"Dispatch readiness: {run['dispatch']['status']}"]
    for b in run["dispatch"]["blockers"][:10]:
        lines.append(f"  - {b['item']} ({b['status']})")
    if run["missing_information"]:
        lines += ["", "Missing information:"] + [f"  - {m}" for m in run["missing_information"][:12]]
    return "\n".join(lines)


def cmd_case(args) -> int:
    store = _store(args)
    if args.action == "create":
        case = store.create_case(machine_id=args.machine, title=args.title, actor=args.actor, synthetic=args.synthetic,
                                 symptom_start=args.symptom_start, symptom_end=args.symptom_end, symptom_source=args.symptom_source)
        _emit(args, case, "Created " + _case_line(case))
    elif args.action == "list":
        cases = store.cases()
        _emit(args, cases, "\n".join(_case_line(c) for c in cases) or "No cases yet.")
    else:
        case = store.case(args.case_id)
        imports = store.imports(case["id"], include_rows=False)
        runs = store.runs(case["id"])
        data = {"case": case, "imports": imports, "runs": [{"id": r["id"], "created_at": r["created_at"], "run_hash": r["run_hash"]} for r in runs],
                "ledger_entries": len(store.ledger(case["id"]))}
        text = [_case_line(case), "", "Imports:"] + [f"  {i['id']}  {i['filename']}  {i['status']}  {i['clock_domain']}  sha256 {i['sha256'][:16]}…" for i in imports]
        text += ["", "Runs:"] + [f"  {r['id']}  {r['created_at']}" for r in runs] + ["", f"Ledger entries: {data['ledger_entries']}"]
        _emit(args, data, "\n".join(text))
    return 0


def cmd_import(args) -> int:
    store = _store(args)
    profile = parse_profile(json.loads(Path(args.profile).read_text(encoding="utf-8")))
    raw = Path(args.file).read_bytes()
    name = Path(args.file).name
    if args.preview:
        result = store.preview_import(args.case_id, raw, name, profile)
        report = result.to_dict(include_rows=False)
        _emit(args, report, f"PREVIEW (nothing stored) {name}: {result.status}, {len(result.rows)} accepted, {result.rejected_row_count} rejected"
              + "".join(f"\n  error: {e}" for e in result.file_errors) + "".join(f"\n  row {r['row']}: {'; '.join(r['reasons'])}" for r in result.rejected_rows[:10])
              + "".join(f"\n  missing: {m}" for m in result.missing_information))
        return 0
    record, result = store.commit_import(args.case_id, raw, name, profile, actor=args.actor, review_reason=args.review_reason)
    _emit(args, record, f"Stored {record['id']} {name}: {record['status']} ({len(result.rows)} rows, clock {record['clock_domain']}).\n"
          "Reported context from the profile was recorded as 'reported' (not confirmed).")
    return 0


def cmd_context(args) -> int:
    store = _store(args)
    if args.action == "set":
        body = {"machine_id": args.machine, "key": args.key, "status": args.status, "source": args.source, "actor": args.actor,
                "effective_from": args.effective_from, "evidence": args.evidence or [], "note": args.note or ""}
        if args.status not in (context_model.UNKNOWN, context_model.RETRACTED):
            body["value"] = args.value
        assertion = store.assert_context(body)
        _emit(args, assertion, f"Recorded {assertion['key']} = {assertion['value']!r} ({assertion['status']}) effective {assertion['effective_from']}")
    elif args.action == "schema":
        schema = context_model.describe()
        _emit(args, schema, "\n".join(f"{a['key']:<24} {a['category']:<12} {a['description']}" + (f"  allowed: {', '.join(a['allowed'])}" if a["allowed"] else "")
                                      for a in schema["attributes"]))
    else:
        snapshot = store.context(args.machine, at=args.at, known_at=args.known_at)
        lines = [f"Machine {args.machine} context (effective {snapshot['effective_at']}, known {snapshot['known_at']}), hash {snapshot['hash'][:16]}…"]
        for key, a in snapshot["attributes"].items():
            flag = "  CONFLICT: " + ", ".join(a["conflicting_values"]) if a.get("conflicting_values") else ""
            src = f"  [{a.get('confirmation_source') or a.get('source')}]" if a["status"] != "not_recorded" else ""
            lines.append(f"  {key:<24} {str(a['value'] or '—'):<36} {a['status']}{src}{flag}")
        _emit(args, snapshot, "\n".join(lines))
    return 0


def cmd_investigate(args) -> int:
    store = _store(args)
    baseline = (args.baseline_start, args.baseline_end) if args.baseline_start or args.baseline_end else None
    if baseline and not all(baseline):
        raise StoreError("Give both --baseline-start and --baseline-end")
    run = store.investigate(args.case_id, actor=args.actor, goal=args.goal, baseline_window=baseline)
    _emit(args, run, _run_text(run))
    return 0


def cmd_checks(args) -> int:
    store = _store(args)
    run = store.latest_run(args.case_id)
    if run is None:
        raise StoreError("No run yet; use 'veyra investigate'")
    if args.json:
        _emit(args, run["checks"], "")
        return 0
    lines = []
    for c in run["checks"]:
        lines += [f"{c['rank']:>2}. {c['check_id']} — {c['question']}", f"    availability: {c['availability']['state']} ({c['availability']['detail']})",
                  f"    who/tools: {c['who']} / {', '.join(c['tools'])}; disruption {c['disruption']}; cost/duration unknown",
                  f"    why: {c['why']}", f"    rank: {c['rank_explanation']}"]
        if c["previously_performed"]:
            lines.append(f"    previously performed: {c['previously_performed']['entry_id']}")
    print("\n".join(lines))
    return 0


def cmd_review(args) -> int:
    store = _store(args)
    run = store.latest_run(args.case_id)
    if run is None:
        raise StoreError("No run yet")
    payload = {"run_id": run["id"], "check_id": args.check_id, "verdict": args.verdict, "reason": args.reason}
    if args.edited_procedure:
        payload["edited_procedure"] = args.edited_procedure
    entry = store.append(args.case_id, "check_review", payload, actor=args.actor, source=args.source)
    _emit(args, entry, f"Recorded {args.verdict} for {args.check_id} on {run['id']} ({entry['id']})")
    return 0


def cmd_execute(args) -> int:
    store = _store(args)
    out = store.execute_automatic(args.case_id, args.check_id, actor=args.actor)
    result = out["result"]
    if args.json:
        _emit(args, out, "")
        return 0
    lines = [f"Executed {args.check_id} read-only ({out['entry']['id']})."]
    if result["tool"] == "window_rows":
        lines.append(f"{result['count']} records in the symptom window: " + ", ".join(f"{k} {v}" for k, v in sorted(result["by_signal"].items())))
        for row in result["rows"][: args.limit]:
            lines.append(f"  {row['time_utc']}  {row['filename']}#{row['row']}  {row['signal']}  {row['value'] if row['value'] is not None else (row['event_value'] or row['code'] or row['command_id'] or '')}")
    else:
        lines.append("Answered in the run; see 'veyra report'.")
    print("\n".join(lines))
    return 0


def _parse_fields(args) -> Dict[str, Any]:
    payload: Dict[str, Any] = json.loads(Path(args.json_file).read_text(encoding="utf-8")) if args.json_file else {}
    for item in args.set or []:
        if "=" not in item:
            raise StoreError(f"--set needs key=value, got '{item}'")
        key, value = item.split("=", 1)
        payload[key.strip()] = value
    for item in args.add or []:
        if "=" not in item:
            raise StoreError(f"--add needs key=value, got '{item}'")
        key, value = item.split("=", 1)
        if key.strip() not in LIST_FIELDS:
            raise StoreError(f"--add is for list fields: {', '.join(LIST_FIELDS)}")
        payload.setdefault(key.strip(), []).append(value)
    return payload


def cmd_record(args) -> int:
    store = _store(args)
    payload = _parse_fields(args)
    if args.kind in RUN_KINDS and "run_id" not in payload:
        run = store.latest_run(args.case_id)
        if run is None:
            raise StoreError("No run yet")
        payload["run_id"] = run["id"]
    entry = store.append(args.case_id, args.kind, payload, actor=args.actor, source=args.source)
    _emit(args, entry, f"Recorded {args.kind} {entry['id']} (hash {entry['hash'][:12]}…)")
    return 0


def cmd_report(args) -> int:
    store = _store(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_html.render(store, args.case_id), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_ledger(args) -> int:
    store = _store(args)
    if args.verify:
        result = store.verify(args.case_id)
        _emit(args, result, f"Ledger chain: {'valid' if result['ledger_chain']['valid'] else 'BROKEN ' + str(result['ledger_chain'])}; "
                            f"imports intact: {result['imports_intact']}; runs intact: {result['runs_intact']}")
        return 0 if result["ledger_chain"]["valid"] and result["imports_intact"] and result["runs_intact"] else 4
    if args.export:
        episodes = store.episodes(args.case_id)
        with open(args.export, "w", encoding="utf-8") as handle:
            for episode in episodes:
                handle.write(json.dumps(episode, default=str, ensure_ascii=False) + "\n")
        print(f"Exported {len(episodes)} episode(s) to {args.export} (decision-time and hindsight separated)")
        return 0
    entries = store.ledger(args.case_id)
    _emit(args, entries, "\n".join(f"{e['recorded_at'][:19]}  {e['kind']:<20} {e['actor']:<16} {e['id']}" for e in entries))
    return 0


def cmd_demo(args) -> int:
    from app.workflows.demo import run_demo
    return run_demo(_store(args), Path(args.out_dir or (Path(args.workspace or "veyra-workspace") / "demo")), verbose=not args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="veyra", description="Veyra Investigation Engine for field engineers (local, offline).")
    parser.add_argument("--workspace", help="Workspace folder (default: $VEYRA_WORKSPACE or ./veyra-workspace)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    case = sub.add_parser("case", help="Create, list or show cases")
    case_sub = case.add_subparsers(dest="action", required=True)
    create = case_sub.add_parser("create")
    create.add_argument("--machine", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--actor", required=True)
    create.add_argument("--synthetic", action="store_true", help="Case holds synthetic data only")
    create.add_argument("--symptom-start")
    create.add_argument("--symptom-end")
    create.add_argument("--symptom-source", help="Who reported the symptom window, and how")
    case_sub.add_parser("list")
    show = case_sub.add_parser("show")
    show.add_argument("case_id")
    case.set_defaults(func=cmd_case)

    imp = sub.add_parser("import", help="Validate (--preview) or store an authorized export with an import profile")
    imp.add_argument("case_id")
    imp.add_argument("file")
    imp.add_argument("--profile", required=True)
    imp.add_argument("--actor", required=True)
    imp.add_argument("--preview", action="store_true")
    imp.add_argument("--review-reason")
    imp.set_defaults(func=cmd_import)

    ctx = sub.add_parser("context", help="Machine context: set, show, schema")
    ctx_sub = ctx.add_subparsers(dest="action", required=True)
    cset = ctx_sub.add_parser("set")
    cset.add_argument("machine")
    cset.add_argument("--key", required=True)
    cset.add_argument("--value")
    cset.add_argument("--status", default="reported", choices=context_model.STATUSES)
    cset.add_argument("--source", required=True, help="Where this fact comes from (document, photo, system, person)")
    cset.add_argument("--actor", required=True)
    cset.add_argument("--effective-from", required=True, help="When this became true (ISO 8601 with timezone)")
    cset.add_argument("--evidence", action="append")
    cset.add_argument("--note")
    cshow = ctx_sub.add_parser("show")
    cshow.add_argument("machine")
    cshow.add_argument("--at", help="Effective time (default now)")
    cshow.add_argument("--known-at", help="Only what was recorded by this time (replay)")
    ctx_sub.add_parser("schema")
    ctx.set_defaults(func=cmd_context)

    inv = sub.add_parser("investigate", help="Run the investigation engine on the case's imports")
    inv.add_argument("case_id")
    inv.add_argument("--actor", required=True)
    inv.add_argument("--goal", default="reduce_uncertainty", choices=("reduce_uncertainty", "restore_operations"))
    inv.add_argument("--baseline-start")
    inv.add_argument("--baseline-end")
    inv.set_defaults(func=cmd_investigate)

    chk = sub.add_parser("checks", help="Show ranked next checks of the latest run")
    chk.add_argument("case_id")
    chk.set_defaults(func=cmd_checks)

    rev = sub.add_parser("review", help="Accept, reject or edit a proposed check (reason required)")
    rev.add_argument("case_id")
    rev.add_argument("check_id")
    rev.add_argument("--verdict", required=True, choices=("accepted", "rejected", "edited"))
    rev.add_argument("--reason", required=True)
    rev.add_argument("--edited-procedure")
    rev.add_argument("--actor", required=True)
    rev.add_argument("--source", default="veyra-cli")
    rev.set_defaults(func=cmd_review)

    ex = sub.add_parser("execute", help="Run an accepted automatic read-only check")
    ex.add_argument("case_id")
    ex.add_argument("check_id")
    ex.add_argument("--actor", required=True)
    ex.add_argument("--limit", type=int, default=15)
    ex.set_defaults(func=cmd_execute)

    rec = sub.add_parser("record", help="Append a ledger entry: check_execution, decision, dispatch_preparation, action, outcome, outcome_review, recurrence_check, support_milestone")
    rec.add_argument("case_id")
    rec.add_argument("kind", choices=("check_execution", "decision", "dispatch_preparation", "action", "outcome", "outcome_review", "recurrence_check", "support_milestone"))
    rec.add_argument("--actor", required=True)
    rec.add_argument("--source", required=True, help="Where this record comes from (ticket, call, log, site report)")
    rec.add_argument("--set", action="append", help="field=value")
    rec.add_argument("--add", action="append", help="list field=value (options_considered, evidence, prepared, blockers)")
    rec.add_argument("--json-file", help="Payload as a JSON file")
    rec.set_defaults(func=cmd_record)

    rep = sub.add_parser("report", help="Write a self-contained HTML investigation report")
    rep.add_argument("case_id")
    rep.add_argument("--out", required=True)
    rep.set_defaults(func=cmd_report)

    led = sub.add_parser("ledger", help="List, verify or export the intervention ledger")
    led.add_argument("case_id")
    led.add_argument("--verify", action="store_true")
    led.add_argument("--export", help="Write decision episodes as JSON Lines")
    led.set_defaults(func=cmd_ledger)

    demo = sub.add_parser("demo", help="Run the full loop on the SYNTHETIC remote-delay fixtures")
    demo.add_argument("--out-dir")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (StoreError, LedgerError, ContextError, ProfileError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
