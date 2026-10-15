"use client";

import { useEffect, useState } from "react";
import { API } from "../lib/api";

const DEMO = "inv-120-robots-bad-rollout";
export default function InvestigationAnalysis({ stage, id = DEMO }: { stage: string; id?: string }) {
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
    if (!response.ok) throw new Error(await response.text());
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
          if (id !== DEMO) throw e;
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
  const diagnostic = ["outcome", "outcomes", "checks"].includes(stage);
  return <section className="panel investigation-analysis" aria-label="Evidence-driven investigation">
    <header className="investigation-heading"><div><small>Evidence → Hypothesis → Test → Outcome</small><h3>Veyra Investigation Engine</h3></div><button disabled={busy} onClick={() => load(true)}>New investigation</button></header>
    {error && <p role="alert">{error}</p>}
    {!analysis ? <button disabled={busy} onClick={() => load()}>{busy ? "Loading evidence…" : "Connect investigation engine"}</button> : <>
      <p>{analysis.uncertainty}</p>
      <p><strong>{analysis.comparison_quality.conclusion}</strong></p>
      <small>Synthetic Field Case · {analysis.diagnostic_events.length} persisted diagnostic observations · no calibrated root-cause probabilities</small>
      {stage === "fleet" && <>
        <h4>Context-aware comparison</h4><p>{analysis.comparison_quality.valid_control_count} verified controls · sample size insufficient for causal estimation.</p>
        <table><thead><tr><th>Asset</th><th>Configuration / zone</th><th>Actual exposure</th><th>Valid control</th></tr></thead><tbody>{analysis.cohorts.map((c: any) => <tr key={c.asset_id}><td>{c.asset_id}</td><td>{c.context.configuration || "unknown"} / {c.context.network_profile || "unknown"}</td><td>{c.exposure_status}</td><td>{c.valid_control ? "Yes · qualified observation" : "No"}</td></tr>)}</tbody></table>
        <p>Configuration describes a machine. Exposure records show what it actually experienced. Missing telemetry cannot establish health.</p><p>Possible confounding: {analysis.comparison_quality.confounded_dimensions.join(", ")}. Still missing: {analysis.comparison_quality.missing_data.join("; ")}.</p>
      </>}
      {["decision", "hypotheses", "checks"].includes(stage) && <>
        {analysis.hypotheses.map((h: any) => <article key={h.id}><h4>{h.label} · {h.status} · review {h.review_status}</h4>{["supporting_evidence", "contradicting_evidence"].map(key => <div key={key}><strong>{key === "supporting_evidence" ? "Supporting Evidence" : "Contradicting Evidence"}</strong><ul>{h[key].length ? h[key].map((e: any) => <li key={e.id}>{e.id} · {e.source} · event {e.event_time} · known {e.known_at}</li>) : <li>No verified evidence</li>}</ul></div>)}<strong>Missing Evidence</strong><p>{h.missing_evidence.join("; ")}</p><strong>Possible confounders</strong><p>{h.possible_confounders.join("; ")}</p><small>{h.proposed_checks.length} proposed checks linked to this hypothesis. {h.non_discriminating_evidence.length} shared release records cannot separate these explanations.</small>{h.challenges.map((c: any, i: number) => <p key={i}>Engineer challenge · {c.owner}: {c.reason}</p>)}</article>)}
        <details><summary>Challenge a hypothesis</summary><label>Hypothesis <select value={challengeId} onChange={e => setChallengeId(e.target.value)}><option value="">Select hypothesis</option>{analysis.hypotheses.map((h: any) => <option key={h.id} value={h.id}>{h.label}</option>)}</select></label><label>Engineer <input value={owner} onChange={e => setOwner(e.target.value)} /></label><label>Reason / missing evidence <textarea value={challengeReason} onChange={e => setChallengeReason(e.target.value)} /></label><button disabled={busy || !challengeId || !owner.trim() || !challengeReason.trim()} onClick={challenge}>Record challenge</button></details>
        <h4>Next-best diagnostic check</h4><p>{analysis.next_check ? analysis.next_check.question : "No remaining discriminating check; engineer review required."}</p>
        {analysis.checks.map((c: any) => <article key={c.id}><strong>{c.question}</strong><p>{c.priority} priority · {c.cost.time_minutes} minutes · {c.risk.level} risk · {c.status}</p><p>{c.why}</p>{c.required_approval && <p>Requires {c.required_approval}. This interface cannot execute an intervention.</p>}</article>)}
      </>}
      {diagnostic && <>
        <h4>Test an exposure scenario</h4><p>These are explicitly synthetic inputs. Each click writes a sourced observation to the backend and recomputes this investigation. It does not run a machine.</p>
        <div className="investigation-scenarios"><button disabled={busy} onClick={() => sample("unknown")}>Exposure still unknown</button><button disabled={busy} onClick={() => sample("same")}>Same conditions · healthy</button><button disabled={busy} onClick={() => sample("different")}>Different conditions verified</button></div>
        <details><summary>Record your own diagnostic observation</summary>
          <label>Result <select value={result} onChange={e => setResult(e.target.value)}><option value="unknown">Unknown</option><option value="safe_stop">Safe-stop observed</option><option value="healthy">Healthy throughout observation</option></select></label>
          <label>Actual operating exposure <select value={relation} onChange={e => setRelation(e.target.value)}><option value="unknown">Not verified</option><option value="same">Verified same conditions</option><option value="different">Verified different conditions</option></select></label>
          <label><input type="checkbox" checked={complete} onChange={e => setComplete(e.target.checked)} /> Telemetry window complete</label>
          <label>Observation time <input type="datetime-local" value={eventTime} onChange={e => setEventTime(e.target.value)} /></label>
          <label>Source reference <input value={source} onChange={e => setSource(e.target.value)} /></label>
          <button disabled={busy || result === "unknown" || !source.trim() || !eventTime} onClick={() => record({asset_id: "EX11", zone: "loading-zone-B", safe_stop: result === "safe_stop", exposure_relation: relation, exposure_verified: relation !== "unknown", telemetry_complete: complete, source, event_time: new Date(eventTime).toISOString()})}>Run Diagnostic Check</button>
        </details>
        {analysis.hypotheses.map((h: any) => <p key={h.id}>{h.label}: <strong>{h.status}</strong></p>)}
        <details><summary>Save intervention, outcome and conditional precedent</summary>
          <label>Decision owner <input value={owner} onChange={e => setOwner(e.target.value)} /></label>
          <label>Action actually taken <input value={action} onChange={e => setAction(e.target.value)} /></label>
          <label>Was it the only changed variable? <select value={onlyChange} onChange={e => setOnlyChange(e.target.value)}><option value="unknown">Unknown</option><option value="yes">Yes</option><option value="no">No</option></select></label>
          <label>Observed outcome <textarea value={outcome} onChange={e => setOutcome(e.target.value)} /></label>
          <label><input type="checkbox" checked={recovered} onChange={e => setRecovered(e.target.checked)} /> Recovery independently verified by engineer</label>
          <label>Verification window <input value={window} onChange={e => setWindow(e.target.value)} /></label>
          <label>Outcome source <input value={outcomeSource} onChange={e => setOutcomeSource(e.target.value)} /></label>
          <label>Applicable conditions (separate with ;) <input value={conditions} onChange={e => setConditions(e.target.value)} /></label>
          <label>Remaining unknowns <textarea value={unknowns} onChange={e => setUnknowns(e.target.value)} /></label>
          <button disabled={busy || !owner.trim() || !outcome.trim() || !action.trim() || !outcomeSource.trim() || !conditions.trim() || !analysis.diagnostic_events.length} onClick={save}>Save conditional precedent</button>
        </details>
      </>}
      {["outcome", "outcomes", "precedents"].includes(stage) && analysis.conditional_precedents.map((p: any) => <article key={p.outcome_id}><strong>{p.outcome}</strong><p>{p.verification} · Conditions: {p.conditions.join("; ") || "unspecified; cannot recommend"}</p><p>Action: {p.verification_details.action || "not recorded"} · only changed variable: {String(p.verification_details.only_variable_changed ?? "unknown")} · recovery verified: {String(p.verification_details.recovery_verified ?? false)}</p><p>Remaining unknowns: {p.verification_details.remaining_unknowns || "not resolved"}. Causal attribution not established.</p></article>)}
      <details><summary>Persisted investigation evidence</summary>{analysis.diagnostic_events.map((e: any) => <p key={e.id}>{e.id} · {e.source} · exposure {e.exposure_relation} · {e.event_time}</p>)}</details>
      <small>{analysis.method}</small>
    </>}
  </section>;
}
