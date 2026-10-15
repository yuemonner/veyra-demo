"use client";

import { FormEvent, useEffect, useState } from "react";
import { API } from "../lib/api";
import { fetchCaseJSON } from "../lib/case-data";

export default function DecisionWorkspace({ caseId, stage }: {caseId: string; stage: string}) {
  const [data, setData] = useState<any>(null);
  const [reuse, setReuse] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [option, setOption] = useState("inspect");
  function request(path = "", body?: any) { return fetchCaseJSON(API, `/investigations/${caseId}/workflow${path}`, body); }
  async function load() {
    const [d, r] = await Promise.all([request(), request("/reuse")]); setData(d); setReuse(r);
  }
  useEffect(() => { setData(null); setError(""); setMessage(""); void load().catch(e => setError(String(e))); }, [caseId]);
  async function submit(e: FormEvent<HTMLFormElement>, kind: string) {
    e.preventDefault(); const form = e.currentTarget; const values: any = Object.fromEntries(new FormData(form));
    setBusy(true); setError(""); setMessage("");
    try {
      if (kind === "decisions") values.option_id = option;
      if (kind === "actions") values.executed_at = new Date(values.executed_at).toISOString();
      if (kind === "verifications") {
        values.window_start = new Date(values.window_start).toISOString(); values.window_end = new Date(values.window_end).toISOString();
        values.conditions = Object.fromEntries(String(values.conditions).split("\n").filter(x => x.trim()).map(line => { const at = line.indexOf("="); if (at < 1) throw new Error("Use one field=value condition per line"); return [line.slice(0,at).trim(), line.slice(at+1).trim()]; }));
      }
      await request(`/${kind}`, values); await load(); form.reset(); setMessage("Saved to this case. Refreshing preserves the record.");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const field = (label: string, name: string, type = "text") => <label>{label}<input required name={name} type={type} step={type === "datetime-local" ? "1" : undefined} /></label>;
  const text = (label: string, name: string, hint?: string) => <label>{label}<textarea required name={name} placeholder={hint} /></label>;
  if (!data) return <article className="investigation-card">{error || "Loading decision records…"}{error && <button onClick={() => { setError(""); void load().catch(e => setError(String(e))); }}>Retry loading records</button>}</article>;
  const decisionView = stage === "decision";
  return <div className="decision-workspace">
    <p className="muted">{decisionView ? `${data.affected_count} machines with reported stops · Root cause unresolved. Compare the operational choices, then record the team's decision.` : "Record what actually happened, verify the result over time, and carry forward experience with its limits."}</p>
    {error && <p role="alert" className="investigation-error">{error}</p>}{message && <p role="status">{message}</p>}
    {decisionView && <>
      <div className="hypothesis-grid">{data.options.map((o: any) => <article className="investigation-card" key={o.id}><small>{o.goal}</small><h3>{o.label}</h3><p>{o.tradeoff}</p><details><summary>Constraints & cost</summary><p>{o.constraint}</p><p>{o.time_cost}</p></details><button className={option === o.id ? "" : "quiet-button"} onClick={() => setOption(o.id)} aria-pressed={option === o.id}>{option === o.id ? "Selected for decision" : "Consider this option"}</button></article>)}</div>
      <details className="investigation-card" open><summary>Record human decision</summary><form onSubmit={e => submit(e, "decisions")} className="investigation-form"><p>Choice: <strong>{data.options.find((o: any) => o.id === option)?.label}</strong></p>{field("Decision owner", "owner")}{text("Why this option?", "rationale", "Balance recovery, uncertainty, risk and alternatives")}{text("Constraints & accepted trade-offs", "constraints")}
        <label>Approval status<select name="approval" required defaultValue="pending" key={option}><option value="pending">Pending approval</option><option value="approved">Approved by responsible team</option>{option === "inspect" && <option value="not_required">Approval not required for records review</option>}</select></label><small>Saves all considered options and the evidence available now. Does not execute an action.</small><button disabled={busy}>Save decision & evidence snapshot</button></form></details>
      <article className="investigation-card"><h3>Decision history ({data.decisions.length})</h3>{!data.decisions.length && <p>No human decision recorded.</p>}{data.decisions.map((d: any) => <details key={d.id}><summary>{d.selected_option.label} · {d.owner} · {d.approval}</summary><p>{d.rationale}</p><p>Constraints: {d.constraints}</p><small>Recorded {d.recorded_at} UTC · {d.id}</small><p>Snapshot: {d.evidence_snapshot.evidence.length} source records · {d.evidence_snapshot.diagnostic_events.length} diagnostic observations</p><ul>{d.evidence_snapshot.hypotheses.map((h: any) => <li key={h.id}>{h.label}: {h.status} · {h.missing_evidence.length} evidence gaps</li>)}</ul><details><summary>Evidence available at decision time</summary><ul>{[...d.evidence_snapshot.evidence, ...d.evidence_snapshot.diagnostic_events].map((r: any) => <li key={r.id}>{r.source} · {r.id} · Event {r.event_time} · Known {r.known_at || d.recorded_at}</li>)}</ul></details></details>)}</article>
    </>}
    {!decisionView && stage !== "precedents" && <>
      <article className="investigation-card"><h3>Actual actions ({data.actions.length}) · Verification updates ({data.verifications.length})</h3>{!data.actions.length && <p>No execution reported. A saved decision alone is not an executed action.</p>}{data.actions.map((a: any) => <details key={a.id} open><summary>{a.description} · {a.owner}</summary><p>{a.scope} · Executed {a.executed_at} UTC</p><p>Compared with plan: {a.deviation_reason}</p><small>Source: {a.source}</small>{data.verifications.filter((v: any) => v.action_id === a.id).map((v: any) => <div className="verification-update" key={v.id}><strong>{v.result.replaceAll("_", " ")}</strong><p>{v.summary}</p><small>{v.window_start} → {v.window_end} UTC · {v.source}</small><details><summary>Verification & applicability</summary><p>Human report by {v.owner}; source requires review. Causal attribution not established.</p><p>Unknowns: {v.remaining_unknowns}</p><p>Exclusions: {v.exclusions}</p><code>{JSON.stringify(v.conditions)}</code></details></div>)}</details>)}</article>
      <details className="investigation-card"><summary>Report an actual action</summary>{!data.decisions.length ? <p>Record a human decision in Decision first.</p> : <form onSubmit={e => submit(e,"actions")} className="investigation-form"><label>Decision<select name="decision_id" required><option value="">Select recorded decision</option>{data.decisions.map((d: any) => <option key={d.id} value={d.id}>{d.selected_option.label} · {d.owner} · {d.approval} · {d.recorded_at}</option>)}</select></label>{field("Execution owner", "owner")}{text("What was actually done?", "description")}{field("Machines / scope", "scope")}{field("Executed at (your local time)", "executed_at", "datetime-local")}{field("Execution evidence reference", "source")}{text("Difference from plan, or confirm same as plan", "deviation_reason")}<small>Record only completed actions. Pending required approval blocks execution reporting. Veyra sends no commands to machines.</small><button disabled={busy}>Save reported action</button></form>}</details>
      <details className="investigation-card"><summary>Verify outcome or record recurrence</summary>{!data.actions.length ? <p>Report an actual action first.</p> : <form onSubmit={e => submit(e,"verifications")} className="investigation-form"><label>Action<select name="action_id" required><option value="">Select action</option>{data.actions.map((a: any) => <option key={a.id} value={a.id}>{a.description} · {a.executed_at}</option>)}</select></label>{field("Reviewer", "owner")}<label>Observed result<select name="result"><option value="unknown">Unknown</option><option value="recovered">Recovered during observation window</option><option value="partially_recovered">Partially recovered</option><option value="not_recovered">Not recovered</option><option value="recurred">Issue recurred</option></select></label>{text("What did the observation show?", "summary")}{field("Window start (local time)","window_start","datetime-local")}{field("Window end (local time)","window_end","datetime-local")}{field("Verification evidence reference","source")}{text("Remaining unknowns", "remaining_unknowns")}{text("Applicability conditions · one field=value per line","conditions",`configuration=${data.context.configuration}\napplication_version=${data.context.application_version}`)}<small>Supported: application_version, configuration, firmware_version, map_version, network_profile, site, actual_exposure. Only enter known conditions; actual exposure is not inferred from configuration.</small>{text("Exclusions / when not to reuse", "exclusions")}<small>Appends a verification record. A later recurrence is preserved and governs reuse for this action.</small><button disabled={busy}>Save verification update</button></form>}</details>
    </>}
    <details className="investigation-card" open={stage === "precedents"}><summary>Experience from other cases ({reuse?.candidates.length || 0})</summary><p className="muted">{reuse?.boundary}</p>{!reuse?.candidates.length && <p>No other case has a recorded action and verification yet. A historical demo ending is not a reusable verified decision.</p>}{reuse?.candidates.map((p: any) => <article className="verification-update" key={p.verification.id}><strong>{p.action.description}</strong><p>{p.status.replaceAll("_"," ")} · {p.use.replaceAll("_"," ")}</p><p>Why chosen: {p.decision.rationale}</p><p>Latest result: {p.verification.result} · {p.verification.summary}</p><small>Case {p.source_case_id} · Source {p.verification.source}</small><p>Matched: {p.matched_fields.join(", ") || "none"} · Conflicting: {p.mismatched_fields.join(", ") || "none"} · Unknown: {p.unknown_fields.join(", ") || "none"}</p><p>Exclusions: {p.verification.exclusions}</p><p>Remaining unknowns: {p.verification.remaining_unknowns}</p><small>Human review required. No automatic action recommendation.</small></article>)}</details>
    <small className="muted">{data.boundary}</small>
  </div>;
}
