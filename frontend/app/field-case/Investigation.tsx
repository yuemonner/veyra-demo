"use client";
/* The case, action first: Veyra investigates; the engineer only does what Veyra cannot do from the data.
   What happened -> what Veyra already checked -> the one next action -> what you found (feeds the next step). */
import { useCallback, useEffect, useRef, useState } from "react";
import st from "./investigation.module.css";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const STATUS: Record<string, { label: string; cls: string }> = {
  cause_probable: { label: "Probable cause", cls: "probable" },
  cause_unknown: { label: "Cause not established", cls: "unknown" },
  no_failure_found: { label: "No failure found in the data", cls: "unknown" },
};
const VERDICT: Record<string, { mark: string; label: string; cls: string }> = {
  supports: { mark: "●", label: "supports", cls: "vSupports" },
  weakens: { mark: "◐", label: "weakens", cls: "vWeakens" },
  rules_out: { mark: "✕", label: "rules out", cls: "vRules" },
  inconclusive: { mark: "○", label: "inconclusive", cls: "vInc" },
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Investigation({ caseId, call, actor, jobId, onChanged }:
  { caseId: string; call: Call; actor: string; jobId?: string | null; onChanged: () => void }) {
  const [data, setData] = useState<Any>(null);
  const [job, setJob] = useState<Any>(null);
  const [error, setError] = useState<string | null>(null);
  const polling = useRef<string | null>(null);

  const load = useCallback(async () => {
    try { setData(await call(`/engine/cases/${caseId}/investigation`)); } catch (e: Any) { setError(e.message); }
  }, [caseId, call]);

  const follow = useCallback(async (id: string) => {
    polling.current = id;
    for (;;) {
      if (polling.current !== id) return;
      let j: Any;
      try { j = await call(`/engine/jobs/${id}`); } catch { await sleep(1500); continue; }
      setJob(j);
      if (j.state !== "running") {
        polling.current = null;
        if (j.state === "failed") setError(j.error || "The investigation failed");
        await load();
        onChanged();
        setJob(null);
        return;
      }
      await sleep(900);
    }
  }, [call, load, onChanged]);

  useEffect(() => { setData(null); setJob(null); setError(null); load(); if (jobId) follow(jobId); return () => { polling.current = null; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start(allowModel: boolean) {
    setError(null);
    try { const { job_id } = await call(`/engine/cases/${caseId}/investigate`, { method: "POST", body: JSON.stringify({ allow_model: allowModel }) }); follow(job_id); }
    catch (e: Any) { setError(e.message); }
  }

  if (job) return <Live job={job} />;
  if (!data) return <section className={st.surface}><p className={st.dim}>Loading…</p></section>;
  const inv = data.investigation;
  const agent = data.agent || {};
  if (!inv) {
    return (
      <section className={st.surface}>
        <h1 className={st.headline}>Veyra has not investigated this case yet</h1>
        <p className={st.lead}>Veyra reads the original data, runs the checks it can do itself, and gives you one next action.</p>
        <div className={st.row}>
          {agent.configured ? <button className={st.primary} onClick={() => start(true)}>Investigate now</button>
            : <button className={st.primary} onClick={() => start(false)}>Read the log now</button>}
        </div>
        {agent.configured && <p className={st.fine}>The AI investigator reads this case's data through {agent.model}. Starting it records that you allowed it.</p>}
        {error && <p className={st.err}>{error}</p>}
      </section>
    );
  }
  const r = inv.result || {};
  const status = STATUS[r.status] || STATUS.cause_unknown;
  return (
    <section className={st.surface}>
      {inv.mode !== "agent" && (
        <div className={st.banner}>
          <b>Rule-based reading only.</b> {inv.fallback_reason}.{" "}
          {agent.configured ? <button className={st.inline} onClick={() => start(true)}>Let Veyra investigate with AI</button>
            : <span>Connect the AI investigator (Claude Code sign-in on this computer) to have Veyra run the checks for you.</span>}
        </div>
      )}
      <header className={st.head}>
        <span className={`${st.status} ${st[status.cls]}`}>{status.label}{r.confidence && r.status === "cause_probable" ? ` · ${r.confidence} confidence` : ""}</span>
        <h1 className={st.headline}>{r.headline}</h1>
        {r.explanation && <p className={st.lead}>{r.explanation}</p>}
        {inv.recorded && <p className={st.fine}>Recorded {new Date(inv.recorded.at).toLocaleString()} with {String(inv.model || "").replace("claude-code:", "Claude ")} on a local Veyra instance and shown here as recorded; this demo did not run it again.</p>}
        {inv.mode === "agent" && <p className={st.fine}>Veyra ran {inv.steps.length} check{inv.steps.length === 1 ? "" : "s"} on the original data in {inv.seconds} s · every quoted line verified against the file{(inv.dropped || []).length ? ` · ${inv.dropped.length} unverifiable statement${inv.dropped.length === 1 ? "" : "s"} removed` : ""}</p>}
      </header>

      <NextAction caseId={caseId} call={call} next={r.next_action} entry={inv.entry_id} closed={data.closed} onRecorded={(j) => j ? follow(j) : load()} />

      {(r.evidence || []).length > 0 && (
        <div className={st.block}>
          <h2 className={st.h2}>Why Veyra thinks so</h2>
          <ul className={st.lines}>
            {r.evidence.map((e: Any, i: number) => (
              <li key={i}><code className={st.ln}>{e.line}</code><div><code className={st.raw}>{e.quote}</code><span className={st.shows}>{e.shows}</span></div></li>))}
          </ul>
        </div>
      )}

      {(r.checked || []).length > 0 && (
        <div className={st.block}>
          <h2 className={st.h2}>Already checked</h2>
          <ul className={st.checks}>
            {r.checked.map((c: Any, i: number) => {
              const v = VERDICT[c.verdict] || VERDICT.inconclusive;
              return (
                <li key={i}>
                  <span className={`${st.verdict} ${st[v.cls]}`} title={v.label}>{v.mark}</span>
                  <div><b>{c.check}</b><span className={st.result}>{c.result}</span>
                    {c.about && <span className={st.about}>{v.label}: {c.about}</span>}</div>
                </li>);
            })}
          </ul>
        </div>
      )}

      {(r.effects || []).length > 0 && (
        <details className={st.fold}>
          <summary>Effects, not causes ({r.effects.length})</summary>
          <ul className={st.lines}>
            {r.effects.map((e: Any, i: number) => (<li key={i}><code className={st.ln}>{e.line}</code><div><code className={st.raw}>{e.quote}</code><span className={st.shows}>{e.why}</span></div></li>))}
          </ul>
        </details>
      )}

      {(data.findings || []).length > 0 && (
        <div className={st.block}>
          <h2 className={st.h2}>What you found</h2>
          <ul className={st.findings}>
            {data.findings.map((f: Any) => (
              <li key={f.id}><span className={st.when}>{new Date(f.recorded_at).toLocaleString()}</span>
                <b>{f.action}</b> — {({ found: "found it", not_found: "nothing found", cannot_do: "could not do it", other: "other result" } as Any)[f.result]}
                {f.observations ? `: ${f.observations}` : ""}</li>))}
          </ul>
        </div>
      )}

      {((r.unknowns || []).length > 0 || r.lesson) && (
        <div className={st.block}>
          {(r.unknowns || []).length > 0 && <><h2 className={st.h2}>Not established by the data</h2>
            <ul className={st.plain}>{r.unknowns.map((u: string, i: number) => <li key={i}>{u}</li>)}</ul></>}
          {r.lesson && <p className={st.lessonNote}><b>Earlier cases:</b> {r.lesson}</p>}
        </div>
      )}

      {(inv.steps || []).length > 0 && (
        <details className={st.fold}>
          <summary>How Veyra investigated ({inv.steps.length} steps)</summary>
          <ol className={st.steps}>{inv.steps.map((s: Any) => <li key={s.id}><span>{s.label}</span><span className={st.dim}>{s.summary}</span></li>)}</ol>
        </details>
      )}

      {!data.closed && <CloseCase caseId={caseId} call={call} actor={actor} onClosed={() => { load(); onChanged(); }} />}
      {data.closed && <p className={st.fine}>This case is closed. Its lesson is used when a similar failure comes in.</p>}
      {error && <p className={st.err}>{error}</p>}
    </section>
  );
}

function Live({ job }: { job: Any }) {
  const steps: Any[] = job.steps || [];
  return (
    <section className={st.surface} aria-live="polite">
      <span className={`${st.status} ${st.working}`}>Investigating</span>
      <h1 className={st.headline}>{job.step === "Veyra is investigating" ? "Veyra is investigating the data" : job.step || "Starting"}</h1>
      <p className={st.lead}>You don't need to stay on this page. Each check below runs on the original data.</p>
      <ol className={st.liveSteps}>
        {steps.map((s: Any) => <li key={s.id} className={st.done}><span>{s.label}</span><span className={st.dim}>{s.summary}</span></li>)}
        <li className={st.now}><span className={st.pulse} aria-hidden /> <span>{steps.length ? "Deciding the next check" : job.step}</span></li>
      </ol>
    </section>
  );
}

function NextAction({ caseId, call, next, entry, closed, onRecorded }:
  { caseId: string; call: Call; next: Any; entry: string; closed: boolean; onRecorded: (jobId: string | null) => void }) {
  const [choice, setChoice] = useState<Any>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  if (!next) return null;
  const outcomes: Any[] = [...(next.outcomes || []).map((o: Any) => ({ ...o, result: "found" })), { label: "I can't do this", means: "Veyra looks for another way", result: "cannot_do" }];
  async function save() {
    setBusy(true); setErr(null);
    try {
      const result = choice.result === "cannot_do" ? "cannot_do" : (/nothing|no |not |normal|flat|clean/i.test(choice.label) ? "not_found" : "found");
      const observations = [choice.result === "cannot_do" ? null : `${choice.label}${choice.means ? ` (${choice.means})` : ""}`, notes.trim() || null].filter(Boolean).join(". ") || null;
      const out = await call(`/engine/cases/${caseId}/finding`, { method: "POST", body: JSON.stringify({ action: next.action, result, observations, investigation_entry: entry }) });
      setChoice(null); setNotes(""); onRecorded(out.job_id || null);
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className={st.next}>
      <span className={st.nextLabel}>Your next action{next.where ? ` · ${({ remote: "remote", on_site: "on site", lab: "in the lab" } as Any)[next.where] || next.where}` : ""}</span>
      <h2 className={st.action}>{next.action}</h2>
      {next.why && <p className={st.why}>{next.why}</p>}
      {(next.how || []).length > 0 && (
        <div className={st.howWrap}>
          <ol className={st.how}>{next.how.map((h: string, i: number) => <li key={i}>{h}</li>)}</ol>
          <button className={st.ghost} onClick={async () => { await navigator.clipboard?.writeText(`${next.action}\n${next.how.map((h: string, i: number) => `${i + 1}. ${h}`).join("\n")}`); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? "Copied" : "Copy steps"}</button>
        </div>
      )}
      {!closed && <>
        <p className={st.ask}>What did you find?</p>
        <div className={st.outcomes}>
          {outcomes.map((o, i) => (
            <button key={i} className={choice?.label === o.label ? st.outcomeOn : st.outcome} onClick={() => setChoice(o)} aria-pressed={choice?.label === o.label}>
              <b>{o.label}</b><span>{o.means}</span></button>))}
        </div>
        {choice && (
          <div className={st.record}>
            <label className={st.fieldLabel} htmlFor="finding-notes">{choice.result === "cannot_do" ? "Why not? (optional)" : "What exactly did you see? (optional, helps the next step)"}</label>
            <textarea id="finding-notes" className={st.text} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={choice.result === "cannot_do" ? "e.g. no access to the robot until Monday" : "a value, a message, a part number"} />
            <div className={st.row}>
              <button className={st.primary} onClick={save} disabled={busy}>{busy ? "Saving…" : "Save and continue the investigation"}</button>
              <button className={st.ghost} onClick={() => setChoice(null)}>Cancel</button>
            </div>
            {err && <p className={st.err}>{err}</p>}
          </div>
        )}
      </>}
    </div>
  );
}

function CloseCase({ caseId, call, actor, onClosed }: { caseId: string; call: Call; actor: string; onClosed: () => void }) {
  const [open, setOpen] = useState(false);
  const [d, setD] = useState<Any>(null);
  const [domains, setDomains] = useState<Any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function draft() {
    setOpen(true); setErr(null);
    try {
      const out = await call(`/engine/cases/${caseId}/lesson-draft`);
      const pick = (out.domains || []).find((x: Any) => x.status === "anomaly_observed") || (out.domains || [])[0];
      setDomains(out.domains || []); setD({ ...out.draft, cause_domain: out.draft.cause_domain || pick?.id || "" });
    } catch (e: Any) { setErr(e.message); }
  }
  async function confirm() {
    setBusy(true); setErr(null);
    try {
      const withCause = d.investigation_status !== "cause_unknown";
      const payload: Any = { investigation_status: d.investigation_status, resolution_status: d.resolution_status, evidence_note: d.evidence_note || null,
        decisive_check: (d.decisive_check || "").slice(0, 200) || null, applies_when: d.applies_when || null, fix: d.fix || null };
      if (withCause) Object.assign(payload, { cause: d.cause, cause_domain: d.cause_domain, confirmed_by: d.investigation_status === "cause_confirmed" && !["inspection", "reproduction", "fix_resolved_it"].includes(d.confirmed_by) ? "fix_resolved_it" : (d.investigation_status === "cause_confirmed" ? d.confirmed_by : "engineer_judgement") });
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "case_closure", actor, source: "lesson draft (confirmed)", payload }) });
      onClosed();
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  if (!open) return <div className={st.closeRow}><button className={st.ghost} onClick={draft}>Close the case</button><span className={st.fine}>Veyra drafts the lesson; you confirm it.</span></div>;
  if (!d) return <div className={st.lesson}><p className={st.dim}>Drafting the lesson…</p>{err && <p className={st.err}>{err}</p>}</div>;
  const set = (k: string) => (e: Any) => setD({ ...d, [k]: e.target.value });
  return (
    <div className={st.lesson}>
      <h2 className={st.h2}>Lesson from this case</h2>
      <p className={st.fine}>Drafted from the investigation and your results. Change what is wrong, then confirm.</p>
      <div className={st.grid}>
        <label>Cause<select value={d.investigation_status} onChange={set("investigation_status")}>
          <option value="cause_probable">Probable</option><option value="cause_unknown">Not found</option><option value="cause_confirmed">Confirmed (inspection, reproduction or a fix that worked)</option></select></label>
        {d.investigation_status !== "cause_unknown" && <label>Where<select value={d.cause_domain} onChange={set("cause_domain")}>
          {domains.map((x: Any) => <option key={x.id} value={x.id}>{x.label}</option>)}</select></label>}
        {d.investigation_status === "cause_confirmed" && <label>Confirmed by<select value={["inspection", "reproduction", "fix_resolved_it"].includes(d.confirmed_by) ? d.confirmed_by : "fix_resolved_it"} onChange={set("confirmed_by")}>
          <option value="inspection">Inspection</option><option value="reproduction">Reproduction</option><option value="fix_resolved_it">A fix that resolved it</option></select></label>}
        <label>Machine now<select value={d.resolution_status} onChange={set("resolution_status")}>
          <option value="not_yet_repaired">Not repaired yet</option><option value="resolved">Resolved</option><option value="temporary_workaround">Workaround in place</option></select></label>
      </div>
      {d.investigation_status !== "cause_unknown" && <label className={st.wide}>What caused it<textarea className={st.text} rows={2} value={d.cause} onChange={set("cause")} /></label>}
      <label className={st.wide}>Fix (if any)<input className={st.input} value={d.fix || ""} onChange={set("fix")} placeholder="what was changed" /></label>
      <label className={st.wide}>Reuse only when<input className={st.input} value={d.applies_when || ""} onChange={set("applies_when")} placeholder="firmware, configuration or signature that must match" /></label>
      <p className={st.fine}>Evidence kept with it: {d.evidence_note || "none"}</p>
      <div className={st.row}><button className={st.primary} onClick={confirm} disabled={busy || (d.investigation_status !== "cause_unknown" && !d.cause?.trim())}>{busy ? "Saving…" : "Confirm and close"}</button>
        <button className={st.ghost} onClick={() => setOpen(false)}>Not yet</button></div>
      {err && <p className={st.err}>{err}</p>}
    </div>
  );
}
