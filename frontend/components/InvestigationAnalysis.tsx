"use client";

import { useEffect, useState } from "react";
import { API } from "../lib/api";

const DEMO = "inv-120-robots-bad-rollout";
export default function InvestigationAnalysis({ stage, id = DEMO, onNavigate }: { stage: string; id?: string; onNavigate?: (stage: string) => void }) {
  const [analysis, setAnalysis] = useState<any>(null);
  const [caseId, setCaseId] = useState(id);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("unknown");
  const [relation, setRelation] = useState("unknown");
  const [complete, setComplete] = useState(false);
  const [source, setSource] = useState("");
  const [eventTime, setEventTime] = useState("");
  const [owner, setOwner] = useState("");
  const [outcome, setOutcome] = useState("");
  const [conditions, setConditions] = useState("");
  const [action, setAction] = useState("");
  const [onlyChange, setOnlyChange] = useState("unknown");
  const [recovered, setRecovered] = useState(false);
  const [window, setWindow] = useState("");
  const [outcomeSource, setOutcomeSource] = useState("");
  const [unknowns, setUnknowns] = useState("");
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
  async function save() {
    setBusy(true);
    try {
      await request(`/investigations/${caseId}/analysis/precedent`, { owner, outcome, applicable_conditions: conditions.split(";").map(c => c.trim()).filter(Boolean), intervention_verification: { action, only_variable_changed: onlyChange === "unknown" ? null : onlyChange === "yes", recovery_verified: recovered, verification_window: window, source: outcomeSource, remaining_unknowns: unknowns, alternatives_excluded: null } });
      await load();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function challenge() {
    setBusy(true);
    try { setAnalysis(await request(`/investigations/${caseId}/hypotheses/${encodeURIComponent(challengeId)}/challenge`, {owner, reason: challengeReason, evidence_ids: []})); setChallengeReason(""); setError(""); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const diagnostic = stage === "checks";
  const titles: Record<string, string> = {overview: "Get the team to the next decision", fleet: "Candidate controls", hypotheses: "Competing hypotheses", decision: "Decision support", checks: "Take the next useful step", outcome: "Outcome memory", outcomes: "Capture the result. Keep the learning.", precedents: "Outcome memory", evidence: "Evidence & timeline"};
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
    <header className="investigation-heading"><div><small>Understand the issue → Choose a check → Record the result</small><h1>{titles[stage] || "Current investigation"}</h1></div><button className="quiet-button" disabled={busy} onClick={() => load(true)}>New investigation</button></header>
    {error && <p role="alert" className="investigation-error">{error}</p>}
    {!analysis ? <button disabled={busy} onClick={() => load()}>{busy ? "Loading evidence…" : "Connect investigation engine"}</button> : <>
      <div className="investigation-state"><span className="state-badge">{analysis.comparison_quality.valid_control_count ? "New evidence ready for review" : "Cause not confirmed"}</span><small>{analysis.diagnostic_events.length} checks recorded · current case</small></div>
      {stage === "overview" && <>
        <p className="situation-intro">Three machines reported safe-stops after an update. Give engineering and delivery one shared view of what is known and what to do next.</p>
        <div className="investigation-metrics"><div><b>{cfg.affected_count}</b><span>Machines with reported stops</span></div><div><b>{cfg.no_known_signal_count}</b><span>Machines with no reported stops</span></div><div><b>{analysis.diagnostic_events.length}</b><span>Follow-up checks recorded</span></div></div>
        <div className="overview-grid"><article className="investigation-card"><small>What needs a decision</small><h2>What should the team check before choosing a fix?</h2><p>The update is a lead, not a confirmed cause. Operating conditions still need checking.</p><details><summary>See the engineering evidence</summary><p>{cfg.affected_configured_l4_zone_b}/{cfg.affected_count} affected and {cfg.no_signal_configured_l4_zone_b}/{cfg.no_known_signal_count} machines with no known signal share the L4 + Zone B configuration. Configuration does not establish actual operating exposure.</p><p>{analysis.comparison_quality.conclusion}</p>{onNavigate && <button className="quiet-button" onClick={() => onNavigate("hypotheses")}>Review possible causes →</button>}</details></article><article className="investigation-card"><small>Recommended next action</small><h2>{next ? "Check whether EX11 ran in the same conditions." : "Review what the new evidence changes."}</h2><p>{next ? "EX11 has no reported stop. Its run records can tell the team whether it is a useful comparison." : "Use the recorded check to update the investigation before deciding on an intervention."}</p><p className="muted">{next ? "This check helps avoid choosing a fix from an unverified comparison." : "A completed check is evidence for a decision, not proof of a root cause."}</p>{onNavigate && <button onClick={() => onNavigate("checks")}>{next ? "Run the next check →" : "Review check result →"}</button>}</article></div>
        <p className="muted">One case keeps the evidence, the team's decision and the observed result together. Downtime and delivery impact have not been measured in this synthetic case.</p>
      </>}
      {stage === "fleet" && <>
        <article className="investigation-card candidate-card"><div><small>EX11 · Candidate control</small><h2>{candidate?.valid_control ? "Qualified healthy observation" : candidate?.exposure_status}</h2><p>Configured: {short(candidate?.context.configuration)} + {short(candidate?.context.network_profile)}</p></div><span className="state-badge">Valid control: {candidate?.valid_control ? "Yes" : "No"}</span></article>
        <p className="muted">A counterexample needs matched operating conditions, complete telemetry and no failure.</p>
        <details className="investigation-card"><summary>Compare all {analysis.cohorts.length} machines</summary><div className="investigation-table-wrap"><table><thead><tr><th>Machine</th><th>Configured</th><th>Operating exposure</th><th>Valid control</th></tr></thead><tbody>{analysis.cohorts.map((c: any) => <tr key={c.asset_id}><td>{c.asset_id}</td><td>{short(c.context.configuration)} · {short(c.context.network_profile)}</td><td>{c.exposure_status}</td><td>{c.valid_control ? "Yes" : "No"}</td></tr>)}</tbody></table></div></details>
      </>}
      {["decision", "hypotheses"].includes(stage) && hypothesisCards()}
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
      {["outcome", "outcomes", "precedents"].includes(stage) && <>
        <article className="investigation-card"><small>Current investigation state</small><h2>{analysis.conditional_precedents.length ? "Recorded outcomes" : "No outcome recorded"}</h2>{analysis.conditional_precedents.map((p: any) => <div key={p.outcome_id}><h3>{p.outcome}</h3><p>Conditions: {p.conditions.join("; ")}</p><details><summary>Verification record</summary><p>{p.verification}</p><p>Action: {p.verification_details.action || "not recorded"}</p><p>Only changed variable: {String(p.verification_details.only_variable_changed ?? "unknown")}</p><p>Remaining unknowns: {p.verification_details.remaining_unknowns || "unresolved"}</p></details></div>)}</article>
        <details className="investigation-card"><summary>Record action & outcome</summary><div className="investigation-form"><label>Decision owner <input value={owner} onChange={e => setOwner(e.target.value)} /></label><label>Action taken <input value={action} onChange={e => setAction(e.target.value)} /></label><label>Observed outcome <textarea value={outcome} onChange={e => setOutcome(e.target.value)} /></label><label>Outcome evidence reference <input value={outcomeSource} onChange={e => setOutcomeSource(e.target.value)} /></label><label>Applicable conditions (separate with ;) <input value={conditions} onChange={e => setConditions(e.target.value)} /></label><details><summary>Verification & remaining unknowns</summary><label>Only changed variable? <select value={onlyChange} onChange={e => setOnlyChange(e.target.value)}><option value="unknown">Unknown</option><option value="yes">Yes</option><option value="no">No</option></select></label><label><input type="checkbox" checked={recovered} onChange={e => setRecovered(e.target.checked)} /> Recovery verified by engineer</label><label>Verification window <input value={window} onChange={e => setWindow(e.target.value)} /></label><label>Remaining unknowns <textarea value={unknowns} onChange={e => setUnknowns(e.target.value)} /></label></details>{!analysis.diagnostic_events.length && <p className="muted">Record a diagnostic observation before saving an outcome.</p>}<button disabled={busy || !owner.trim() || !outcome.trim() || !action.trim() || !outcomeSource.trim() || !conditions.trim() || !analysis.diagnostic_events.length} onClick={save}>Save conditional precedent</button></div></details>
        <details className="investigation-card historical-reference"><summary>Historical reference outcome · separate from this case</summary><p>{analysis.historical_reference.reported_result}</p><small>Preset synthetic narrative. Not independently verified. Excluded from the current hypothesis assessment.</small></details>
      </>}
      {stage === "evidence" && <>
        <p className="muted">Event time is when it happened. Known time is when the source became available. All times are UTC.</p>
        <div className="investigation-card investigation-table-wrap"><table><thead><tr><th>Machine / record</th><th>Event time (UTC)</th><th>Known time (UTC)</th></tr></thead><tbody>{analysis.timeline.map((e: any) => <tr key={e.id}><td><strong>{e.asset_id}</strong> · {e.label}<small>{e.source}</small></td><td>{time(e.event_time)}</td><td>{time(e.known_at)}</td></tr>)}</tbody></table></div>
        <details className="investigation-card"><summary>New diagnostic observations ({analysis.diagnostic_events.length})</summary>{analysis.diagnostic_events.map((e: any) => <p key={e.id}>{e.source} · Event {time(e.event_time)} · Exposure {e.exposure_relation}</p>)}</details>
      </>}
      <details className="investigation-method"><summary>Method & boundaries</summary><p>{analysis.method}. Inputs are engineer reports or explicitly synthetic observations. No causal probability is estimated.</p><small>Case {caseId}</small></details>
    </>}
  </section>;
}
