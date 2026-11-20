"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import RuntimeWorkspace from "../../components/RuntimeWorkspace";
import InvestigationAnalysis from "../../components/InvestigationAnalysis";

const objects = [
  ["overview", "Overview"], ["timeline", "Timeline"], ["evidence", "Evidence"], ["changes", "Changes"],
  ["fleet", "Fleet"], ["hypotheses", "Possible Causes"], ["checks", "Next Checks"], ["decision", "Decision"],
  ["actions", "Action"], ["outcomes", "Outcomes"], ["precedents", "Precedents"],
];

export default function ProductDemoPage() {
  const runtimeEnabled = process.env.NEXT_PUBLIC_ENABLE_RUNTIME === "true";
  // With the local engine running, the field case page is the product; this workspace stays reachable with ?legacy=1.
  // The static public demo (no runtime) is unchanged.
  useEffect(() => {
    if (runtimeEnabled && !new URLSearchParams(window.location.search).has("legacy")) window.location.replace("/field-case");
  }, [runtimeEnabled]);
  const [template, setTemplate] = useState(runtimeEnabled ? "industrial" : "fleet");
  const executable = template === "cerberus" || template === "industrial";
  const remote = template === "remote";
  const [stage, setStage] = useState(runtimeEnabled ? "overview" : "fleet");
  const [analysis, setAnalysis] = useState<any>(null);
  const [query, setQuery] = useState(runtimeEnabled ? "Review changes or find the next check" : "Compare affected machines with EX11");
  const [answer, setAnswer] = useState("");
  const [selected, setSelected] = useState<any>(null);
  const [trace, setTrace] = useState(false);
  const count = (key: string) => !analysis ? "—" : ({fleet: analysis.cohorts.length, hypotheses: remote ? analysis.automatic_analysis?.domains.length || 0 : analysis.hypotheses.length, checks: analysis.checks.length, evidence: analysis.evidence.length, timeline: analysis.timeline.length} as Record<string, number>)[key] ?? "";
  function ask(e: FormEvent) {
    e.preventDefault(); if (!analysis) return;
    const q = query.toLowerCase();
    if (executable) { const target = /check|检查/.test(q)?"checks":/cause|hypoth|原因|假设/.test(q)?"hypotheses":/trace|timeline|过程/.test(q)?"timeline":/decision|决定/.test(q)?"decision":/outcome|结果/.test(q)?"outcomes":/memory|经验/.test(q)?"precedents":"overview";setStage(target);setAnswer(template==="industrial"?"Opened the matching workspace. Navigation uses recorded evidence; no unverified diagnosis is generated.":"Use Run Investigation to execute the allowlisted analytical tools on imported data."); return; }
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
      setStage("checks"); setAnswer(remote ? "Import source logs to detect defined anomalies and rank evidence-backed checks. No fault domain is excluded by missing alerts." : analysis.next_check?.why || "Review the new evidence and plan independent replication; no further discriminating check is available in this bounded rule set.");
    } else if (/compare|machine|ex11|fleet|对比|机器|对照/.test(q)) {
      setStage("fleet"); setAnswer(remote ? "This is a single-machine support case. Review its measured evidence; no fleet comparator is inferred." : `${analysis.configuration_summary.affected_count} machines have reported stops; ${analysis.comparison_quality.valid_control_count} verified controls. EX11 requires comparable exposure and complete healthy telemetry before it can serve as a control.`);
    } else {
      setStage("evidence"); setAnswer("This demo supports case navigation for comparisons, changes, possible causes, checks, decisions, outcomes and reuse. Source records are available here for review.");
    }
  }
  const title = objects.find(([key]) => key === stage)?.[1];
  return <main className={"ide-shell restored-workspace"+(template==="industrial"?" support-shell":"")}>
    <header className="ide-topbar">
      <Link className="ide-brand" href="/veyra"><span>V</span> Veyra</Link>

      <form className="ide-command workspace-ask" onSubmit={ask}>
        <label htmlFor="ask-veyra">⌘ Ask Veyra</label><input id="ask-veyra" value={query} onChange={e => setQuery(e.target.value)} placeholder="Compare machines, review options, inspect evidence…" /><button disabled={!analysis || !query.trim()}>Ask</button>
      </form>
      <Link href="/field-case" className="field-case-link">Field case</Link><div className="ide-source-counts"><b>{analysis ? new Set(analysis.evidence.map((e: any) => e.source)).size : "—"} sources</b><b>{remote ? analysis?.automatic_analysis?.domains.length ?? "—" : analysis?.hypotheses.length ?? "—"} {remote ? "domains" : executable ? "hypotheses" : "causes"}</b></div>
    </header>
    <section className="ide-casebar"><div><span>Field case</span><span>{executable ? template === "cerberus" ? "Public data · local eval" : "Industrial adapter" : remote ? "Synthetic test" : "FC-021"}</span><h1>{executable ? analysis?.title || "Executable investigation" : remote ? "Remote control delay" : "Safe-stop cluster"}</h1></div><b className="open">{executable ? analysis?.runtime_status || "Local" : "Open"}</b><b>{analysis?.cohorts.length ?? "—"} MACHINES</b><b>{analysis?.diagnostic_assessment?.root_cause_status === "engineer_reported_confirmed" ? "Root cause reported" : "Root cause unresolved"}</b></section>
    <section className="ide-grid">
      <aside className="ide-left"><div className="ide-left-title">{template==="industrial"?"Field cases":"Case objects"}</div><label className="workspace-case-picker">Case <select aria-label="Case template" value={template} onChange={e=>{setTemplate(e.target.value);setAnalysis(null);setAnswer("");setSelected(null);setStage(["cerberus","industrial"].includes(e.target.value)?"overview":e.target.value==="remote"?"evidence":"fleet");setQuery(e.target.value==="cerberus"?"Investigate the available trajectory evidence":e.target.value==="industrial"?"Review imported signals and next checks":e.target.value==="remote"?"Review evidence and next checks":"Compare affected machines with EX11");}}>{runtimeEnabled&&<><option value="cerberus">CERBERUS · Real public trajectories</option><option value="industrial">Industrial · CSV / JSON</option></>}<option value="fleet">Fleet Incident · FC-021</option><option value="remote">Remote Support · Synthetic test</option></select></label><nav aria-label="Case objects">{objects.map(([key,label], index) => <div key={key}>{(index === 0 || index === 5 || index === 7) && <div className="workspace-nav-group">{template==="industrial"?(index===0?"Understand":index===5?"Investigate":"Act & learn"):index === 0 ? "Understand" : index === 5 ? "Investigate" : "Act & learn"}</div>}<button className={stage === key ? "active" : ""} aria-current={stage === key ? "page" : undefined} onClick={() => setStage(key)}><span>{template==="industrial"?({overview:"Workspace",timeline:"Timeline",evidence:"Log files",changes:"Machine context",fleet:"Data comparison",hypotheses:"Fault domains",checks:"Next checks",decision:"Decision & dispatch",actions:"Actions",outcomes:"Verification",precedents:"Experience"} as Record<string,string>)[key]:label}</span><b>{count(key)}</b></button></div>)}</nav>
        <details className="ide-mini-status"><summary>Cohort context</summary><div className="cohort-group"><label>Affected</label><p>{analysis?.cohorts.filter((c: any) => c.signal_observed).map((c: any) => c.asset_id).join(" · ") || "Loading…"}</p></div><div className="cohort-group"><label>Candidate control</label><p>{executable ? "Explicit source and baseline comparison" : remote ? "Not applicable · single-machine support" : "EX11 · Exposure requires verification"}</p></div></details>
      </aside>
      <section className="ide-center" aria-label={`${title} workspace`}>
        {answer && <div className="workspace-answer" role="status"><div><strong>Veyra · case guidance</strong><button aria-label="Dismiss answer" onClick={() => setAnswer("")}>×</button></div><p>{answer}</p><small>Rule-based navigation and current case data · no LLM inference</small></div>}
        {executable ? <RuntimeWorkspace key={template} kind={template as "cerberus"|"industrial"} stage={stage} onNavigate={setStage} onAnalysis={setAnalysis}/> : <InvestigationAnalysis key={template} id={remote ? "remote-support" : undefined} stage={stage} onNavigate={setStage} onAnalysis={setAnalysis} />}
      </section>
      <aside className="ide-right" aria-label="Case context">
        <section className="reasoning-panel"><span>Current evidence</span><h3>Keep uncertainty visible</h3><p>{analysis?.comparison_quality.conclusion || "Connecting to the case engine…"}</p><div className="workspace-side-stat"><b>{template==="industrial"?analysis?.industrial_support?.domains.length??"—":analysis?.comparison_quality.valid_control_count??"—"}</b><span>{template==="industrial"?"open fault domains":"verified controls"}</span></div></section>
        <section className="next-gap-panel"><span>Next evidence gap</span><h3>{executable ? template==="industrial" ? analysis?.checks?.[0]?.question||"Machine context & next useful check" : "Evidence, alignment & missing signals" : remote ? "Endpoint records & symptom window" : analysis?.next_check ? "EX11 operating exposure" : "Review recorded evidence"}</h3><p>{executable&&template==="industrial"?analysis?.checks?.[0]?.why||"Record the machine context and run an investigation.":analysis?.next_check ? "Confirm comparable conditions and a complete observation window." : "Plan the next independent check with the team."}</p><button onClick={() => setStage("checks")}>Open next check</button></section>
        {selected && <section className="reason-detail"><span>Selected event</span><h3>{selected.asset_id} · {selected.label}</h3><p>Event {selected.event_time?.slice(11,19)} UTC<br />Known {selected.known_at?.slice(11,19)} UTC</p><small>{selected.source}</small></section>}
        <section className="provenance-panel"><button className="trace-button" onClick={() => setTrace(!trace)}>{trace ? "Hide source references" : "Inspect source references"}</button>{trace && <div className="trace-list">{analysis?.timeline.map((e: any) => <button key={e.id} onClick={() => {setSelected(e); setStage("evidence");}}>{e.asset_id} · {e.label}<small>{e.source}</small></button>)}</div>}</section>
      </aside>
    </section>
    <footer className="ide-bottom"><div className="ide-event-rail">{analysis?.timeline.filter((e: any) => e.event_type === "telemetry_anomaly" || e.event_type === "deployment" || e.event_type === "human_discovery").map((e: any) => <button key={e.id} className={`${selected?.id === e.id ? "active" : ""} ${e.event_type === "telemetry_anomaly" ? "stop-event" : ""}`} onClick={() => {setSelected(e); setStage("timeline");}}><b>{e.event_time?.slice(11,16)}</b><span>{e.asset_id} · {e.label}</span></button>)}</div></footer>
  </main>;
}
