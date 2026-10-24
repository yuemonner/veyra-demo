"""Evidence discovery as part of an investigation: after a recording is imported and investigated, read the WHOLE original
recording around each anomaly the run found, and record what it holds as a trusted ledger record (kind
`evidence_discovery`), so the case's questions, Brief and Next action use it before anyone is asked for more evidence.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app import discovery
from app.engine import mechanisms
from app.machines import profiles


def profile_for_run(run: Dict[str, Any]) -> Dict[str, Any]:
    pid = (run.get("machine_profile") or {}).get("id")
    every = profiles.load_all()
    chosen = every.get(pid) or every[profiles.DEFAULT_ID]
    if not chosen.get("signal_chain"):
        chosen = {**chosen, "signal_chain": every[profiles.DEFAULT_ID].get("signal_chain")}
    return chosen


def incidents_from_run(run: Dict[str, Any], chain: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    """The run's timed anomalies (earliest first; one per event), else the start of the reported symptom."""
    timed = sorted([a for a in mechanisms.anomalies(run, chain) if a["t"] is not None], key=lambda a: a["t"])
    out: List[Dict[str, Any]] = []
    lag = float(chain.get("lag_s") or mechanisms.DEFAULT_LAG_S)
    for a in timed:
        if out and a["t"] - out[-1]["t"] <= lag:
            continue
        out.append({"id": a["key"], "t": a["t"], "label": a["text"], "channel": a["scope"], "basis": "anomaly found by the investigation"})
        if len(out) >= limit:
            break
    if not out:
        window = run.get("symptom_window") or []
        t = mechanisms._iso_t(window[0]) if window else None
        if t is not None:
            out.append({"id": "symptom-start", "t": t, "label": "start of the reported symptom", "channel": None,
                        "basis": "no timed anomaly; the reported symptom start is used"})
    return out


def discover_and_record(store, case_id: str, run: Dict[str, Any], path: Path, *, actor: str, source_name: Optional[str] = None,
                        sha256: Optional[str] = None) -> Dict[str, Any]:
    chain = profile_for_run(run)["signal_chain"]
    found = discovery.discover(Path(path), incidents_from_run(run, chain), chain, source_name=source_name, sha256=sha256)
    return store.append_trusted(case_id, "evidence_discovery", {**found, "run_id": run["id"]}, actor=actor,
                                source=f"veyra://discovery/{discovery.DISCOVERY_VERSION}")
