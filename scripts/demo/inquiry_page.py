"""Build a self-contained HTML page from ONE recorded Veyra investigation (an inquiry-eval-v1 record), and nothing else.

  python scripts/demo/inquiry_page.py <rerun.json | H*.json> --out page.html [--title "..."]

Every number on the page is read from the record. The page states the record's SHA-256, recomputes the inquiry ledger's
hash chain, and says what the page is not. No text is written by hand about the case. A page built from customer data
contains customer data: keep it where the data is kept and never publish it.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any, Dict, List

KIND_LABEL = {"observation": "Observed in rows", "reported_value": "Reported by the machine (not verified)", "assumption": "Assumption",
              "inferred_parameter": "Inferred from data", "conditional": "Holds only under stated assumptions", "verified_fact": "Verified"}
OUT_LABEL = {"consistent": "consistent", "contradicted": "contradicted", "no_result": "no result"}


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def chain_ok(entries: List[Dict[str, Any]]) -> bool:
    for i, en in enumerate(entries):
        prev = entries[i - 1]["hash"] if i else "0" * 64
        h = hashlib.sha256((prev + json.dumps(en["payload"], sort_keys=True, default=str) + en["kind"]).encode()).hexdigest()
        if en["prev"] != prev or en["hash"] != h:
            return False
    return True


def expect_text(x: Dict[str, Any]) -> str:
    if "between" in x:
        return f"between {x['between'][0]} and {x['between'][1]}"
    if "lt" in x:
        return f"below {x['lt']}"
    if "gt" in x:
        return f"above {x['gt']}"
    if "approx" in x:
        return f"about {x['approx']} (± {x.get('tol', '10 %')})"
    return f"= {x.get('eq')}"


def evidence_table(ev: List[Dict[str, Any]]) -> str:
    if not ev:
        return ""
    rows = "".join(f"<tr><td>{e(r.get('file'))}</td><td class=n>{e(r.get('row'))}</td><td>{e(r.get('role') or r.get('field'))}</td>"
                   f"<td class=n>{e(r.get('t'))}</td><td class=n>{e(r.get('value'))}</td></tr>" for r in ev)
    return f"<details><summary>Source rows ({len(ev)})</summary><table><tr><th>File</th><th>Line</th><th>Signal</th><th>t (s)</th><th>Value</th></tr>{rows}</table></details>"


def build(record_path: Path, title: str) -> str:
    raw = record_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    rec = json.loads(raw)
    mr = rec.get("main_investigator_record") or {}
    res = mr.get("result") or {}
    iq = mr.get("inquiry") or {}
    entries = iq.get("entries") or []
    tests = {en["payload"]["id"]: en["payload"] for en in entries if en["kind"] == "test"}
    preds = {p["id"]: p for p in iq.get("predictions") or []}
    hyps = iq.get("hypotheses") or []
    out: List[str] = []
    out.append(f"<header><p class=eyebrow>Veyra autonomous investigation · recorded run</p><h1>{e(title)}</h1>"
               f"<p class=meta>Commit {e(rec.get('commit'))} · model {e(mr.get('model'))} · mode {e(mr.get('mode'))} · "
               f"{len(mr.get('steps') or [])} tool calls · {e(mr.get('seconds'))} s</p>"
               f"<p class=note>This page is generated only from the recorded investigation file (SHA-256 <code>{sha[:16]}…</code>). "
               "Nothing on it was written by hand about the case. It is not the frozen v4 run and not the team's post-run analysis.</p></header>")
    out.append(f"<section><h2>What Veyra concluded</h2><p class=lead>{e(res.get('headline'))}</p><p>{e(res.get('explanation'))}</p>"
               f"<p class=meta>Status {e(res.get('status'))} · confidence {e(res.get('confidence'))}</p></section>")
    if res.get("ranking"):
        tiers = "".join(f"<li><div class=tier>{' + '.join(e(h) for h in t.get('hypotheses') or [])}{' <span class=tag>may act together</span>' if t.get('coexist') else ''}</div>"
                        f"<p>{e(t.get('basis'))}</p><p class=meta>Placed by tests: {e(', '.join(t.get('tests') or []) or 'none')}</p></li>" for t in res["ranking"])
        out.append(f"<section><h2>Ranking (most likely first; a tier holds causes no test separated)</h2><ol class=tiers>{tiers}</ol></section>")
    hrows = "".join(f"<tr><td><b>{e(h['id'])}</b></td><td>{e(h.get('statement'))}</td><td>{e(h.get('source'))}</td>"
                    f"<td><span class='st {e(h.get('standing'))}'>{e(h.get('standing'))}</span></td><td>{e(', '.join(h.get('consistent_with') or []))}</td>"
                    f"<td>{e(', '.join(h.get('contradicted_by') or []))}</td></tr>" for h in hyps)
    out.append(f"<section><h2>Competing hypotheses</h2><table><tr><th>Id</th><th>Hypothesis</th><th>Source</th><th>Standing</th><th>Survived</th><th>Contradicted by</th></tr>{hrows}</table>"
               "<p class=meta>A contradiction weakens a hypothesis under the test's assumptions; it never rules it out. Only a verified outcome changes a status.</p></section>")
    cards = []
    for pid, p in preds.items():
        t = tests.get(p.get("test_id") or "")
        lines = "".join(f"<tr><td><b>{e(x['hypothesis'])}</b></td><td>{e(expect_text(x['expect']))}</td><td>{e(x.get('why'))}</td>"
                        f"<td>{'<span class=\"st ' + e((t or {}).get('update', {}).get('outcomes', {}).get(x['hypothesis'])) + '\">' + e(OUT_LABEL.get((t or {}).get('update', {}).get('outcomes', {}).get(x['hypothesis']), 'not tested')) + '</span>' if t else 'not tested'}</td></tr>"
                        for x in p.get("predictions") or [])
        ass = "".join(f"<li>{e(a)}</li>" for a in (p.get("assumptions") or []) + ((t or {}).get("result", {}).get("assumptions") or []))
        res_q = (t or {}).get("result", {}).get("quantities") or {}
        val = (t or {}).get("value")
        notes = "".join(f"<li>{e(n)}</li>" for n in (t or {}).get("result", {}).get("notes") or [])
        cards.append(f"<article class=card><p class=eyebrow>{e(pid)}{' → ' + e(t['id']) if t else ''} · {e(p['test']['op'])}"
                     f"{' · <span class=warn>post-hoc: does not count</span>' if p.get('post_hoc') else ''}</p><h3>{e(p.get('question'))}</h3>"
                     f"<p class=meta>Predicted before the test (hash <code>{e((p.get('hash') or '')[:12])}</code>). Quantity: <code>{e(p.get('quantity'))}</code></p>"
                     f"<table><tr><th>Hypothesis</th><th>Predicts</th><th>Because</th><th>Result</th></tr>{lines}</table>"
                     + (f"<p class=value>Computed: <b>{e(val)}</b> <span class=kind>{e(KIND_LABEL.get(t.get('value_kind'), t.get('value_kind')))}</span>"
                        f"{' · <b>discriminating</b>' if t['update'].get('discriminating') else ''}</p>" if t else "<p class=meta>Not run.</p>")
                     + (f"<details><summary>Computation arguments and result</summary><pre>{e(json.dumps(p['test']['args'], ensure_ascii=False))}\n{e(json.dumps(res_q, ensure_ascii=False, indent=1)[:3000])}</pre></details>" if t else "")
                     + (f"<p class=meta>Assumptions</p><ul>{ass}</ul>" if ass else "") + (f"<ul class=notes>{notes}</ul>" if notes else "")
                     + evidence_table((t or {}).get("result", {}).get("evidence") or []) + "</article>")
    out.append(f"<section><h2>Predict → Test → Update</h2>{''.join(cards) or '<p>No predictions were registered.</p>'}</section>")
    xs = [en["payload"] for en in entries if en["kind"] == "compute"]
    if xs:
        xr = "".join(f"<tr><td>{e(x['id'])}</td><td>{e(x['op'])}</td><td><code>{e(json.dumps(x['args'], ensure_ascii=False)[:160])}</code></td></tr>" for x in xs)
        out.append(f"<section><h2>Exploratory computations (not tests)</h2><table><tr><th>Id</th><th>Op</th><th>Arguments</th></tr>{xr}</table></section>")
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for r in iq.get("records") or []:
        by_kind.setdefault(r["kind"], []).append(r)
    kinds = "".join(f"<details><summary>{e(KIND_LABEL.get(k, k))} ({len(v)})</summary><ul>"
                    + "".join(f"<li>{e(r['statement'])}{(': <b>' + e(json.dumps(r['value'], ensure_ascii=False)[:200]) + '</b>') if r.get('value') is not None else ''} "
                              f"<span class=meta>[{e(r['source'])}]</span></li>" for r in v[:60]) + "</ul></details>" for k, v in by_kind.items())
    out.append(f"<section><h2>Knowledge by kind</h2>{kinds}</section>")
    na = res.get("next_action") or {}
    oc = "".join(f"<li><b>{e(o.get('label'))}</b>: {e(o.get('means'))}</li>" for o in na.get("outcomes") or [])
    g = mr.get("safety_gate") or {}
    out.append(f"<section><h2>Next action and safety gate</h2><p class=lead>{e(na.get('action'))}</p><p>{e(na.get('why'))}</p><ul>{oc}</ul>"
               f"<p class=meta>Independent safety gate: action {e(g.get('action_verdict'))}, outcome {e(g.get('outcome'))}, "
               f"engineer confirmation required: {e(g.get('requires_confirmation'))}. Rules fired: {e(', '.join(r.get('rule', '') for r in g.get('rules_fired') or []) or 'none')}.</p></section>")
    steps = "".join(f"<li><span class=tool>{e(s.get('tool'))}</span> {e(s.get('label'))}<br><span class=meta>{e(s.get('summary'))}</span></li>" for s in mr.get("steps") or [])
    out.append(f"<section><h2>Every step, in order</h2><ol class=steps>{steps}</ol></section>")
    out.append(f"<footer><p>Ledger: {len(entries)} entries, chain head <code>{e((iq.get('chain_head') or '')[:16])}</code>, "
               f"chain {'verifies' if chain_ok(entries) else '<b>DOES NOT VERIFY</b>'} (recomputed by this page's builder).</p>"
               f"<p>{e(iq.get('rule'))}</p><p>{e(mr.get('boundary'))}</p></footer>")
    css = """:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#fff;--accent:#2f5d8a;--ok:#2e7d4f;--bad:#a8402f;--warn:#9a6a00}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141414;--fg:#ecebe6;--muted:#a3a29b;--line:#2c2b28;--card:#1c1c1a;--accent:#8fb6dc;--ok:#7cc79a;--bad:#e48b7b;--warn:#e0b44f}}
:root[data-theme=dark]{--bg:#141414;--fg:#ecebe6;--muted:#a3a29b;--line:#2c2b28;--card:#1c1c1a;--accent:#8fb6dc;--ok:#7cc79a;--bad:#e48b7b;--warn:#e0b44f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:4px 0 8px}h2{font-size:19px;margin:36px 0 12px;border-top:1px solid var(--line);padding-top:20px}
h3{font-size:16px;margin:4px 0 8px}.eyebrow{text-transform:uppercase;letter-spacing:.06em;font-size:11px;color:var(--muted);margin:0}.meta{color:var(--muted);font-size:13px}
.note{border-left:3px solid var(--accent);padding:8px 12px;background:var(--card)}.lead{font-size:17px;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;display:block;overflow-x:auto}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
td.n{font-variant-numeric:tabular-nums;text-align:right}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.st{font-weight:600}.st.contradicted{color:var(--bad)}.st.consistent,.st.survived{color:var(--ok)}.st.untested,.st.no_result{color:var(--muted)}
.warn{color:var(--warn);font-weight:600}.kind{font-size:12px;color:var(--muted)}.value{font-size:15px}.tag{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:1px 5px;margin-left:6px;color:var(--muted)}
.tiers li{margin:10px 0}.tier{font-weight:700;font-size:16px}.steps li{margin:6px 0}.tool{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--accent)}
code,pre{font-family:ui-monospace,Menlo,monospace;font-size:12px}pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:8px;border-radius:6px}
details{margin:6px 0}summary{cursor:pointer;color:var(--accent)}footer{margin-top:40px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:16px}"""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Veyra Recorded Investigation</title><style>{css}</style></head><body><main>{''.join(out)}</main></body></html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Recorded investigation")
    a = ap.parse_args()
    Path(a.out).expanduser().write_text(build(Path(a.record).expanduser(), a.title), encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
