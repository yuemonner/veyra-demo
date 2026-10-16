"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import InvestigationAnalysis from "../../components/InvestigationAnalysis";

const objects = [
  ["overview", "Overview"], ["timeline", "Timeline"], ["evidence", "Evidence"], ["changes", "Changes"],
  ["fleet", "Fleet"], ["hypotheses", "Possible Causes"], ["checks", "Next Checks"], ["decision", "Decision"],
  ["actions", "Action"], ["outcomes", "Outcomes"], ["precedents", "Precedents"],
];

export default function ProductDemoPage() {
  const [stage, setStage] = useState("fleet");
  const [analysis, setAnalysis] = useState<any>(null);
  const [query, setQuery] = useState("Compare affected machines with EX11");
  const [answer, setAnswer] = useState("");
  const [selected, setSelected] = useState<any>(null);
  const [trace, setTrace] = useState(false);
  const count = (key: string) => !analysis ? "—" : ({fleet: analysis.cohorts.length, hypotheses: analysis.hypotheses.length, checks: analysis.checks.length, evidence: analysis.evidence.length, timeline: analysis.timeline.length} as Record<string, number>)[key] ?? "";
  function ask(e: FormEvent) {
    e.preventDefault(); if (!analysis) return;
    const q = query.toLowerCase();
    if (/decision|option|rollback|dispatch|决定|方案|回滚|派工/.test(q)) {
      setStage("decision"); setAnswer("Compare rollback, records review and field dispatch. Save the human choice and its evidence snapshot before reporting an actual action.");
    } else if (/outcome|recover|action|verify|结果|恢复|执行|验证/.test(q)) {
      setStage("outcomes"); setAnswer("Actual actions and verification updates are recorded separately. Reported recovery is limited to its observation window; recurrence remains part of the record.");
    } else if (/precedent|reuse|history|复用|先例|经验/.test(q)) {
      setStage("precedents"); setAnswer("Other cases are checked for matching, conflicting and unknown conditions. Review exclusions before reusing experience.");
    } else if (/change|config|变更|配置/.test(q)) {
      setStage("changes"); setAnswer("Configuration records describe settings, not actual operating exposure. Inspect the source fields before drawing a conclusion.");
    } else if (/cause|hypoth|原因|假设/.test(q)) {
      setStage("hypotheses"); setAnswer(analysis.comparison_quality.conclusion);
    } else if (/check|next|检查|下一步/.test(q)) {
      setStage("checks"); setAnswer(analysis.next_check?.why || "Review the new evidence and plan independent replication; no further discriminating check is available in this bounded rule set.");
    } else if (/compare|machine|ex11|fleet|对比|机器|对照/.test(q)) {
      setStage("fleet"); setAnswer(`${analysis.configuration_summary.affected_count} machines have reported stops; ${analysis.comparison_quality.valid_control_count} verified controls. EX11 requires comparable exposure and complete healthy telemetry before it can serve as a control.`);
    } else {
      setStage("evidence"); setAnswer("This demo supports case navigation for comparisons, changes, possible causes, checks, decisions, outcomes and reuse. Source records are available here for review.");
    }
  }
  const title = objects.find(([key]) => key === stage)?.[1];
  return <main className="ide-shell restored-workspace">
    <header className="ide-topbar">
      <Link className="ide-brand" href="/veyra"><span>V</span> Veyra</Link>
      <form className="ide-command workspace-ask" onSubmit={ask}>
        <label htmlFor="ask-veyra">⌘ Ask Veyra</label><input id="ask-veyra" value={query} onChange={e => setQuery(e.target.value)} placeholder="Compare machines, review options, inspect evidence…" /><button disabled={!analysis || !query.trim()}>Ask</button>
      </form>
      <div className="ide-source-counts"><b>{analysis ? new Set(analysis.evidence.map((e: any) => e.source)).size : "—"} sources</b><b>{analysis?.hypotheses.length ?? "—"} causes</b></div>
    </header>
    <section className="ide-casebar"><div><span>FIELD CASE</span><span>FC-021</span><h1>Safe-stop cluster</h1></div><b className="open">OPEN</b><b>{analysis?.cohorts.length ?? "—"} MACHINES</b><b>{analysis?.diagnostic_assessment?.root_cause_status === "engineer_reported_confirmed" ? "ROOT CAUSE REPORTED" : "ROOT CAUSE UNRESOLVED"}</b></section>
    <section className="ide-grid">
      <aside className="ide-left"><div className="ide-left-title">Case objects</div><nav aria-label="Case objects">{objects.map(([key,label], index) => <div key={key}>{(index === 0 || index === 5 || index === 7) && <div className="workspace-nav-group">{index === 0 ? "UNDERSTAND" : index === 5 ? "INVESTIGATE" : "ACT & LEARN"}</div>}<button className={stage === key ? "active" : ""} aria-current={stage === key ? "page" : undefined} onClick={() => setStage(key)}><span>{label}</span><b>{count(key)}</b></button></div>)}</nav>
        <details className="ide-mini-status"><summary>Cohort context</summary><div className="cohort-group"><label>Affected</label><p>{analysis?.cohorts.filter((c: any) => c.signal_observed).map((c: any) => c.asset_id).join(" · ") || "Loading…"}</p></div><div className="cohort-group"><label>Candidate control</label><p>EX11 · Exposure requires verification</p></div></details>
      </aside>
      <section className="ide-center" aria-label={`${title} workspace`}>
        {answer && <div className="workspace-answer" role="status"><div><strong>Veyra · case guidance</strong><button aria-label="Dismiss answer" onClick={() => setAnswer("")}>×</button></div><p>{answer}</p><small>Rule-based navigation and current case data · no LLM inference</small></div>}
        <InvestigationAnalysis stage={stage} onNavigate={setStage} onAnalysis={setAnalysis} />
      </section>
      <aside className="ide-right" aria-label="Case context">
        <section className="reasoning-panel"><span>Current evidence</span><h3>Keep uncertainty visible</h3><p>{analysis?.comparison_quality.conclusion || "Connecting to the case engine…"}</p><div className="workspace-side-stat"><b>{analysis?.comparison_quality.valid_control_count ?? "—"}</b><span>verified controls</span></div></section>
        <section className="next-gap-panel"><span>Next evidence gap</span><h3>{analysis?.next_check ? "EX11 operating exposure" : "Review recorded evidence"}</h3><p>{analysis?.next_check ? "Confirm comparable conditions and a complete observation window." : "Plan the next independent check with the team."}</p><button onClick={() => setStage("checks")}>Open next check</button></section>
        {selected && <section className="reason-detail"><span>Selected event</span><h3>{selected.asset_id} · {selected.label}</h3><p>Event {selected.event_time?.slice(11,19)} UTC<br />Known {selected.known_at?.slice(11,19)} UTC</p><small>{selected.source}</small></section>}
        <section className="provenance-panel"><button className="trace-button" onClick={() => setTrace(!trace)}>{trace ? "Hide source references" : "Inspect source references"}</button>{trace && <div className="trace-list">{analysis?.timeline.map((e: any) => <button key={e.id} onClick={() => {setSelected(e); setStage("evidence");}}>{e.asset_id} · {e.label}<small>{e.source}</small></button>)}</div>}</section>
      </aside>
    </section>
    <footer className="ide-bottom"><div className="ide-event-rail">{analysis?.timeline.filter((e: any) => e.event_type === "telemetry_anomaly" || e.event_type === "deployment" || e.event_type === "human_discovery").map((e: any) => <button key={e.id} className={`${selected?.id === e.id ? "active" : ""} ${e.event_type === "telemetry_anomaly" ? "stop-event" : ""}`} onClick={() => {setSelected(e); setStage("timeline");}}><b>{e.event_time?.slice(11,16)}</b><span>{e.asset_id} · {e.label}</span></button>)}</div></footer>
  </main>;
}
