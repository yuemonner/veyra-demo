"use client";

import { useEffect, useState } from "react";
import { API } from "../lib/api";
import DecisionWorkspace from "./DecisionWorkspace";

const DEMO = "inv-120-robots-bad-rollout";
export default function InvestigationAnalysis({ stage, id = DEMO, onNavigate, onAnalysis }: { stage: string; id?: string; onNavigate?: (stage: string) => void; onAnalysis?: (analysis: any) => void }) {
  const [analysis, setAnalysis] = useState<any>(null);
  const [caseId, setCaseId] = useState(id);
  const [workflow, setWorkflow] = useState<any>(null);
  const [workflowError, setWorkflowError] = useState("");
  const [timelineBasis, setTimelineBasis] = useState("event_time");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("unknown");
  const [relation, setRelation] = useState("unknown");
  const [complete, setComplete] = useState(false);
  const [source, setSource] = useState("");
  const [eventTime, setEventTime] = useState("");
  const [owner, setOwner] = useState("");
  const [challengeReason, setChallengeReason] = useState("");
  const [challengeId, setChallengeId] = useState("");
  async function request(path: string, body?: unknown) {
    const response = await fetch(`${API}${path}`, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) throw Object.assign(new Error(await response.text()), { status: response.status });
    return response.json();
  }
  async function load(fresh = false) {
    setBusy(true);
    try {
      let saved = id === DEMO ? localStorage.getItem("veyra-investigation-session-v2") : id;
      let data;
      if (fresh || !saved) {
        data = await request("/demo/investigation-sessions", {});
        saved = data.investigation_id;
        localStorage.setItem("veyra-investigation-session-v2", saved!);
      } else {
        try { data = await request(`/investigations/${saved}/analysis`); }
        catch (e) {
          if (id !== DEMO || (e as { status?: number }).status !== 404) throw e;
          data = await request("/demo/investigation-sessions", {});
          saved = data.investigation_id;
          localStorage.setItem("veyra-investigation-session-v2", saved!);
        }
      }
      setCaseId(saved!); setAnalysis(data); setError("");
    } catch (e) { setError(`Investigation engine unavailable: ${e instanceof Error ? e.message : e}`); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, [id]);
  useEffect(() => {
    if (stage !== "overview" || !analysis) return;
    let active = true; setWorkflow(null); setWorkflowError("");
    request(`/investigations/${caseId}/workflow`).then(data => { if (active) setWorkflow(data); }).catch(() => { if (active) setWorkflowError("Decision records unavailable. Open Decision to retry."); });
    return () => { active = false; };
  }, [stage, caseId, analysis]);
  async function record(observation: any) {
    setBusy(true);
    try {
      const check = analysis.checks.find((c: any) => c.id.endsWith("check-ex11-zone-b"));
      setAnalysis(await request(`/investigations/${caseId}/diagnostic-checks/${encodeURIComponent(check.id)}/observation`, { observation }));
      setError("");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  function sample(exposure: string) {
    return record({ asset_id: "EX11", zone: "loading-zone-B", safe_stop: false, exposure_relation: exposure, exposure_verified: exposure !== "unknown", telemetry_complete: exposure !== "unknown", source: `synthetic://ex11/${exposure}`, event_time: new Date().toISOString(), synthetic: true });
  }
  async function challenge() {
    setBusy(true);
    try { setAnalysis(await request(`/investigations/${caseId}/hypotheses/${encodeURIComponent(challengeId)}/challenge`, {owner, reason: challengeReason, evidence_ids: []})); setChallengeReason(""); setError(""); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  useEffect(() => { if (analysis) onAnalysis?.(analysis); }, [analysis, onAnalysis]);
  const diagnostic = stage === "checks";
  const titles: Record<string, string> = {overview: "Current investigation", fleet: "Candidate controls", hypotheses: "Competing hypotheses", decision: "Decision workspace", checks: "Next diagnostic check", outcome: "Outcome memory", outcomes: "Action & outcome", precedents: "Conditional reuse", actions: "Actual actions", changes: "Configuration changes", timeline: "Case timeline", evidence: "Evidence sources"};
  const short = (value: string = "") => value.replace("localization-", "").replace("loading-zone-", "Zone ").replace("lidar-fw-", "");
  const time = (value: string) => value?.slice(11, 19) || "—";
  const label = (h: any) => h.id.endsWith("h-l4-zone-b") ? "H2" : h.id.endsWith("h-l4-alone") ? "H1" : "H3";
  const status = (value: string) => (({possible: "Unresolved", insufficient_evidence: "Evidence missing", supported_not_proven: "Supported · not proven", weakened: "Weakened", conflicting_evidence: "Conflicting evidence"} as Record<string, string>)[value] || value);
  const EvidenceList = ({items}: {items: any[]}) => <ul className="evidence-list">{items.map((e: any) => <li key={e.id}><strong>{e.asset_id || e.id}</strong><span>{e.source}</span>{e.matched_fields && <code>{Object.entries(e.matched_fields).map(([k,v]) => `${k}: ${v}`).join(" · ")}</code>}<small>Event {time(e.event_time)} · Known {time(e.known_at)}</small><code>{e.id}</code></li>)}</ul>;
  const candidate = analysis?.cohorts.find((c: any) => c.asset_id === "EX11");
  const cfg = analysis?.configuration_summary;
  const next = analysis?.next_check;
  const hypothesisCards = () => <>
    <p className="muted">Investigation priority orders checks. It is not causal confidence.</p>
    <div className="hypothesis-grid">{analysis.hypotheses.map((h: any) => <article key={h.id} className="investigation-card">
      <header><span>{label(h)}</span><span className="state-badge">{status(h.status)}</span></header>
      <h3>{h.label}</h3><small>Investigation priority · {h.investigation_priority}</small>
      <p>{h.supporting_evidence.length} supporting · {h.contradicting_evidence.length} contradicting</p>
      {!h.supporting_evidence.length && <p className="muted">No direct supporting evidence yet.{label(h) === "H3" && !h.context_evidence.length ? " Firmware metadata required." : ""}</p>}
      <details><summary>Evidence & gaps</summary>
        <h4>Supporting Evidence</h4>{h.supporting_evidence.length ? <EvidenceList items={h.supporting_evidence} /> : <p>No qualified diagnostic observation.</p>}
        <h4>Contradicting Evidence</h4>{h.contradicting_evidence.length ? <EvidenceList items={h.contradicting_evidence} /> : <p>No qualified counterexample.</p>}
        <h4>Configuration context · not causal support</h4>{h.context_evidence.length ? <EvidenceList items={h.context_evidence} /> : <p>Required metadata is missing.</p>}
        <h4>Missing Evidence</h4><ul>{h.missing_evidence.map((item: string) => <li key={item}>{item}</li>)}</ul>
        <small>Confounders: {h.possible_confounders.join(", ")}</small>
        {h.challenges.map((c: any, i: number) => <p key={i}>Challenge by {c.owner}: {c.reason}</p>)}
      </details>
    </article>)}</div>
    <details className="investigation-card"><summary>Challenge a hypothesis</summary><div className="investigation-form"><label>Hypothesis <select value={challengeId} onChange={e => setChallengeId(e.target.value)}><option value="">Select</option>{analysis.hypotheses.map((h: any) => <option key={h.id} value={h.id}>{label(h)} · {h.label}</option>)}</select></label><label>Engineer <input value={owner} onChange={e => setOwner(e.target.value)} /></label><label>Reason <textarea value={challengeReason} onChange={e => setChallengeReason(e.target.value)} /></label><button disabled={busy || !challengeId || !owner.trim() || !challengeReason.trim()} onClick={challenge}>Record challenge</button></div></details>
  </>;
  return <section className="investigation-analysis" aria-label="Evidence-driven investigation">
    <header className="investigation-heading"><div><small>Understand → Decide → Act → Verify → Reuse</small><h1>{titles[stage] || "Current investigation"}</h1></div><button className="quiet-button" disabled={busy} onClick={() => load(true)}>New investigation</button></header>
    {error && <p role="alert" className="investigation-error">{error}</p>}
    {!analysis ? <button disabled={busy} onClick={() => load()}>{busy ? "Loading evidence…" : "Connect investigation engine"}</button> : <>
      <div className="investigation-state"><span className="state-badge">{analysis.comparison_quality.valid_control_count ? "Control observation available" : "Exposure evidence incomplete"}</span><small>{analysis.diagnostic_events.length} diagnostic observations · current case</small></div>
      {stage === "overview" && <>
        <p className="muted">From field evidence to better operational decisions.</p>
        <article className="investigation-card decision-required"><header><strong>Decision required</strong><span className="state-badge">{workflow?.decisions.length ? "Decision recorded" : "Open"}</span></header><h2>{cfg.affected_count} machines with reported stops. What should the team do next?</h2><p>Restore service · Investigate further · Dispatch support</p><p className="muted">Restoring operations and reducing diagnostic uncertainty are different goals. The next diagnostic check is not automatically the best operational action.</p><p>{workflowError || (!workflow ? "Loading current decision…" : workflow.decisions.length ? `Current selection: ${workflow.decisions[workflow.decisions.length - 1].selected_option.label}` : "Current selection: No human decision recorded.")}</p>{onNavigate && <button onClick={() => onNavigate("decision")}>Compare options & record decision →</button>}</article>
        <div className="investigation-metrics"><div><b>{cfg.affected_count}</b><span>Affected machines</span></div><div><b>{analysis.comparison_quality.valid_control_count}</b><span>Verified controls</span></div><div><b>{analysis.diagnostic_events.length}</b><span>New observations</span></div></div>
        <div className="overview-grid"><article className="investigation-card"><small>What the records say</small><h2>L4 + Zone B is configured.</h2><p>{cfg.affected_configured_l4_zone_b}/{cfg.affected_count} affected and {cfg.no_signal_configured_l4_zone_b}/{cfg.no_known_signal_count} machines with no known signal share that configuration.</p><p className="muted">Comparable operating exposure is not established by configuration.</p></article><article className="investigation-card"><small>Next defensible step</small><h2>{next ? "Verify EX11 exposure." : "Review the new evidence."}</h2><p>Candidate control · {candidate?.exposure_status}</p>{onNavigate && <button onClick={() => onNavigate("checks")}>Review next check →</button>}</article></div>
        <p className="muted">{analysis.comparison_quality.conclusion}</p>
      </>}
      {stage === "fleet" && <>
        <article className="investigation-card candidate-card"><div><small>EX11 · Candidate control</small><h2>{candidate?.valid_control ? "Qualified healthy observation" : candidate?.exposure_status}</h2><p>Configured: {short(candidate?.context.configuration)} + {short(candidate?.context.network_profile)}</p></div><span className="state-badge">Valid control: {candidate?.valid_control ? "Yes" : "No"}</span></article>
        <p className="muted">A counterexample needs matched operating conditions, complete telemetry and no failure.</p>
        <details className="investigation-card" open><summary>Compare all {analysis.cohorts.length} machines</summary><div className="investigation-table-wrap"><table><thead><tr><th>Machine</th><th>Signal</th><th>Configured</th><th>Operating exposure</th><th>Valid control</th></tr></thead><tbody>{analysis.cohorts.map((c: any) => <tr key={c.asset_id} className={c.signal_observed ? "machine-affected" : c.asset_id === "EX11" ? "machine-watch" : ""}><td><strong>{c.asset_id}</strong></td><td><span className={c.signal_observed ? "signal-stop" : "signal-clear"}>{c.signal_observed ? "Stop reported" : "No reported stop"}</span></td><td>{short(c.context.configuration)} · {short(c.context.network_profile)}</td><td>{c.exposure_status}</td><td>{c.valid_control ? "Yes" : "No"}</td></tr>)}</tbody></table></div></details>
      </>}
      {stage === "hypotheses" && hypothesisCards()}
      {["decision", "actions", "outcome", "outcomes", "precedents"].includes(stage) && <DecisionWorkspace key={caseId} caseId={caseId} stage={stage} />}
      {diagnostic && <>
        <article className="investigation-card"><small>{next ? `Recommended · ${next.cost.time_minutes} min · ${next.risk.level} risk` : "Check recorded · engineer review"}</small><h2>{next ? "Verify EX11 operating exposure" : "Review the new evidence"}</h2><p>{next ? "Use its existing run records before treating it as a healthy control." : "No further discriminating check in this rule set. Plan independent replication with matched workload and site conditions."}</p>{next && <details><summary>Why this check?</summary><p>{next.why}</p></details>}</article>
        <article className="investigation-card"><h3>Try a synthetic observation</h3><p className="muted">Saved to this case and reassessed by the backend.</p><div className="investigation-scenarios"><button disabled={busy} onClick={() => sample("unknown")}>Exposure unknown</button><button disabled={busy} onClick={() => sample("same")}>Same conditions · healthy</button><button disabled={busy} onClick={() => sample("different")}>Different conditions</button></div><div className="hypothesis-statuses">{analysis.hypotheses.map((h: any) => <span key={h.id}>{label(h)} · {status(h.status)}</span>)}</div></article>
        <details className="investigation-card"><summary>Record a sourced diagnostic observation</summary><div className="investigation-form">
          <label>Result <select value={result} onChange={e => setResult(e.target.value)}><option value="unknown">Unknown</option><option value="safe_stop">Safe-stop observed</option><option value="healthy">Healthy throughout observation</option></select></label>
          <label>Operating exposure <select value={relation} onChange={e => setRelation(e.target.value)}><option value="unknown">Not verified</option><option value="same">Verified same conditions</option><option value="different">Verified different conditions</option></select></label>
          <label><input type="checkbox" checked={complete} onChange={e => setComplete(e.target.checked)} /> Complete telemetry window</label>
          <label>Event time <input type="datetime-local" value={eventTime} onChange={e => setEventTime(e.target.value)} /></label><label>Source reference <input value={source} onChange={e => setSource(e.target.value)} /></label>
          <button disabled={busy || result === "unknown" || !source.trim() || !eventTime} onClick={() => record({asset_id: "EX11", zone: "loading-zone-B", safe_stop: result === "safe_stop", exposure_relation: relation, exposure_verified: relation !== "unknown", telemetry_complete: complete, source, event_time: new Date(eventTime).toISOString()})}>Save & reassess</button>
        </div></details>
      </>}
      {["outcome", "outcomes", "precedents"].includes(stage) && <details className="investigation-card historical-reference"><summary>Historical reference outcome · separate from this case</summary><p>{analysis.historical_reference.reported_result}</p><small>Preset synthetic narrative. Not independently verified. Excluded from the current hypothesis assessment and workflow reuse.</small></details>}
      {["outcome", "outcomes", "precedents"].includes(stage) && analysis.conditional_precedents.length > 0 && <details className="investigation-card"><summary>Earlier outcome records · separate legacy format ({analysis.conditional_precedents.length})</summary><p className="muted">These records predate the separate decision/action/verification workflow. They are preserved, but excluded from workflow reuse.</p>{analysis.conditional_precedents.map((p: any) => <div key={p.outcome_id}><strong>{p.outcome}</strong><p>Conditions: {p.conditions.join("; ") || "not recorded"}</p><p>Verification: {p.verification}</p><p>Reported action: {p.verification_details.action || "not recorded"}</p></div>)}</details>}
      {stage === "changes" && <><p className="muted">Field-matched configuration records. These do not establish actual operating exposure.</p><div className="investigation-card investigation-table-wrap"><table><thead><tr><th>Machine</th><th>Recorded fields</th><th>Source</th><th>Event / Known UTC</th></tr></thead><tbody>{Array.from(new Map(analysis.hypotheses.flatMap((h: any) => h.context_evidence).map((e: any) => [e.id, e])).values()).map((e: any) => <tr key={e.id}><td>{e.asset_id}</td><td>{Object.entries(e.matched_fields || {}).map(([k,v]) => <div key={k}>{k}: {String(v)}</div>)}</td><td>{e.source}</td><td>{time(e.event_time)} / {time(e.known_at)}</td></tr>)}</tbody></table></div></>}
      {stage === "evidence" && <>
        <p className="muted">Source inventory and exact record references. A source reference alone does not mean the system independently reviewed its supporting material.</p>
        <div className="investigation-card investigation-table-wrap"><table><thead><tr><th>Source</th><th>Records</th><th>Inspect references</th></tr></thead><tbody>{Array.from(new Set<string>(analysis.evidence.map((e: any) => e.source))).map(sourceName => { const records = analysis.evidence.filter((e: any) => e.source === sourceName); return <tr key={sourceName}><td><strong>{sourceName}</strong></td><td>{records.length}</td><td><details><summary>Source records</summary><ul className="evidence-list">{records.map((e: any) => <li key={e.id}><strong>{e.asset_id || "Diagnostic record"} · {e.event_type || "Engineer-reported observation"}</strong><code>{e.id}</code><small>Event {time(e.event_time)} · Known {time(e.known_at)} UTC</small></li>)}</ul></details></td></tr>; })}</tbody></table></div>
      </>}
      {stage === "timeline" && <>
        <div className="timeline-toggle"><button className={timelineBasis === "event_time" ? "active" : ""} onClick={() => setTimelineBasis("event_time")}>When it happened</button><button className={timelineBasis === "known_at" ? "active" : ""} onClick={() => setTimelineBasis("known_at")}>When it became known</button></div>
        <p className="muted">Event time is when it happened. Known time is when the source became available. All times are UTC.</p>
        <div className="investigation-card investigation-table-wrap"><table><thead><tr><th>Machine / record</th><th>Event time (UTC)</th><th>Known time (UTC)</th></tr></thead><tbody>{[...analysis.timeline].sort((a: any,b: any) => a[timelineBasis].localeCompare(b[timelineBasis])).map((e: any) => <tr key={e.id}><td><strong>{e.asset_id}</strong> · {e.label}<small>{e.source}</small></td><td>{time(e.event_time)}</td><td>{time(e.known_at)}</td></tr>)}</tbody></table></div>
        <details className="investigation-card"><summary>New diagnostic observations ({analysis.diagnostic_events.length})</summary>{analysis.diagnostic_events.map((e: any) => <p key={e.id}>{e.source} · Event {time(e.event_time)} · Exposure {e.exposure_relation}</p>)}</details>
      </>}
      <details className="investigation-method"><summary>Method & boundaries</summary><p>{analysis.method}. Inputs are engineer reports or explicitly synthetic observations. No causal probability is estimated.</p><small>Case {caseId}</small></details>
    </>}
  </section>;
}
