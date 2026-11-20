"""Run the deterministic engine (no model) of ONE code root on a list of sealed cases; one JSON per case, rerun-safe.
Run as a subprocess so each code root imports its own `app`:

  python engine_runner.py --code-root R --generator G.py --cases CASES.json --out DIR
Works for any code that has app.physchecks.anticipate (screen, focus) and app.agent.inquiry; the watch is read where the
code has it (app.physchecks.watch). Nothing here reads the truth.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

FRACTIONS = (0.5, 0.75, 0.9, 1.0)
LEAVES = ("L1", "L2a", "L2b", "L2c", "L3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--generator", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.code_root).resolve()
    sys.path.insert(0, str(root / "backend"))
    # import the code root's own modules FIRST (the generator would otherwise put its own checkout on the path)
    from app.agent import inquiry as IQ
    from app.physchecks import anticipate as ANT, compute as C, dynamics as D
    try:
        from app.physchecks import watch as W
    except ImportError:
        W = None
    import app
    assert Path(app.__file__).resolve().is_relative_to(root), f"imported {app.__file__}, not {root}"
    spec = importlib.util.spec_from_file_location("gen", a.generator)
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    assert Path(sys.modules["app"].__file__).resolve().is_relative_to(root)
    import subprocess
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for c in json.loads(Path(a.cases).read_text()):
        f = out / f"{c['label']}.json"
        if f.exists():
            continue
        t0 = time.time()
        try:
            p = G.draw(c["domain"], c["cls"], c["seed"])
            rec, prof = G.to_recording(p, c["label"])
            ctx = C.Context(rec, prof)
            iq = IQ.Inquiry(rec, prof, {"hypotheses": []})
            st = {h: iq.standing(h) for h in LEAVES if h in iq.hypotheses}
            g0 = ANT.focus(ctx)
            ev_t = None
            if g0:
                e = D._event_time(ctx, g0["window"], g0["event"], None)
                ev_t = e and e["t"]
            if ev_t is None:                                     # no event: the watch reads the whole record
                gs = ANT.groups(ctx)
                ev_t = None
            watch = {}
            if W is not None:
                for fr in FRACTIONS:
                    as_of = None if ev_t is None and fr == 1.0 else ((ev_t - 1.0) * fr if ev_t else None)
                    if ev_t is None and fr < 1.0:
                        # no event: as_of fractions of the record's end
                        end = max(float(s.t[-1]) for s in [ctx.series(m, gs[0]["window"]) for m in gs[0]["members"]]) if gs else None
                        as_of = end * fr if end else None
                    try:
                        gw = W.watch(ctx, as_of)["groups"]
                        g = gw[0] if gw else {}
                        flags = g.get("flags") if "flags" in g else (["material_departure"] if g.get("status") == "material_departure" else [])
                        watch[str(fr)] = {"status": g.get("status"), "flags": flags, "possible": g.get("possible") or [],
                                          "leans": [x["class"] for x in g.get("leans_to") or []],
                                          "check": (g.get("recommended_check") or {}).get("id"), "as_of": as_of}
                    except Exception as ex:                     # noqa: BLE001 - recorded, never hidden
                        watch[str(fr)] = {"error": f"{type(ex).__name__}: {ex}"}
            r = {"label": c["label"], "commit": commit, "event_t": ev_t, "investigated": bool(iq.dyn),
                 "standing": {h: s["standing"] for h, s in st.items()}, "contradicted_by": {h: s["contradicted_by"] for h, s in st.items()},
                 "next_check": ((iq.dyn or {}).get("next_check") or {}).get("id"), "unresolved": (iq.dyn or {}).get("unresolved_class_pairs"),
                 "generated": [{"q": t["quantity"], "v": t["value"], "o": t["update"]["outcomes"]} for t in (iq.dyn or {}).get("generated_tests") or []],
                 "after_tests": ({h["id"]: h["after_tests"] for h in iq.assessment_after_tests()["hypotheses"]} if hasattr(iq, "assessment_after_tests") else None),
                 "watch": watch, "seconds": round(time.time() - t0, 2)}
        except Exception as ex:                                 # noqa: BLE001 - recorded, never hidden
            r = {"label": c["label"], "commit": commit, "error": f"{type(ex).__name__}: {ex}"}
        f.write_text(json.dumps(r, indent=1, default=str))
        print(c["label"], "error" if "error" in r else "ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
