"""End-to-end check of every upload format, through the same HTTP endpoints the upload dialog uses, with the AI
investigator on (VEYRA_AGENT_BACKEND=claude-code, or ANTHROPIC_API_KEY). Writes a JSON record and a markdown table that
says, per format, what was actually verified: parsed, rule-based reading, and whether the AI investigation completed.

Usage: PYTHONPATH=backend python scripts/validation/upload_formats_e2e.py <workspace folder with datasets/> <out dir>
Every input is named with its provenance; synthetic inputs are labelled synthetic. Nothing is tuned to these files.
"""
import base64
import csv
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))


def alfa_canonical_csv(folder: Path) -> bytes:
    """A real ALFA flight (CMU AirLab, CC0) rewritten into Veyra's canonical CSV: airspeed, roll and altitude."""
    stem = folder.name
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "machine", "signal", "value", "unit", "scope", "event", "code", "command_id"])
    for suffix, col, signal, unit in (("mavros-nav_info-airspeed", "field.measured", "vehicle.airspeed", "m/s"),
                                      ("mavros-nav_info-roll", "field.measured", "vehicle.attitude", "deg"),
                                      ("mavros-nav_info-roll", "field.commanded", "control.attitude_setpoint", "deg"),
                                      ("mavros-global_position-rel_alt", "field.data", "vehicle.altitude", "m")):
        with open(folder / f"{stem}-{suffix}.csv") as fh:
            for row in csv.DictReader(fh):
                t = datetime.fromtimestamp(int(row["%time"]) / 1e9, tz=timezone.utc).isoformat()
                w.writerow([t, "carbonZ", signal, row[col], unit, "roll" if "roll" in suffix else "", "", "", ""])
    return out.getvalue().encode()


def main(workspace: Path, out_dir: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="veyra-e2e-"))
    os.environ.update(VEYRA_ENABLE_RUNTIME="true", VEYRA_ENGINE_DB=str(tmp / "engine.db"), VEYRA_DATA_DIR=str(tmp))
    os.environ.pop("VEYRA_ENGINE_SEED", None)
    os.environ.pop("VEYRA_PRIVATE", None)
    os.environ.setdefault("VEYRA_AGENT_BACKEND", "claude-code")
    os.environ["VEYRA_ALLOW_RECORDINGS"] = "true"
    from fastapi.testclient import TestClient
    import importlib
    main_mod = importlib.import_module("app.main")
    import make_synthetic_recordings as gen

    synth = gen.write_all(tmp / "synthetic")
    ds = workspace / "datasets"
    alfa = ds / "alfa/raw/processed/carbonZ_2018-10-05-14-37-22_3_left_aileron_failure"
    real = ROOT / "fixtures/real/public"
    inputs = [
        ("Text log", real / "rplidar-no-scans/log.txt", False, "Real public log (rplidar_ros issue thread)", "Lidar gives no scans"),
        ("Text log", real / "microros-stm32-session-drops/log.txt", False, "Real public log (micro-ROS issue thread)", "Session drops right after it opens"),
        ("Text log", real / "ardupilot-sitl-startup-crash/log.txt", False, "Real public log (ArduPilot issue thread)", "Simulator crashes at startup"),
        ("ROS 1 bag", alfa / f"{alfa.name}.bag", False, "Real flight, CMU ALFA dataset (CC0)", "Aircraft rolled and lost control in flight"),
        ("PX4 ULog", ds / "uav-sead/ulg/2019-02-06_07_57_05.ulg", False, "Real PX4 flight log (UAV-SEAD public dataset)", "Check this flight for anomalies"),
        ("MCAP", synth["mcap"], True, "SYNTHETIC quadruped recording (scripts/make_synthetic_recordings.py)", "Robot started limping"),
        ("ROS 2 bag (zip)", synth["ros2"], True, "SYNTHETIC quadruped recording (scripts/make_synthetic_recordings.py)", "Robot started limping"),
    ]
    rows = []
    with TestClient(main_mod.app) as c:
        mode = c.get("/engine/mode").json()
        print("instance mode:", mode, flush=True)
        for fmt, path, synthetic, source, report in inputs:
            row = {"format": fmt, "file": path.name, "source": source, "synthetic": synthetic}
            t0 = time.time()
            try:
                if not path.exists():
                    raise FileNotFoundError(f"missing input {path}")
                up = c.post(f"/engine/recordings?filename={path.name}", content=path.read_bytes())
                row["parsed"] = up.status_code == 200
                if not row["parsed"]:
                    raise RuntimeError(f"upload {up.status_code}: {up.text[:200]}")
                rec = up.json()
                row["detected_as"] = rec["inspection"]["format"]
                body = {"mapping": rec["suggested"] or {}, "machine_id": "E2E-" + fmt.split()[0].upper() + "-" + path.stem[:12], "title": f"{report} ({'SYNTHETIC' if synthetic else 'real data'})",
                        "synthetic": synthetic, "allow_model": True, "report": {"stated_reason": report}}
                job = c.post(f"/engine/recordings/{rec['id']}/debrief", json=body).json()
                row.update(_follow(c, job["job_id"]))
            except Exception as error:  # recorded, never hidden
                row.setdefault("parsed", False)
                row["error"] = str(error)[:300]
            row["wall_s"] = round(time.time() - t0, 1)
            print(json.dumps(row), flush=True)
            rows.append(row)

        # CSV goes through the import path (canonical format), then the investigator on the case page.
        row = {"format": "CSV (Veyra canonical)", "file": "alfa_left_aileron_canonical.csv", "source": "Real flight, CMU ALFA dataset (CC0), rewritten to canonical CSV", "synthetic": False}
        t0 = time.time()
        try:
            data = alfa_canonical_csv(alfa)
            prof = next(p for p in c.get("/engine/profiles").json()["profiles"] if p["id"] == "veyra-canonical-csv")["profile"]
            cid = c.post("/engine/cases", json={"machine_id": "carbonZ", "title": "Aircraft rolled and lost control in flight (real data)", "actor": "E2E"}).json()["id"]
            imp = c.post(f"/engine/cases/{cid}/imports", json={"actor": "E2E", "filename": row["file"], "content_base64": base64.b64encode(data).decode(), "profile": prof})
            row["parsed"] = imp.status_code == 200
            if not row["parsed"]:
                raise RuntimeError(f"import {imp.status_code}: {imp.text[:200]}")
            row["detected_as"] = "canonical_csv"
            c.post(f"/engine/cases/{cid}/runs", json={"actor": "E2E"})
            job = c.post(f"/engine/cases/{cid}/investigate", json={"allow_model": True}).json()
            row.update(_follow(c, job["job_id"], cid))
        except Exception as error:
            row.setdefault("parsed", False)
            row["error"] = str(error)[:300]
        row["wall_s"] = round(time.time() - t0, 1)
        print(json.dumps(row), flush=True)
        rows.append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    (out_dir / "upload_formats_e2e.json").write_text(json.dumps({"date": stamp, "instance_mode": mode, "rows": rows}, indent=2))
    lines = [f"# Upload formats, end to end ({stamp})", "",
             f"Instance: {mode['mode']} ({mode.get('model') or 'no model'}). Same HTTP endpoints as the upload dialog.", "",
             "| Format | Input | Parsed | Rule-based reading | AI investigation | Status · headline |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("mode") == "agent":
            ai = f"completed ({r['steps']} checks, {r['seconds']} s)"
        else:
            ai = ("not completed: " + (r.get("fallback_reason") or r.get("error") or "not run"))[:90]
        headline = (r.get("headline") or r.get("error") or "").replace("|", "/")[:110]
        lines.append(f"| {r['format']} | {r['source']} | {'yes' if r.get('parsed') else 'no'} | {'yes' if r.get('status') else 'no'} | {ai} | "
                     f"{r.get('status', '')} · {headline} |")
    (out_dir / "upload_formats_e2e.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(r.get("parsed") and r.get("status") for r in rows) else 1


def _follow(c, job_id: str, case_id: str = None) -> dict:
    deadline = time.time() + 600
    while time.time() < deadline:
        job = c.get(f"/engine/jobs/{job_id}").json()
        if job["state"] == "failed":
            return {"error": job.get("error")}
        if job["state"] == "done":
            break
        time.sleep(2)
    else:
        return {"error": "timed out after 10 minutes"}
    cid = case_id or job.get("case_id") or job["result"]["case_id"]
    d = c.get(f"/engine/cases/{cid}/investigation").json()
    inv = d.get("investigation") or {}
    r = inv.get("result") or {}
    return {"case_id": cid, "mode": inv.get("mode"), "model": inv.get("model"), "fallback_reason": inv.get("fallback_reason"),
            "steps": len(inv.get("steps") or []), "seconds": inv.get("seconds"), "dropped": len(inv.get("dropped") or []),
            "status": r.get("status"), "headline": r.get("headline"), "hypotheses": len(r.get("hypotheses") or []),
            "next_action": ((r.get("next_action") or {}).get("action") or "")[:160]}


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
