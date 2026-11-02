// The case as one self-contained HTML file: readable by an engineer in any browser, and by an AI assistant through
// the JSON block embedded in it. Plots are inline SVG; nothing is fetched when the file is opened.

const esc = (s: string | number) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
export type Tbl = { cols: string[]; rows: (string | number)[][] };
export type Discussed = { at: string; who: "engineer" | "veyra"; text: string; verdict?: string; title?: string; points?: string[]; table?: Tbl; lines?: number[]; source?: string };
export type Feedback = { id: string; area: string; verdict: "confirmed" | "ruled_out" | "unsure"; note: string; at: string };
export type ReportInput = {
  title: string; caseId: string; url?: string; provenance?: string; synthetic?: boolean;
  status?: string; headline?: string; explanation?: string; confidence?: string;
  hypotheses?: Any[]; nextAction?: Any; evidence?: Any[]; checked?: Any[]; effects?: Any[]; unknowns?: string[];
  steps?: Any[]; investigator?: string;
  data?: { title: string; svg?: string; caption?: string; table?: Tbl }[];
  normal?: { title: string; intro?: string; table: Tbl }[];
  discussion?: Discussed[]; feedback?: Feedback[];
  log?: string[];
};

const STATUS: Record<string, string> = { leading: "Leading", open: "Open", weakened: "Weakened", ruled_out: "Ruled out" };
const VERDICT: Record<string, string> = { supports: "Data agrees", against: "Data disagrees", cannot_tell: "Data can't tell", facts: "From the data",
  confirmed: "Engineer confirmed", ruled_out: "Engineer ruled out", unsure: "Engineer unsure" };

const table = (t?: Tbl) => !t || !t.rows.length ? "" : `<div class="tw"><table><thead><tr>${t.cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
  t.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;

export function reportFileName(title: string, caseId: string) {
  return `veyra-${(title || caseId).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || caseId}.html`;
}

export function buildReport(r: ReportInput): string {
  const now = new Date().toISOString();
  const fb = new Map((r.feedback || []).map((f) => [f.id, f]));
  const machine = {
    schema: "veyra-case-report-v1", generated_at: now, case_id: r.caseId, title: r.title, synthetic: !!r.synthetic, source: r.provenance || null,
    conclusion: { status: r.status || null, headline: r.headline || null, explanation: r.explanation || null, confidence: r.confidence || null },
    hypotheses: (r.hypotheses || []).map((h) => ({ id: h.id, area: h.area, status: h.status, claim: h.claim, because: h.because, lines: h.lines || [],
      engineer_feedback: fb.get(h.id) ? { verdict: fb.get(h.id)!.verdict, note: fb.get(h.id)!.note, at: fb.get(h.id)!.at } : null })),
    next_action: r.nextAction || null, evidence: r.evidence || [], checked: r.checked || [], effects: r.effects || [], unknowns: r.unknowns || [],
    data_tables: (r.data || []).filter((d) => d.table).map((d) => ({ title: d.title, ...d.table })),
    normal_behaviour: (r.normal || []).map((n) => ({ title: n.title, ...n.table })),
    discussion: r.discussion || [], log_lines: r.log || [],
  };
  const hyps = (r.hypotheses || []).map((h) => {
    const f = fb.get(h.id);
    return `<tr class="h-${esc(h.status)}"><td><span class="pill p-${esc(h.status)}">${esc(STATUS[h.status] || h.status)}</span></td><td><b>${esc(h.area)}</b><br><span class="muted">${esc(h.claim || "")}</span></td>
      <td>${esc(h.because || "")}${(h.lines || []).length ? ` <span class="muted">· lines ${esc((h.lines || []).join(", "))}</span>` : ""}</td>
      <td>${f ? `<b>${esc(VERDICT[f.verdict])}</b>${f.note ? `: ${esc(f.note)}` : ""}` : `<span class="muted">—</span>`}</td></tr>`;
  }).join("");
  const na = r.nextAction;
  const disc = (r.discussion || []).map((d) => d.who === "engineer"
    ? `<div class="q"><span class="who">Engineer</span><p>${esc(d.text)}</p></div>`
    : `<div class="a"><span class="who">Veyra${d.source ? ` · ${esc(d.source)}` : ""}</span>${d.verdict ? `<span class="pill v-${esc(d.verdict)}">${esc(VERDICT[d.verdict] || d.verdict)}</span>` : ""}
       ${d.title ? `<h4>${esc(d.title)}</h4>` : ""}<p>${esc(d.text)}</p>${(d.points || []).length ? `<ul>${d.points!.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
       ${table(d.table)}${(d.lines || []).length ? `<p class="muted">Lines: ${esc(d.lines!.join(", "))}</p>` : ""}</div>`).join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veyra · ${esc(r.title)}</title>
<meta name="description" content="Veyra case report: ${esc(r.headline || r.title)}">
<style>
:root{--ink:#17183a;--ink2:#4a4c6a;--ink3:#8a8ca3;--line:#e4e5ef;--soft:#f6f6fb;--thread:#4c47d6;--fault:#c2304a;--ok:#10806a;--amber:#b06a00}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 20px 64px}h1{font-size:26px;line-height:1.15;letter-spacing:-.02em;margin:6px 0 10px}
h2{font-size:18px;margin:36px 0 10px;padding-top:14px;border-top:1px solid var(--line)}h3{font-size:15px;margin:18px 0 6px}h4{margin:6px 0 2px;font-size:14px}
p{margin:6px 0;max-width:75ch}.muted{color:var(--ink3)}.kicker{font-size:13px;color:var(--thread);font-weight:650;margin:0}
.lead{font-size:16px;color:var(--ink2)}.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}th{background:var(--soft);font-weight:600}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.pill{display:inline-block;border-radius:99px;padding:1px 9px;font-size:12px;font-weight:650;border:1px solid var(--line);background:var(--soft);white-space:nowrap}
.p-leading{color:var(--thread);border-color:#c9c7f3}.p-ruled_out{color:var(--ink3);text-decoration:line-through}.p-weakened{color:var(--ink2)}
.v-supports{color:var(--ok)}.v-against{color:var(--fault)}.v-cannot_tell{color:var(--amber)}
.box{border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0;background:var(--soft)}
figure{margin:10px 0}figure svg{width:100%;height:auto;display:block}figcaption{font-size:12.5px;color:var(--ink3)}
.q,.a{border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:12px 0}.a{border-color:var(--thread)}.who{font-size:12px;color:var(--ink3);display:block}
ol.log{font-family:ui-monospace,Menlo,monospace;font-size:12px;padding-left:44px;white-space:pre-wrap;word-break:break-all}
.ai{font-size:13px;color:var(--ink2)}@media print{h2{break-after:avoid}.tw{overflow:visible}}
</style></head><body><main>
<p class="kicker">Veyra case report${r.synthetic ? " · SYNTHETIC data" : ""}</p>
<h1>${esc(r.headline || r.title)}</h1>
<p class="muted">${esc(r.title)} · case ${esc(r.caseId)} · written ${esc(now.replace("T", " ").slice(0, 16))} UTC${r.url ? ` · <a href="${esc(r.url)}">open the live case</a>` : ""}</p>
${r.explanation ? `<p class="lead">${esc(r.explanation)}</p>` : ""}
${r.status ? `<p><span class="pill">${esc(r.status)}${r.confidence ? ` · ${esc(r.confidence)} confidence` : ""}</span>${r.investigator ? ` <span class="muted">${esc(r.investigator)}</span>` : ""}</p>` : ""}
${r.provenance ? `<p class="muted">Source: ${esc(r.provenance)}</p>` : ""}

${hyps ? `<h2>Where the fault can be</h2><div class="tw"><table><thead><tr><th>Status</th><th>Area</th><th>Why</th><th>Engineer's check</th></tr></thead><tbody>${hyps}</tbody></table></div>` : ""}

${na ? `<h2>Next action${na.where ? ` · ${esc(na.where.replace("_", " "))}` : ""}</h2><div class="box"><h3 style="margin-top:0">${esc(na.action)}</h3>${na.why ? `<p>${esc(na.why)}</p>` : ""}
${(na.how || []).length ? `<ol>${na.how.map((h: string) => `<li>${esc(h)}</li>`).join("")}</ol>` : ""}
${(na.outcomes || []).length ? table({ cols: ["If you find", "It means", "Then"], rows: na.outcomes.map((o: Any) => [o.label, o.means || "", o.then || ""]) }) : ""}</div>` : ""}

${(r.data || []).length ? `<h2>What the data shows</h2>${r.data!.map((d) => `<h3>${esc(d.title)}</h3>${d.svg ? `<figure>${d.svg}${d.caption ? `<figcaption>${esc(d.caption)}</figcaption>` : ""}</figure>` : d.caption ? `<p class="muted">${esc(d.caption)}</p>` : ""}${table(d.table)}`).join("")}` : ""}

${(r.normal || []).length ? `<h2>Normal behaviour, against this case</h2>${r.normal!.map((n) => `<h3>${esc(n.title)}</h3>${n.intro ? `<p class="muted">${esc(n.intro)}</p>` : ""}${table(n.table)}`).join("")}` : ""}

${(r.evidence || []).length ? `<h2>Why Veyra thinks so</h2>${table({ cols: ["Line", "Quoted from the data", "What it shows"], rows: r.evidence!.map((e: Any) => [e.line, e.quote, e.shows]) })}` : ""}
${(r.checked || []).length ? `<h2>Already checked</h2>${table({ cols: ["Check", "Result", "Verdict"], rows: r.checked!.map((c: Any) => [c.check, c.result, `${String(c.verdict || "").replace("_", " ")}${c.about ? `: ${c.about}` : ""}`]) })}` : ""}
${(r.effects || []).length ? `<h3>Effects, not causes</h3>${table({ cols: ["Line", "Quoted", "Why it is an effect"], rows: r.effects!.map((e: Any) => [e.line, e.quote, e.why]) })}` : ""}
${(r.unknowns || []).length ? `<h3>Not established by the data</h3><ul>${r.unknowns!.map((u) => `<li>${esc(u)}</li>`).join("")}</ul>` : ""}

${disc ? `<h2>Discussion</h2><p class="muted">Questions and proposed causes from the engineer, each checked against the data. Engineer feedback is recorded as what the engineer reported, not as verified fact.</p>${disc}` : ""}

${(r.steps || []).length ? `<h2>How Veyra investigated</h2><ol>${r.steps!.map((s: Any) => `<li>${esc(s.label)}${s.summary ? ` <span class="muted">· ${esc(s.summary)}</span>` : ""}</li>`).join("")}</ol>` : ""}

${(r.log || []).length ? `<h2>The data, line by line (${r.log!.length} lines)</h2><ol class="log">${r.log!.map((l) => `<li>${esc(l)}</li>`).join("")}</ol>` : ""}

<h2>For AI assistants</h2>
<p class="ai">This file embeds the whole case as JSON in the element <code>#veyra-case</code> (schema <code>veyra-case-report-v1</code>): the conclusion, every hypothesis with its status and the engineer's feedback, the evidence with line numbers, the tables behind each plot, the discussion, and the original data lines. Cite line numbers when you reason about it. Engineer feedback is reported, not verified.</p>
<script type="application/json" id="veyra-case">${JSON.stringify(machine).replace(/</g, "\\u003c")}</script>
<p class="muted">Written by Veyra in the browser from the case data. No AI model was involved in writing this file.</p>
</main></body></html>`;
}
