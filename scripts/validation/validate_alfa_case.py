"""Strict validation of the ALFA demo case (case-9ebc37dd5591): three questions.

Q1  Is the anomaly real? Compare the case flight with every normal flight of the same aircraft
    (and the three flown the same day), under matched conditions (binned by commanded roll).
Q2  Are the agent's checks useful? Run its top check by hand; compare with what simple
    engineer-style baselines would point at.
Q3  Is the evidence sufficient? Replay every agent tool call (byte-identical?), check every number
    in every claim against the cited tool output, and recompute key facts from the original bytes.

Usage:
  python scripts/validation/validate_alfa_case.py                      # Q2 + Q3 (seed only)
  python scripts/validation/validate_alfa_case.py <ALFA processed/>    # + Q1 (needs the public dataset)
Ground truth (failure_status) is read only for scoring, after everything else.
Dataset: CMU AirLab ALFA, doi:10.1184/R1/12707963 (CC0 per README).
"""
from __future__ import annotations

import bisect, csv, glob, gzip, hashlib, json, os, re, shutil, sqlite3, statistics as st, sys, tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.agent import tools as toolset  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

CID = None  # the investigated ALFA case in the seed (not one of its known-good runs); found in load_seed()
FLIGHT = "carbonZ_2018-09-11-15-06-34_2_rudder_right_failure"
SAME_DAY = {"carbonZ_2018-09-11-14-16-55_no_failure", "carbonZ_2018-09-11-14-41-38_no_failure", "carbonZ_2018-09-11-15-05-11_2_no_failure"}
NAMES = {"rc_out": "out", "attitude_cmd": "cmd", "attitude_meas": "meas", "airspeed": "airspeed", "altitude": "alt", "groundspeed": "gs", "throttle": "thr"}
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def load_seed():
    tmp = Path(tempfile.mkdtemp())
    with gzip.open(REPO / "fixtures/seed/alfa_carbonz_rudder_case.sqlite.gz") as g, open(tmp / "e.db", "wb") as o:
        shutil.copyfileobj(g, o)
    store = CaseStore(tmp / "e.db")
    global CID
    CID = next(c["id"] for c in store.cases() if c.get("fault_type") != "known_good_run" and "ALFA" in c["title"])
    return store


def holds(pts):
    out, i = [], 0
    while i < len(pts):
        j = i
        while j + 1 < len(pts) and pts[j + 1][1] == pts[i][1]:
            j += 1
        out.append((pts[j][0] - pts[i][0], pts[i][1], j - i + 1, pts[i][0]))
        i = j + 1
    return out


def q3(store):
    p = [e for e in store.ledger(CID) if e["kind"] == "agent_report"][-1]["payload"]
    box = toolset.Toolbox(store, CID)
    box.run = [r for r in store.runs(CID) if r["id"] == p["run_id"]][0]
    outputs, replay = {}, []
    for c in p["tool_calls"]:
        text = toolset.serialize(box.call(c["tool"], c["input"]))
        outputs[c["id"]] = text
        replay.append({"id": c["id"], "tool": c["tool"], "byte_identical": hashlib.sha256(text.encode()).hexdigest() == c["result_sha256"]})

    def present(n, blob):
        x = float(n)
        return n in blob or any(abs(x - float(m)) <= max(0.051, abs(float(m)) * 0.006) for m in NUM.findall(blob))
    r, claims = p["report"], []
    items = [("observation", o["text"], o["evidence"]) for o in r["observations"]] + \
            [(f"hypothesis ({h['status']})", h["statement"], h["support"] + h.get("against", [])) for h in r["hypotheses"]] + \
            [("proposed check", c["action"] + " " + c["why"], c["evidence"]) for c in r["proposed_checks"]]
    for kind, text, cites in items:
        blob = " ".join(outputs[c] for c in cites)
        nums = [n for n in NUM.findall(text) if "." in n or len(n.lstrip("-")) >= 3]
        hedge = re.search(r"\b(may|might|could|likely|probably|possibly|suggests?|appears?|seems?|consistent with|fits|presumably|artifact|artefact)\b", text, re.I)
        claims.append({"kind": kind, "cites": cites, "numbers_checked": len(nums), "numbers_not_in_cited_output": [n for n in nums if not present(n, blob)],
                       "interpretation_in_observation": hedge.group(0) if hedge and kind == "observation" else None, "text": text})
    run = box.run
    engine = {"engine": run["versions"]["engine"], "known_good_runs": len((run.get("machine_baseline") or {}).get("known_good_runs", [])),
              "output_findings": [f["text"] for f in (run["findings"].get("actuator_outputs") or {}).get("findings", [])],
              "suppressed_as_normal": [f["text"] for f in ((run["findings"].get("actuator_outputs") or {}).get("machine_baseline") or {}).get("suppressed_as_normal", [])],
              "tracking": [f["text"] for f in (run["findings"].get("tracking") or {}).get("findings", [])],
              "brief_events_in_window": (run["findings"].get("transients") or {}).get("events_in_window")}
    return {"case_id": CID, "agent": p.get("agent"), "model": p["model"], "citations_valid": p["citations_valid"], "turns": p["turns"], "duration_ms": p["duration_ms"],
            "tool_calls": len(replay), "replay": replay, "claims": claims, "engine": engine,
            "leading": [h["statement"] for h in p["report"]["hypotheses"] if h["status"] == "leading"]}


def case_series(store):
    content = store.original_bytes(store.imports(CID, include_rows=False)[0]["id"])
    s = defaultdict(list)
    for line in content.decode().splitlines():
        r = json.loads(line)
        s[(NAMES[r["signal"]], r["scope"] or "")].append((r["t"] / 1e9, r["value"]))
    for k in s:
        s[k].sort()
    return s


def facts_from_original_bytes(s):
    att = {ax: s[("meas", ax)] for ax in ("roll", "pitch", "yaw")}
    yaw = att["yaw"]
    return {
        "attitude_repeated_sample_share": {ax: round(sum(a[1] == b[1] for a, b in zip(v, v[1:])) / (len(v) - 1), 3) for ax, v in att.items()},
        "yaw_wrap_crossings": [round(b[0], 3) for a, b in zip(yaw, yaw[1:]) if abs(b[1] - a[1]) > 180],
        "rudder_longest_hold_after_reference": max(holds([p for p in s[("out", "rudder_rc4")] if p[0] > s[("out", "rudder_rc4")][0][0] + 20]))[:3],
        "output_rate_hz": round(len(s[("out", "rudder_rc4")]) / (s[("out", "rudder_rc4")][-1][0] - s[("out", "rudder_rc4")][0][0]), 2),
        "airspeed_all_zero": all(v == 0 for _, v in s[("airspeed", "")]),
    }


def q2(s, onset):
    t0 = min(v[0][0] for v in s.values())
    ref = (t0, t0 + 20)
    rows_near = {k: [(round(t - onset, 2), v) for t, v in s[k] if -2.5 <= t - onset <= 2.5]
                 for k in [("out", "rudder_rc4"), ("cmd", "yaw"), ("cmd", "roll"), ("out", "throttle_rc3")]}
    rank, first = [], []
    for k, pts in s.items():
        r = [v for t, v in pts if ref[0] <= t <= ref[1]]
        if not r:
            continue
        lo, hi = min(r), max(r)
        after = [v for t, v in pts if t > ref[1]]
        rank.append((round(max(0, max(after) - hi, lo - min(after)) / ((hi - lo) or 1e-9), 2), f"{k[0]}:{k[1]}"))
        hit = next((t for t, v in pts if t > ref[1] and not lo <= v <= hi), None)
        if hit:
            first.append((round(hit - onset, 2), f"{k[0]}:{k[1]}"))
    return {"top_check_rows_around_onset": {f"{a}:{b}": v for (a, b), v in rows_near.items()},
            "baseline_rank_by_excursion": sorted(rank, reverse=True)[:5],
            "baseline_first_out_of_reference_s_vs_onset": sorted(first)[:6]}


def q1(processed):
    def rows(folder, topic):
        f = glob.glob(os.path.join(processed, folder, f"*-{topic}.csv"))
        return list(csv.DictReader(open(f[0], newline=""))) if f else []

    def series(folder):
        s = defaultdict(list)
        for r in rows(folder, "mavros-rc-out"):
            t = int(r["%time"]) / 1e9
            s["rudder"].append((t, float(r["field.channels3"])))
            s["throttle"].append((t, float(r["field.channels2"])))
        for r in rows(folder, "mavros-nav_info-roll"):
            t = int(r["%time"]) / 1e9
            s["roll"].append((t, float(r["field.commanded"]), float(r["field.measured"])))
        return s

    def track(s, w):
        out, last = [], None
        for t, c, m in s["roll"]:
            if w[0] <= t <= w[1] and (c, m) != last:
                last = (c, m)
                out.append((abs(c), abs(c - m)))
        return out

    def binned(e):
        res = {}
        for lo, hi in ((0, 10), (10, 30), (30, 46)):
            x = sorted(v for c, v in e if lo <= c < hi)
            if len(x) >= 5:
                res[f"{lo}-{hi}"] = {"n": len(x), "median": round(st.median(x), 1), "p95": round(x[int(.95 * (len(x) - 1))], 1)}
        return res
    onset = min(int(r["%time"]) / 1e9 for p in glob.glob(os.path.join(processed, FLIGHT, "*failure_status-*.csv")) for r in csv.DictReader(open(p)))
    normal = sorted(d for d in os.listdir(processed) if "no_failure" in d)
    flights, pooled, same = {}, [], []
    for f in normal:
        s = series(f)
        h = holds(s["rudder"])
        flights[f] = {"minutes": round((s["rudder"][-1][0] - s["rudder"][0][0]) / 60, 2), "rudder_range": [min(v for _, v in s["rudder"]), max(v for _, v in s["rudder"])],
                      "rudder_longest_hold_s": round(max(h)[0], 2), "throttle_s_at_or_above_1700": round(sum(d for d, v, *_ in holds(s["throttle"]) if v >= 1700), 2)}
        e = track(s, (0, 1e12))
        pooled += e
        same += e if f in SAME_DAY else []
    s = series(FLIGHT)
    post = max(holds([p for p in s["rudder"] if p[0] >= onset]))
    return {"normal_flights": len(normal), "normal_minutes": round(sum(v["minutes"] for v in flights.values()), 1), "per_normal_flight": flights,
            "fault_flight": {"rudder_hold_after_onset": {"seconds": round(post[0], 2), "value": post[1], "samples": post[2], "starts_s_after_onset": round(post[3] - onset, 2)},
                             "roll_tracking_before_onset": binned(track(s, (0, onset))), "roll_tracking_after_onset": binned(track(s, (onset, 1e12)))},
            "normal_roll_tracking_all": binned(pooled), "normal_roll_tracking_same_day": binned(same)}, onset


def main():
    store = load_seed()
    out = {"q3": q3(store)}
    s = case_series(store)
    out["q3"]["facts_from_original_bytes"] = facts_from_original_bytes(s)
    onset = None
    if len(sys.argv) > 1:
        out["q1"], onset = q1(sys.argv[1])
    t0 = min(v[0][0] for v in s.values())
    out["q2"] = q2(s, onset or t0 + 51.97)  # 51.97 s: ALFA failure_status onset for this flight (benchmark results)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
