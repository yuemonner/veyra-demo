"""UAV-SEAD height cross-check: the pre-registered rule in docs/research/UAVSEAD_HEIGHT_CHECK.md, implemented once.

Usage: python scripts/research/uavsead_height_check.py <folder with the 44 .ulg files> <out.json>
Files are named as the download script names them: the dataset key with "/" replaced by "_", plus ".ulg".
Calibration flights set the thresholds first; test flights are processed only afterwards, with thresholds fixed.

Implementation details fixed before any flight data was read (committed with this file):
- Airborne: vehicle_land_detected.landed == False, between a landed->airborne and the next airborne->landed change;
  5 s after takeoff and 2 s before landing are excluded. A flight with no vehicle_land_detected is not processed.
- Distance sensor: instances whose `orientation` is 25 (downward facing) when that field exists, else instance 0;
  only samples with min_distance <= current_distance <= max_distance; tilt from vehicle_attitude (nearest, <= 0.2 s).
- Vision: vehicle_vision_position.z, else vehicle_visual_odometry.z, else att_pos_mocap.z (all NED, up = -z).
- Barometer: vehicle_air_data.baro_alt_meter, else sensor_baro.altitude (instance 0).
- Offset removal: e minus the median of valid e in (t - 30 s, t]. Smoothing: median of that in (t - 1 s, t].
- Alarm: smoothed |e| > threshold on consecutive valid samples (gaps <= 0.5 s) for >= 1 s. Alarm start = first sample
  above threshold of that run. One alarm per run; a new alarm needs the run to end first.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.ingest import ulog  # noqa: E402

SELECTION = ROOT / "backend/research/uavsead/selection_v1.json"
FLOOR = {"vision": 0.25, "range": 0.30, "baro": 1.0, "gps": 1.0}
MARGIN, RESAMPLE_S, OFFSET_S, SMOOTH_S, HOLD_S, GAP_S = 1.1, 0.2, 30.0, 1.0, 1.0, 0.5
AFTER_TAKEOFF_S, BEFORE_LANDING_S = 5.0, 2.0
EARLY_S, LATE_S = 2.0, 5.0
TOPICS = {"vehicle_land_detected", "vehicle_local_position", "vehicle_attitude", "distance_sensor", "vehicle_vision_position",
          "vehicle_visual_odometry", "att_pos_mocap", "vehicle_air_data", "sensor_baro", "vehicle_gps_position"}


def series(log, topic, fields, multi=None, where=None):
    """[(t_s, {field: value})] for one topic; multi=None takes every instance unless `where` filters."""
    lay = log["layouts"].get(topic)
    if not lay:
        return []
    out = []
    for (name, m), rows in log["data"].items():
        if name != topic or (multi is not None and m != multi):
            continue
        for t, raw in rows:
            vals = {f: lay.get(raw, f) for f in fields if f in lay.fields}
            if where and not where(vals):
                continue
            out.append((t / 1e6, vals))
    out.sort(key=lambda x: x[0])
    return out


def airborne_intervals(log):
    rows = series(log, "vehicle_land_detected", ["landed"])
    if not rows:
        return None
    spans, start, prev = [], None, True
    for t, v in rows:
        landed = bool(v.get("landed"))
        if prev and not landed:
            start = t
        if not prev and landed and start is not None:
            spans.append((start + AFTER_TAKEOFF_S, t - BEFORE_LANDING_S))
            start = None
        prev = landed
    if start is not None:  # log ends airborne: up to the last estimator sample
        spans.append((start + AFTER_TAKEOFF_S, None))
    return spans


def nearest(ts, vs, t):
    i = bisect.bisect_left(ts, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(ts) and abs(ts[j] - t) <= RESAMPLE_S and (best is None or abs(ts[j] - t) < abs(ts[best] - t)):
            best = j
    return None if best is None else vs[best]


def tilt_cos(log):
    # v1.1 bug fix (after the first run): the quaternion is one array field "q"; v1 asked for "q[0]".."q[3]", found
    # nothing, so every distance-sensor sample was dropped. The rule and the threshold procedure are unchanged.
    rows = series(log, "vehicle_attitude", ["q"])
    ts, vs = [], []
    for t, v in rows:
        q = v.get("q")
        if not q or len(q) != 4:
            continue
        w, x, y, z = q
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
        ts.append(t); vs.append(math.cos(roll) * math.cos(pitch))
    return ts, vs


def references(log, kinds):
    """{kind: (times, heights_up_m)} for the independent references of this flight."""
    out = {}
    if "vision" in kinds:
        for topic in ("vehicle_vision_position", "vehicle_visual_odometry", "att_pos_mocap"):
            rows = [(t, -v["z"]) for t, v in series(log, topic, ["z"], multi=0) if v.get("z") is not None and math.isfinite(v["z"])]
            if rows:
                out["vision"] = rows; break
    if "range" in kinds:
        lay = log["layouts"].get("distance_sensor")
        down = (lambda v: v.get("orientation") == 25) if lay and "orientation" in lay.fields else None
        rows = series(log, "distance_sensor", ["current_distance", "min_distance", "max_distance", "orientation"],
                      multi=None if down else 0, where=down)
        ct, cv = tilt_cos(log)
        r = []
        for t, v in rows:
            d, lo, hi = v.get("current_distance"), v.get("min_distance"), v.get("max_distance")
            c = nearest(ct, cv, t)
            if None in (d, lo, hi, c) or not (lo <= d <= hi):
                continue
            r.append((t, d * c))
        if r:
            out["range"] = r
    if "baro" in kinds:
        rows = [(t, v["baro_alt_meter"]) for t, v in series(log, "vehicle_air_data", ["baro_alt_meter"], multi=0) if v.get("baro_alt_meter") is not None]
        if not rows:
            rows = [(t, v["altitude"]) for t, v in series(log, "sensor_baro", ["altitude"], multi=0) if v.get("altitude") is not None]
        if rows:
            out["baro"] = rows
    if "gps" in kinds:
        rows = [(t, v["alt"] / 1000.0) for t, v in series(log, "vehicle_gps_position", ["alt"], multi=0) if v.get("alt") is not None]
        if rows:
            out["gps"] = rows
    return {k: ([t for t, _ in v], [h for _, h in v]) for k, v in out.items()}


def disagreement(est_t, est_h, ref, spans):
    """Per estimator sample inside the airborne spans: (t, smoothed |e|) where a reference sample is valid."""
    rt, rh = ref
    pts = []
    for t, h in zip(est_t, est_h):
        if not any(a <= t and (b is None or t <= b) for a, b in spans):
            continue
        r = nearest(rt, rh, t)
        if r is not None and math.isfinite(h):
            pts.append((t, h - r))
    if not pts:
        return np.array([]), np.array([])
    t = np.array([p[0] for p in pts]); e = np.array([p[1] for p in pts])
    lo = np.searchsorted(t, t - OFFSET_S, side="right")
    d = np.array([e[i] - np.median(e[lo[i]:i + 1]) for i in range(len(e))])
    lo1 = np.searchsorted(t, t - SMOOTH_S, side="right")
    s = np.array([np.median(d[lo1[i]:i + 1]) for i in range(len(d))])
    return t, np.abs(s)


def alarms(t, s, thr):
    out, start, last, confirmed = [], None, None, False
    for ti, si in zip(t, s):
        if si <= thr:
            start = None
            continue
        if start is None or ti - last > GAP_S:
            start, confirmed = ti, False
        last = ti
        if not confirmed and ti - start >= HOLD_S:
            confirmed = True
            out.append(float(start))
    return out


def process(path: Path, row):
    role, key, _bytes, board, hgt, rng, aid, indep, _sig, ranges = row
    log = ulog.read(path, TOPICS)
    spans = airborne_intervals(log)
    if spans is None:
        return {"error": "no vehicle_land_detected"}
    est = [(t, -v["z"]) for t, v in series(log, "vehicle_local_position", ["z"], multi=0) if v.get("z") is not None]
    if not est:
        return {"error": "no vehicle_local_position data"}
    est_t, est_h = [t for t, _ in est], [h for _, h in est]
    spans = [(a, b if b is not None else est_t[-1]) for a, b in spans if b is None or b > a]
    airborne_s = sum(b - a for a, b in spans)
    refs = references(log, indep)
    out = {"airborne_s": round(airborne_s, 1), "spans_s": [[round(a, 1), round(b, 1)] for a, b in spans], "refs": {}}
    for k in indep:
        if k not in refs:
            out["refs"][k] = {"state": "no independent reference", "coverage": 0.0}
            continue
        t, s = disagreement(est_t, est_h, refs[k], spans)
        cov = (len(t) / max(1, sum(1 for x in est_t if any(a <= x <= b for a, b in spans))))
        out["refs"][k] = {"coverage": round(cov, 3), "t": t, "s": s}
    return out


def main(folder: Path, out_path: Path):
    sel = json.loads(SELECTION.read_text())
    rows = sel["selected"]
    file_of = lambda key: folder / (key.replace("/", "_") + ".ulg")
    # 1. Calibration: thresholds from the 12 calibration flights only.
    cal_max = {}
    cal_report = []
    for row in rows:
        if row[0] != "calibration_normal":
            continue
        r = process(file_of(row[1]), row)
        entry = {"key": row[1], "error": r.get("error"), "airborne_s": r.get("airborne_s"), "refs": {}}
        for k, v in (r.get("refs") or {}).items():
            m = float(v["s"].max()) if "s" in v and len(v["s"]) else None
            entry["refs"][k] = {"coverage": v["coverage"], "max_smoothed_abs_e_m": None if m is None else round(m, 3)}
            if m is not None:
                cal_max[k] = max(cal_max.get(k, 0.0), m)
        cal_report.append(entry)
    thresholds = {k: round(max(FLOOR[k], MARGIN * cal_max[k]), 3) if k in cal_max else None for k in FLOOR}
    # 2. Test flights, thresholds fixed.
    results = []
    for row in rows:
        if not row[0].startswith("test"):
            continue
        r = process(file_of(row[1]), row)
        entry = {"key": row[1], "role": row[0], "board": row[3][-4:], "independent": row[7], "error": r.get("error"),
                 "airborne_s": r.get("airborne_s"), "labelled_ranges_s": [[a / 1e6, b / 1e6] for a, b in row[9]], "refs": {}, "alarms": []}
        for k, v in (r.get("refs") or {}).items():
            if "s" not in v:
                entry["refs"][k] = {"state": v["state"], "coverage": 0.0}; continue
            thr = thresholds.get(k)
            al = alarms(v["t"], v["s"], thr) if thr is not None else []
            entry["refs"][k] = {"coverage": v["coverage"], "threshold_m": thr, "max_smoothed_abs_e_m": round(float(v["s"].max()), 3) if len(v["s"]) else None,
                                "alarms_s": [round(a, 2) for a in al]}
            entry["alarms"] += [(round(a, 2), k) for a in al]
        entry["alarms"].sort()
        lab = entry["labelled_ranges_s"]
        hits = [(a, k, next(r0 for r0, r1 in lab if r0 - EARLY_S <= a <= r1 + LATE_S)) for a, k in entry["alarms"]
                if any(r0 - EARLY_S <= a <= r1 + LATE_S for r0, r1 in lab)]
        entry["unlabelled_alarms"] = [[a, k] for a, k in entry["alarms"] if not any(r0 - EARLY_S <= a <= r1 + LATE_S for r0, r1 in lab)]
        if entry["error"]:
            entry["outcome"] = "not processed"
        elif row[0] == "test_normal":
            entry["outcome"] = "false alarm" if entry["alarms"] else "quiet"
        else:
            entry["outcome"] = "detected" if hits else "missed"
            entry["delay_s"] = round(hits[0][0] - hits[0][2], 2) if hits else None
            entry["ranges_hit"] = len({h[2] for h in hits})
        results.append(entry)
    out_path.write_text(json.dumps({"version": "uavsead-height-check-v1.1", "selection": sel["version"], "thresholds_m": thresholds,
                                    "calibration": cal_report, "results": results}, indent=1, default=float))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
