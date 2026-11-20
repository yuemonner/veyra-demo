"""Internal-model residual detector on CMU AirLab ALFA. RESEARCH ONLY, not part of the product.

Question: does "what the autopilot should be doing, given what it is trying to do" catch faults that Veyra's current
window comparison misses (notably engine failures), without more false alarms?

PRE-REGISTERED DESIGN (written before any fault flight is scored; fault labels are used only for scoring)
- Internal model = a linear model of the autopilot's output law, learned ONLY from the normal flights
  (dataset label "no_failure"): each output channel predicted from the controller's own errors and targets, plus a
  5 s running mean of the errors (stand-in for the integrator). Ridge regression, lambda = 1.0 on standardised inputs.
    throttle (rc3)  <- airspeed error, altitude error, their 5 s means, commanded pitch, measured airspeed
    elevator (rc2)  <- pitch error (commanded - measured), its 5 s mean, commanded pitch, |roll|, measured airspeed
    ailerons (rc5,6)<- roll error, its 5 s mean, commanded roll
    rudder (rc4)    <- measured roll, yaw rate, commanded roll
- Residual = output - prediction, minus the flight's own mean residual in its first 20 s (trim offset; the benchmark
  protocol already declares those 20 s normal), divided by the channel's robust scale (1.4826 x MAD of residuals on
  held-out normal data).
- Alarm: rolling 2 s median of |residual| >= k for at least 1 s, after the first 20 s. k per channel = 1.1 x the
  largest such rolling median seen on held-out normal flights. Isolation: the channel that alarms first.
- Honest false-alarm test: each normal flight is scored with a model AND thresholds fitted on the other normal flights
  (leave one flight out). Fault flights use the model fitted on all normal flights.
- Scoring identical to scripts/alfa_benchmark.py: detection counts within -1 s .. +10 s of the true onset and must
  name the right part. Baseline numbers come from docs/ALFA_BENCHMARK.md (engine v2.2, held-out flights).

V2 (pre-registered 2026-10-23, after v1's results; the only change): "outside what the model has seen".
- For each channel, the envelope of its inputs on the training (normal) flights: per input, the 1st..99th percentile,
  widened by 10% of that span on each side.
- Grid points with any input outside the envelope are not scored: no alarm can start there. They are counted and
  reported as "unfamiliar" time instead (abstention, not a fault).
- Thresholds are recalibrated the same way as v1, on held-out normal flights, with the same masking applied.
- To be judged on flights not used in v1 (see docs/research/INTERNAL_MODEL_ALFA.md); results on the 47 processed
  flights are reported only as "seen data".

V2 RESULT on the seen flights (13:3x): worse than v1 (15 vs 20 right-part detections, 3 vs 1 false alarms). Faults
themselves push the controller's errors outside the envelope, so v2 abstained exactly when a fault happened, and
masking the worst normal stretches lowered the elevator threshold.

V3 (exploratory, written after v2's result on the same seen flights; post-hoc by construction): the envelope covers
only the mission-side inputs (commanded pitch, commanded roll), which a fault does not move directly, not the
controller's errors or the measured state. Everything else as v2.

THREE-STATE (reviewer's design, 2026-10-23): detection exactly as v1 (nothing suppressed); each alarm is labelled
"supported" when the inputs at the alarm lie inside the normal flights' envelope (v2's rule), otherwise
"insufficient_model_coverage". On ALFA this is a design check only (flights already seen), not a performance claim.

Usage: python3 scripts/research/alfa_internal_model.py <processed folder> <out.json> [--cache-only] [--v2 | --v3 | --three-state]   (numpy)
"""
import csv
import glob
import json
import os
import statistics
import sys

import numpy as np

STEP = 0.25
REF_S = 20.0
WINDOW = (-1.0, 10.0)
ROLL_S, HOLD_S, MARGIN = 2.0, 1.0, 1.1
THREE = "--three-state" in sys.argv   # reviewer's design: detect as v1, label coverage, never suppress
V3 = "--v3" in sys.argv
V2 = "--v2" in sys.argv or V3
COMMANDS = {"pitch_cmd", "roll_cmd"}
ENVELOPE_Q, ENVELOPE_WIDEN = (1.0, 99.0), 0.10
DEVELOPMENT = {"carbonZ_2018-09-11-15-06-34_1_rudder_right_failure", "carbonZ_2018-07-30-16-39-00_1_engine_failure",
               "carbonZ_2018-09-11-17-55-30_2_left_aileron_failure"}
CH = {"elevator": "field.channels1", "throttle": "field.channels2", "rudder": "field.channels3", "aileron_l": "field.channels4", "aileron_r": "field.channels5"}
PART = {"elevator": "elevator", "throttle": "engines", "rudder": "rudder", "aileron_l": "aileron", "aileron_r": "aileron"}


def load(folder):
    name = os.path.basename(folder.rstrip("/"))

    def topic(t, fields):
        path = os.path.join(folder, f"{name}-{t}.csv")
        if not os.path.exists(path):
            return None
        ts, cols = [], {f: [] for f in fields}
        with open(path, newline="") as h:
            for r in csv.DictReader(h):
                try:
                    vals = [float(r[f]) for f in fields]
                except (KeyError, ValueError):
                    continue
                ts.append(int(r["%time"]) / 1e9)
                for f, v in zip(fields, vals):
                    cols[f].append(v)
        return (np.array(ts), {f: np.array(v) for f, v in cols.items()}) if ts else None

    src = {"out": topic("mavros-rc-out", list(CH.values())), "err": topic("mavros-nav_info-errors", ["field.alt_error", "field.aspd_error"]),
           "roll": topic("mavros-nav_info-roll", ["field.commanded", "field.measured"]), "pitch": topic("mavros-nav_info-pitch", ["field.commanded", "field.measured"]),
           "aspd": topic("mavros-nav_info-airspeed", ["field.measured"]), "imu": topic("mavros-imu-data", ["field.angular_velocity.z"])}
    if any(v is None for v in src.values()):
        return None
    t0 = max(v[0][0] for v in src.values())
    t1 = min(v[0][-1] for v in src.values())
    grid = np.arange(t0, t1, STEP)

    def hold(key, field):  # zero-order hold onto the grid (last value at or before each grid time)
        ts, cols = src[key]
        i = np.clip(np.searchsorted(ts, grid, side="right") - 1, 0, len(ts) - 1)
        return cols[field][i]

    d = {k: hold("out", f) for k, f in CH.items()}
    d.update({"alt_err": hold("err", "field.alt_error"), "aspd_err": hold("err", "field.aspd_error"), "roll_cmd": hold("roll", "field.commanded"),
              "roll": hold("roll", "field.measured"), "pitch_cmd": hold("pitch", "field.commanded"), "pitch": hold("pitch", "field.measured"),
              "aspd": hold("aspd", "field.measured"), "yaw_rate": hold("imu", "field.angular_velocity.z")})
    n = int(5.0 / STEP)
    mean5 = lambda x: np.convolve(x, np.ones(n) / n, mode="full")[:len(x)]  # noqa: E731  causal running mean
    d["pitch_err"], d["roll_err"] = d["pitch_cmd"] - d["pitch"], d["roll_cmd"] - d["roll"]
    for k in ("aspd_err", "alt_err", "pitch_err", "roll_err"):
        d[k + "_m5"] = mean5(d[k])
    onset, kinds = None, []
    for path in glob.glob(os.path.join(folder, "*failure_status-*.csv")):   # read here, used only by score()
        kinds.append(path.rsplit("failure_status-", 1)[1][:-4])
        with open(path, newline="") as h:
            rows = list(csv.DictReader(h))
        if rows:
            onset = int(rows[0]["%time"]) / 1e9 if onset is None else min(onset, int(rows[0]["%time"]) / 1e9)
    return {"name": name, "t": grid - grid[0], "x": d, "truth": {"kinds": sorted(kinds), "onset_s": None if onset is None else onset - grid[0]}}


FEATURES = {"throttle": ["aspd_err", "alt_err", "aspd_err_m5", "alt_err_m5", "pitch_cmd", "aspd"],
            "elevator": ["pitch_err", "pitch_err_m5", "pitch_cmd", "abs_roll", "aspd"],
            "aileron_l": ["roll_err", "roll_err_m5", "roll_cmd"], "aileron_r": ["roll_err", "roll_err_m5", "roll_cmd"],
            "rudder": ["roll", "yaw_rate", "roll_cmd"]}


def design(f, ch):
    x = f["x"]
    cols = [np.abs(x["roll"]) if c == "abs_roll" else x[c] for c in FEATURES[ch]]
    return np.column_stack(cols)


def fit(flights, ch, lam=1.0):
    X = np.vstack([design(f, ch) for f in flights])
    y = np.concatenate([f["x"][ch] for f in flights])
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ y)
    lo, hi = np.percentile(X, ENVELOPE_Q[0], axis=0), np.percentile(X, ENVELOPE_Q[1], axis=0)
    pad = ENVELOPE_WIDEN * (hi - lo)
    return {"w": w, "mu": mu, "sd": sd, "lo": lo - pad, "hi": hi + pad}


def familiar(model, f, ch, force=False):
    """True where every input lies inside the training envelope (v2); always True in v1."""
    if not (V2 or force):
        return np.ones(len(f["t"]), dtype=bool)
    X = design(f, ch)
    inside = (X >= model["lo"]) & (X <= model["hi"])
    if V3:
        cols = [i for i, c in enumerate(FEATURES[ch]) if c in COMMANDS]
        if not cols:
            return np.ones(len(f["t"]), dtype=bool)
        inside = inside[:, cols]
    return np.all(inside, axis=1)


def residual(model, f, ch):
    X = design(f, ch)
    pred = np.column_stack([np.ones(len(X)), (X - model["mu"]) / model["sd"]]) @ model["w"]
    r = f["x"][ch] - pred
    ref = f["t"] <= REF_S
    return r - (r[ref].mean() if ref.any() else 0.0)


def rolling_median_abs(z):
    n = max(1, int(ROLL_S / STEP))
    a = np.abs(z)
    return np.array([np.median(a[max(0, i - n + 1): i + 1]) for i in range(len(a))])


def calibrate(train):
    """Model, scale and threshold per channel, from normal flights only (leave-one-out inside for the threshold)."""
    out = {}
    for ch in CH:
        model = fit(train, ch)
        held = []
        for i, f in enumerate(train):  # residuals on flights the model did not see
            m = fit(train[:i] + train[i + 1:], ch) if len(train) > 2 else model
            held.append(residual(m, f, ch))
        allr = np.concatenate(held)
        scale = 1.4826 * np.median(np.abs(allr - np.median(allr))) + 1e-6
        worst = 0.0
        for i, (r, f) in enumerate(zip(held, train)):
            m = fit(train[:i] + train[i + 1:], ch) if len(train) > 2 else model
            med = rolling_median_abs(r / scale)
            ok = (f["t"] > REF_S) & familiar(m, f, ch)
            worst = max(worst, float(med[ok].max(initial=0.0)))
        out[ch] = {"model": model, "scale": scale, "k": MARGIN * worst}
    return out


def first_alarm(cal, f):
    best = None
    hold_n = max(1, int(HOLD_S / STEP))
    for ch, c in cal.items():
        m = rolling_median_abs(residual(c["model"], f, ch) / c["scale"])
        m[f["t"] <= REF_S] = 0.0
        m[~familiar(c["model"], f, ch)] = 0.0
        over = m >= c["k"]
        run = 0
        for i, o in enumerate(over):
            run = run + 1 if o else 0
            if run >= hold_n:
                t = f["t"][i - hold_n + 1]
                if best is None or t < best[0]:
                    best = (t, ch)
                break
    return best


def cached(path, cache):
    """load() once per flight; the resampled arrays are kept in <cache>/<flight>.npz (derived data, not committed)."""
    name = os.path.basename(path.rstrip("/"))
    file = os.path.join(cache, name + ".npz")
    if os.path.exists(file):
        z = np.load(file, allow_pickle=True)
        return z["flight"].item() if z["ok"] else None
    f = load(path)
    os.makedirs(cache, exist_ok=True)
    np.savez(file, ok=f is not None, flight=np.array(f, dtype=object))
    return f


def main(processed, out_path, cache=None, only_cache=False):
    cache = cache or os.path.join(os.path.dirname(out_path) or ".", "cache")
    paths = sorted(glob.glob(os.path.join(processed, "*/")))
    if only_cache:
        for p in paths:
            cached(p, cache)
            print("cached", os.path.basename(p.rstrip("/")), flush=True)
        return
    flights = [f for f in (cached(p, cache) for p in paths) if f]
    normal = [f for f in flights if "no_failure" in f["name"]]
    cal_all = calibrate(normal)
    results = []
    for f in flights:
        if any(f is g for g in normal):  # honest false-alarm test: never scored with a model or threshold that saw this flight
            cal = calibrate([g for g in normal if g is not f])
        else:
            cal = cal_all
        alarm = first_alarm(cal, f)                      # blind: truth not consulted above this line
        coverage = None
        if THREE and alarm:  # was the alarm raised where the model has seen such inputs? (label only; nothing suppressed)
            ch = alarm[1]
            i = int(np.searchsorted(f["t"], alarm[0]))
            j = min(len(f["t"]), i + max(1, int(HOLD_S / STEP)))
            coverage = "supported" if familiar(cal[ch]["model"], f, ch, force=True)[i:j].all() else "insufficient_model_coverage"
        after = f["t"] > REF_S
        unfamiliar = {ch: round(float((~familiar(c["model"], f, ch) & after).sum() * STEP), 1) for ch, c in cal.items()}
        kinds, onset = f["truth"]["kinds"], f["truth"]["onset_s"]
        entry = {"flight": f["name"], "split": "development" if f["name"] in DEVELOPMENT else "held_out", "truth": f["truth"],
                 "alarm": {"t_s": round(alarm[0], 2), "channel": alarm[1], "part": PART[alarm[1]]} if alarm else None,
                 "unfamiliar_s": unfamiliar, "flight_s": round(float(f["t"][-1]), 1), "coverage_at_alarm": coverage}
        if not kinds:
            entry["outcome"] = "false_alarm" if alarm else "correct_quiet"
        elif not alarm:
            entry["outcome"] = "missed"
        else:
            delay = alarm[0] - onset
            entry["delay_s"] = round(delay, 2)
            entry["outcome"] = "early_alarm" if delay < WINDOW[0] else "late" if delay > WINDOW[1] else "detected"
            entry["isolated"] = entry["outcome"] == "detected" and PART[alarm[1]] in kinds
        results.append(entry)

    def score(split):
        rs = [r for r in results if split == "all" or r["split"] == split]
        faults, normal_r = [r for r in rs if r["truth"]["kinds"]], [r for r in rs if not r["truth"]["kinds"]]
        det = [r for r in faults if r["outcome"] == "detected"]
        return {"fault_flights": len(faults), "normal_flights": len(normal_r), "detected": len(det),
                "isolated_correctly": sum(r["isolated"] for r in det), "missed": sum(r["outcome"] == "missed" for r in faults),
                "early_or_late": sum(r["outcome"] in ("early_alarm", "late") for r in faults),
                "false_alarms_on_normal_flights": sum(r["outcome"] == "false_alarm" for r in normal_r),
                "median_delay_s": round(statistics.median([r["delay_s"] for r in det]), 2) if det else None,
                "by_fault": {k: {"flights": sum(k in r["truth"]["kinds"] for r in faults), "detected": sum(k in r["truth"]["kinds"] for r in det),
                                 "isolated": sum(k in r["truth"]["kinds"] and r["isolated"] for r in det)} for k in ("engines", "aileron", "elevator", "rudder")}}
    summary = {"version": "three-state" if THREE else "v3" if V3 else "v2" if V2 else "v1", "method": "internal-model residual (autopilot output law, linear, learned from normal flights only)", "design": __doc__.split("PRE-REGISTERED")[1][:1800],
               "normal_flights_used": [f["name"] for f in normal], "thresholds_all_normal": {ch: round(c["k"], 2) for ch, c in cal_all.items()},
               "held_out": score("held_out"), "all": score("all"), "results": results}
    with open(out_path, "w") as h:
        json.dump(summary, h, indent=1, default=float)
    print(json.dumps({"held_out": summary["held_out"], "thresholds": summary["thresholds_all_normal"]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], only_cache="--cache-only" in sys.argv)
