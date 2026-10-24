"""Upload a recording -> a case with a first investigation (the debrief), in one step.

Every step is the ordinary validated path: the case, the import (with its fingerprint and import report), the
known-good reference (labelled with who declared it and on what basis), the operator's report, the investigation.
Nothing is decided here: the debrief is the engine's first run, and the Brief shows it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.ingest import bags, catalog, ulog
from app.ingest.profile import parse_profile


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat().replace("+00:00", "Z")


def debrief_recording(store, path: Path, mapping: Dict[str, Any], *, machine_id: str, title: str, actor: str,
                      machine_class: Optional[str] = None, problem_from_s: Optional[float] = None, problem_to_s: Optional[float] = None,
                      normal_from_s: Optional[float] = None, normal_to_s: Optional[float] = None, normal_basis: Optional[str] = None,
                      report: Optional[Dict[str, Any]] = None, source_name: Optional[str] = None, synthetic: bool = False,
                      progress: Callable[[str], None] = lambda step: None) -> Dict[str, Any]:
    """Seconds are measured from the start of the recording. Returns {case_id, run_id, imports, counts}."""
    path = Path(path)
    progress("Fingerprinting the recording")
    sha = bags.sha256_file(path)
    progress("Reading the recording")
    if ulog.is_ulog(path):  # PX4 flight log: a fixed, documented topic mapping (app/ingest/ulog.py)
        extraction, meta = ulog.extract(path, asset=machine_id)
        clean = ulog.mapping(clock_domain="px4_clock" if meta["clock"] == "gps_utc" else "px4_boot_clock")
    else:
        clean = bags.validate_mapping(mapping, [s["id"] for s in catalog.describe()["signals"]])
        extraction = bags.extract(path, clean, asset=machine_id, progress=lambda n: progress(f"Reading the recording ({n:,} messages)"))
    if not extraction.rows:
        raise ValueError("The mapping produced no readings from this recording")
    start, end = extraction.start_ns, extraction.end_ns
    span = (end - start) / 1e9

    def at(seconds: float) -> str:
        if not 0 <= seconds <= span + 1e-6:
            raise ValueError(f"{seconds:g} s is outside the recording (0 to {span:.1f} s)")
        return _iso(start + int(seconds * 1e9))

    if machine_class:
        current = ((store.context(machine_id).get("attributes") or {}).get("machine_class") or {}).get("value")
        if current != machine_class:
            store.assert_context({"machine_id": machine_id, "key": "machine_class", "value": machine_class, "status": "reported", "actor": actor,
                                  "source": f"Given when uploading {source_name or path.name}", "effective_from": _iso(start)})
    symptom = {}
    if problem_from_s is not None and problem_to_s is not None:
        if problem_to_s <= problem_from_s:
            raise ValueError("The problem must end after it starts")
        symptom = {"symptom_start": at(problem_from_s), "symptom_end": at(problem_to_s),
                   "symptom_source": (report or {}).get("stated_reason") or f"Given when uploading {source_name or path.name}"}
    progress("Creating the case")
    case = store.create_case(machine_id=machine_id, title=title, actor=actor, synthetic=synthetic, **symptom)
    cid = case["id"]
    profile = parse_profile({**bags.import_profile(clean, source_name or path.name, sha), "synthetic": synthetic})
    imports = []
    stem = Path(source_name or path.name).stem[:80]
    files = bags.to_files(extraction.rows, stem)
    for i, (name, content) in enumerate(files, 1):
        progress(f"Importing readings ({i} of {len(files)})")
        record, _ = store.commit_import(cid, content, name, profile, actor=actor,
                                        review_reason=f"Extracted from {source_name or path.name} (sha256 {sha}) with mapping {clean['mapping_id']}")
        imports.append(record["id"])
    if report and report.get("stated_reason"):
        payload = {"occurred_at": at(float(report.get("at_s", problem_from_s or 0.0))), "intervention_type": report.get("intervention_type") or "other",
                   "operator": report.get("operator") or actor, "stated_reason": report["stated_reason"], "reported_via": "Upload form"}
        if report.get("symptom"):
            payload["symptom"] = report["symptom"]
        if report.get("affected_function"):
            payload["affected_function"] = report["affected_function"]
        store.append(cid, "intervention_report", payload, actor=actor, source="upload form")
    if normal_from_s is not None and normal_to_s is not None:
        store.append(cid, "reference_declared", {"window_from": at(normal_from_s), "window_to": at(normal_to_s),
                                                 "basis": normal_basis or "Declared normal by the engineer who uploaded the recording",
                                                 "reference_source": f"Upload of {source_name or path.name}"}, actor=actor, source="upload form")
    progress("Investigating")
    run = store.investigate(cid, actor=actor)
    progress("Reading the rest of the recording")
    discovered = None
    try:  # evidence discovery never blocks an upload: a failure is reported, the case stands
        from app.workflows.discovery import discover_and_record
        entry = discover_and_record(store, cid, run, path, actor=actor, source_name=source_name or path.name, sha256=sha)
        discovered = {"entry_id": entry["id"], "incidents": len(entry["payload"]["incidents"]),
                      "streams": len(entry["payload"]["inventory"]["streams"]), "gaps": len(entry["payload"]["gaps"])}
    except Exception as error:  # noqa: BLE001
        discovered = {"error": f"{type(error).__name__}: {error}"[:300]}
    return {"case_id": cid, "run_id": run["id"], "imports": imports, "counts": extraction.counts, "skipped": extraction.skipped,
            "discovery": discovered,
            "recording": {"sha256": sha, "start": _iso(start), "end": _iso(end), "duration_s": round(span, 3)}}
