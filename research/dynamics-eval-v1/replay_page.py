"""Build a self-contained HTML replay of ONE recorded Veyra investigation, generated only from that run's record.

Everything on the page comes from the record (the agent's report, its tool trace, the inquiry ledger, the tests Veyra
generated and ran, their computed values and source rows). Nothing is added after the run: no team analysis, no
probabilities, no interpretation that the record does not contain. What the record does not establish is shown as the
record states it (unknowns, open distinctions, declarations).

  python research/dynamics-eval-v1/replay_page.py RECORD.json OUT.html [--title "..."]

A customer's record stays private: write OUT.html to a private folder and never publish it.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List

E = html.escape

CSS = """
:root{--bg:#f6f7f4;--panel:#ffffff;--ink:#1d2321;--muted:#5d6a66;--line:#d9dfdc;--accent:#0f6b5c;--warn:#9a5b00;--bad:#a23a2a;--chip:#eef3f1}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121615;--panel:#1a201e;--ink:#e5ece9;--muted:#9aa8a3;--line:#2c3532;--accent:#5fc4ad;--warn:#e0a84b;--bad:#ef8a78;--chip:#222a28;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121615;--panel:#1a201e;--ink:#e5ece9;--muted:#9aa8a3;--line:#2c3532;--accent:#5fc4ad;--warn:#e0a84b;--bad:#ef8a78;--chip:#222a28;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "IBM Plex Sans","Noto Sans SC",system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px max(16px,env(safe-area-inset-left,0px)) 64px}
h1{font-size:26px;margin:0 0 6px;text-wrap:balance}h2{font-size:19px;margin:34px 0 10px;padding-top:14px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 6px}.muted{color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:10px 0}
.tag{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:4px;padding:1px 7px;font-size:12px;margin-right:4px}
.ok{color:var(--accent)}.no{color:var(--bad)}.cond{color:var(--warn)}table{border-collapse:collapse;width:100%;font-size:13.5px}
td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
.scroll{overflow-x:auto}code,.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12.5px}
.banner{border-left:4px solid var(--accent);padding:10px 14px;background:var(--panel);border-radius:4px}
details summary{cursor:pointer;color:var(--muted)}
"""


def num(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return E(str(v))


def outcome_cls(o: str) -> str:
    return {"consistent": "ok", "contradicted": "no", "contradicted_if_assumed": "cond"}.get(o, "muted")


def page(rec: Dict[str, Any], title: str) -> str:
    mr = rec.get("main_investigator_record") or {}
    res = mr.get("result") or {}
    iq = mr.get("inquiry") or {}
    usage = mr.get("usage") or {}
    H: List[str] = []
    H.append(f"<h1>{E(title)}</h1>")
    H.append("<p class='muted'>Generated only from one recorded run of Veyra's investigator. Nothing on this page was written after the run. "
             "本页内容全部来自一次自主运行的记录，运行结束后没有添加任何人工分析。</p>")
    H.append("<div class='banner'>"
             f"<span class='tag'>mode: {E(str(mr.get('mode')))}</span><span class='tag'>model: {E(', '.join(usage.get('model_ids') or [str(mr.get('model'))]))}</span>"
             f"<span class='tag'>code: {E(str(rec.get('commit', ''))[:9])}</span><span class='tag'>{E(str(usage.get('model_calls')))} model calls</span>"
             f"<span class='tag'>{len(mr.get('steps') or [])} tool calls</span><span class='tag'>{E(str(rec.get('seconds')))} s</span>"
             f"<span class='tag'>recorded {E(str(mr.get('recorded_at', ''))[:19])}</span>"
             + (f"<div class='muted' style='margin-top:6px'>Data sent to the model under: {E(rec['authorized_by'])}</div>" if rec.get("authorized_by") else "")
             + "</div>")

    # 1. the agent's conclusion, verbatim
    H.append("<h2>1. What the investigator concluded · 调查结论（原文）</h2>")
    H.append(f"<div class='panel'><b>{E(res.get('headline', ''))}</b><p>{E(res.get('explanation', ''))}</p>"
             f"<div class='muted'>status: {E(str(res.get('status')))} · confidence: {E(str(res.get('confidence')))}</div></div>")
    if res.get("ranking"):
        H.append("<h3>Ranking of causes, by executed tests · 依据已执行检验的原因排序</h3><div class='scroll'><table><tr><th>tier</th><th>causes</th><th>tests</th><th>basis</th></tr>")
        for i, t in enumerate(res["ranking"], 1):
            H.append(f"<tr><td>{i}</td><td>{E(', '.join(t.get('hypotheses') or []))}{' <span class=tag>may act together</span>' if t.get('coexist') else ''}</td>"
                     f"<td class='mono'>{E(', '.join(t.get('tests') or []))}</td><td>{E(t.get('basis', ''))}</td></tr>")
        H.append("</table></div>")
    hyps = {h["id"]: h for h in iq.get("hypotheses") or []}
    if hyps:
        H.append("<h3>Competing hypotheses in the ledger · 账本中的竞争假设</h3><div class='scroll'><table><tr><th>id</th><th>statement</th><th>source</th><th>standing</th><th>tests</th></tr>")
        for h in hyps.values():
            st = h.get("standing", "")
            H.append(f"<tr><td class='mono'>{E(h['id'])}</td><td>{E(h.get('statement', ''))}</td><td>{E(h.get('source', ''))}</td>"
                     f"<td class='{ {'contradicted': 'no', 'survived': 'ok'}.get(st, 'muted') }'>{E(st)}</td>"
                     f"<td class='mono'>{E(', '.join(h.get('consistent_with') or []))}{' / ✗ ' + E(', '.join(h.get('contradicted_by') or [])) if h.get('contradicted_by') else ''}</td></tr>")
        H.append("</table></div>")
    nx = res.get("next_action") or {}
    H.append("<h3>Next check it asked for · 建议的下一步检查</h3>"
             f"<div class='panel'><b>{E(nx.get('action', ''))}</b><p>{E(nx.get('why', ''))}</p>"
             + ("<ul>" + "".join(f"<li>{E(x)}</li>" for x in nx.get("how") or []) + "</ul>" if nx.get("how") else "")
             + "".join(f"<div class='muted'>if <b>{E(o.get('label', ''))}</b>: {E(o.get('means', ''))}</div>" for o in nx.get("outcomes") or []) + "</div>")

    # 2. the tests: generated by Veyra vs registered by the agent
    tests = {t["id"]: t for t in iq.get("tests") or []}
    preds = {p["id"]: p for p in iq.get("predictions") or []}
    full = {e["payload"]["id"]: e["payload"] for e in iq.get("entries") or [] if e["kind"] == "test"}
    H.append("<h2>2. Predictions registered before the tests ran · 先登记预测，再运行检验</h2>")
    H.append("<p class='muted'>Each prediction was hash-chained into the ledger before its computation ran. "
             "<span class='tag'>Veyra generated</span> tests come from the physics of the mechanism classes, not from the model; "
             "<span class='tag'>agent</span> tests were registered by the investigator.</p>")
    for tid, t in tests.items():
        p = preds.get(t["prediction_id"], {})
        res_q = (full.get(tid, {}).get("result") or {}).get("quantities") or {}
        upd = t.get("update") or {}
        src = "Veyra generated" if p.get("source") == "veyra_generated" else "agent"
        H.append(f"<div class='panel'><span class='tag'>{E(tid)}</span><span class='tag'>{src}</span>{' <span class=tag>post-hoc: does not count</span>' if not upd.get('counts') else ''}"
                 f"<b> {E(p.get('question', ''))}</b><div class='mono muted'>{E((p.get('test') or {}).get('op', ''))} → {E(t.get('quantity', ''))} = {num(t.get('value'))} "
                 f"({E(str(t.get('value_kind')))})</div><table><tr><th>hypothesis</th><th>predicted</th><th>why</th><th>outcome</th></tr>")
        for pr in p.get("predictions") or []:
            o = (upd.get("outcomes") or {}).get(pr["hypothesis"], "")
            H.append(f"<tr><td class='mono'>{E(pr['hypothesis'])}</td><td class='mono'>{E(json.dumps(pr.get('expect')))}</td><td>{E(pr.get('why', ''))}</td>"
                     f"<td class='{outcome_cls(o)}'>{E(o)}</td></tr>")
        H.append("</table>")
        if res_q and (p.get("test") or {}).get("op") == "divergence":
            keys = ["limiting_member", "far_member", "spread_baseline_median", "spread_late_median", "spread_rate_baseline", "spread_rate_late", "spread_rate_max",
                    "spread_rate_max_t", "common_share", "differential_share", "diff_departure_z", "onset_t", "first_significant_t", "lead_s", "ordering",
                    "far_departure_z", "load_coupling_change_z", "jump_excess_z", "identity_stable", "skew_qualified", "baseline_robust"]
            H.append("<details><summary>computed quantities of this divergence computation</summary><table>"
                     + "".join(f"<tr><td class='mono'>{E(k)}</td><td class='mono'>{num(res_q.get(k))}</td></tr>" for k in keys if k in res_q) + "</table></details>")
        ev = full.get(tid, {}).get("result", {}).get("evidence") or []
        if ev:
            H.append("<details><summary>source rows</summary><div class='mono'>" + "<br>".join(
                E(f"{e.get('file')} line {e.get('row')} · t={e.get('t')} · {e.get('field')} = {e.get('value')}") for e in ev[:12]) + "</div></details>")
        H.append("</div>")

    # 3. what the agent computed to explore
    comp = [e["payload"] for e in iq.get("entries") or [] if e["kind"] == "compute"]
    if comp:
        H.append("<h2>3. Computations the investigator chose to run · 调查员自主选择的计算</h2><div class='scroll'><table><tr><th>id</th><th>op</th><th>arguments</th><th>selected quantities</th></tr>")
        for c in comp:
            q = c.get("quantities") or {}
            sel = {k: v for k, v in q.items() if not isinstance(v, (list, dict))}
            H.append(f"<tr><td class='mono'>{E(c['id'])}</td><td class='mono'>{E(c['op'])}</td><td class='mono'>{E(json.dumps(c.get('args'))[:220])}</td>"
                     f"<td class='mono'>{E(', '.join(f'{k}={num(v)}' for k, v in list(sel.items())[:10]))}</td></tr>")
        H.append("</table></div>")

    # 4. what stays open
    H.append("<h2>4. What this run did not establish · 本次运行未能确定的内容</h2><div class='panel'><ul>")
    for d in iq.get("distinctions") or []:
        if d.get("state") in ("open", "tested_not_separated", "declared_not_discriminable"):
            dec = d.get("declaration") or {}
            H.append(f"<li><span class='mono'>{E(' vs '.join(d['pair']))}</span>: {E(d['state'])}"
                     + (f" — {E(dec.get('reason', ''))} <i>Would separate:</i> {E(dec.get('would_separate', ''))}" if dec else "") + "</li>")
    for u in res.get("unknowns") or []:
        H.append(f"<li>{E(u)}</li>")
    H.append("</ul></div>")

    # 5. full trace
    H.append("<h2>5. Full tool trace · 完整工具调用记录</h2><div class='scroll'><table><tr><th>#</th><th>tool</th><th>input</th><th>summary</th></tr>")
    for i, s in enumerate(mr.get("steps") or [], 1):
        H.append(f"<tr><td>{i}</td><td class='mono'>{E(s.get('tool', ''))}</td><td class='mono'>{E(json.dumps(s.get('input'))[:200])}</td><td>{E(str(s.get('summary', ''))[:200])}</td></tr>")
    H.append("</table></div>")
    H.append(f"<p class='muted'>Ledger chain head <span class='mono'>{E(str(iq.get('chain_head', ''))[:16])}…</span>. Safety Gate ran independently of the "
             "investigator. This page is a replay, not a safety clearance.</p>")
    return (f"<title>{E(title)}</title><meta name='viewport' content='width=device-width, initial-scale=1'><style>{CSS}</style><main>" + "".join(H) + "</main>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record")
    ap.add_argument("out")
    ap.add_argument("--title", default="Veyra investigation replay")
    a = ap.parse_args()
    rec = json.loads(Path(a.record).read_text(encoding="utf-8"))
    Path(a.out).write_text("<!doctype html><meta charset='utf-8'>" + page(rec, a.title), encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
