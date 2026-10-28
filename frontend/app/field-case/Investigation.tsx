"use client";
/* The case, action first: Veyra investigates; the engineer only does what Veyra cannot do from the data.
   What happened -> what Veyra already checked -> the one next action -> what you found (feeds the next step). */
import React, { useCallback, useEffect, useRef, useState } from "react";
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

  useEffect(() => { setData(null); setJob(null); setError(null); load(); if (jobId && !jobId.startsWith("walkthrough")) follow(jobId); return () => { polling.current = null; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start(allowModel: boolean) {
    setError(null);
    try { const { job_id } = await call(`/engine/cases/${caseId}/investigate`, { method: "POST", body: JSON.stringify({ allow_model: allowModel }) }); follow(job_id); }
    catch (e: Any) { setError(e.message); }
  }

  if (job) return <Live job={job} />;
  if (!data) return <section className={st.surface}><p className={st.dim}>Loading…</p></section>;
  if (data.playback) return <Playback pb={data.playback} />;
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
  return (
    <ResultView inv={inv} findings={data.findings || []}
      banner={inv.mode !== "agent" ? (
        <div className={st.banner}>
          <b>Rule-based reading only.</b> {inv.fallback_reason}.{" "}
          {agent.configured ? <button className={st.inline} onClick={() => start(true)}>Let Veyra investigate with AI</button>
            : <span>Connect the AI investigator (Claude Code sign-in on this computer) to have Veyra run the checks for you.</span>}
        </div>) : null}
      prev={data.previous_hypotheses}
      next={<NextAction caseId={caseId} call={call} next={(inv.result || {}).next_action} entry={inv.entry_id} closed={data.closed} onRecorded={(j) => j ? follow(j) : load()} hyps={(inv.result || {}).hypotheses} />}
      footer={<>
        {!data.closed && <ReviewConfirm inv={inv} submit={async (a) => { await call(`/engine/cases/${caseId}/review`, { method: "POST", body: JSON.stringify(a) }); load(); onChanged(); }} />}
        {data.closed && <p className={st.fine}>This case is closed. Its lesson is used when a similar failure comes in.</p>}
        {error && <p className={st.err}>{error}</p>}
      </>} />
  );
}

function ResultView({ inv, findings, banner, next, footer, prev }: { inv: Any; findings: Any[]; banner?: React.ReactNode; next: React.ReactNode; footer?: React.ReactNode; prev?: Any[] | null }) {
  const r = inv.result || {};
  const status = STATUS[r.status] || STATUS.cause_unknown;
  return (
    <section className={st.surface}>
      {banner}
      <header className={st.head}>
        <span className={`${st.status} ${st[status.cls]}`}>{status.label}{r.confidence && r.status === "cause_probable" ? ` · ${r.confidence} confidence` : ""}</span>
        <h1 className={st.headline}>{r.headline}</h1>
        {r.explanation && <p className={st.lead}>{named(r.explanation, r.hypotheses)}</p>}
        {inv.mode === "agent" && <p className={st.fine}>Veyra ran {inv.steps.length} check{inv.steps.length === 1 ? "" : "s"} on the original data in {inv.seconds} s · every quoted line verified against the file{(inv.dropped || []).length ? ` · ${inv.dropped.length} unverifiable statement${inv.dropped.length === 1 ? "" : "s"} removed` : ""}</p>}
      </header>

      {(r.hypotheses || []).length > 0 && <FaultSpace hyps={r.hypotheses} prev={prev} />}

      {next}

      {(r.evidence || []).length > 0 && (
        <div className={st.block}>
          <h2 className={st.h2}>Why Veyra thinks so</h2>
          <ul className={st.lines}>
            {r.evidence.map((e: Any, i: number) => (
              <li key={i}><code className={st.ln}>{e.line}</code><div><code className={st.raw}>{e.quote}</code><span className={st.shows}>{named(e.shows, r.hypotheses)}</span></div></li>))}
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
                  <div><b>{named(c.check, r.hypotheses)}</b><span className={st.result}>{named(c.result, r.hypotheses)}</span>
                    {c.about && <span className={st.about}>{v.label}: {named(c.about, r.hypotheses)}</span>}</div>
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

      {findings.length > 0 && (
        <div className={st.block}>
          <h2 className={st.h2}>What you found</h2>
          <ul className={st.findings}>
            {findings.map((f: Any) => (
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

      {footer}
    </section>
  );
}

function Live({ job, subtitle }: { job: Any; subtitle?: string }) {
  const steps: Any[] = job.steps || [];
  return (
    <section className={st.surface} aria-live="polite">
      <span className={`${st.status} ${st.working}`}>Investigating</span>
      <h1 className={st.headline}>{job.step === "Veyra is investigating" ? "Veyra is investigating the data" : job.step || "Starting"}</h1>
      <p className={st.lead}>{subtitle || "You don't need to stay on this page. Each check below runs on the original data."}</p>
      <ol className={st.liveSteps}>
        {steps.map((s: Any) => <li key={s.id} className={st.done}><span>{s.label}</span><span className={st.dim}>{s.summary}</span></li>)}
        <li className={st.now}><span className={st.pulse} aria-hidden /> <span>{steps.length ? "Deciding the next check" : job.step}</span></li>
      </ol>
    </section>
  );
}

type Demo = { prefill: (o: Any) => string; submit: (choice: Any, notes: string) => void };
function NextAction({ caseId, call, next, entry, closed, onRecorded, demo, hyps = [] }:
  { caseId: string; call: Call; next: Any; entry: string; closed: boolean; onRecorded: (jobId: string | null) => void; demo?: Demo; hyps?: Any[] }) {
  const area = (id: string) => (hyps.find((h: Any) => h.id === id) || {}).area || id;
  const [choice, setChoice] = useState<Any>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  if (!next) return null;
  const outcomes: Any[] = (next.outcomes || []).map((o: Any) => ({ ...o, result: "found" }));
  const cant = { label: "I can't do this", means: "", result: "cannot_do" };
  async function save() {
    if (demo) { demo.submit(choice, notes); setChoice(null); setNotes(""); return; }
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
      {next.why && <p className={st.why}>{named(next.why, hyps)}</p>}
      {(next.how || []).length > 0 && (
        <div className={st.howWrap}>
          <ol className={st.how}>{next.how.map((h: string, i: number) => <li key={i}>{h}</li>)}</ol>
          <button className={st.ghost} onClick={async () => { await navigator.clipboard?.writeText(`${next.action}\n${next.how.map((h: string, i: number) => `${i + 1}. ${h}`).join("\n")}`); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? "Copied" : "Copy steps"}</button>
        </div>
      )}
      {!closed && <>
        <p className={st.ask}>Run it. What did you find?</p>
        <div className={st.tree}>
          {outcomes.map((o, i) => (
            <button key={i} className={choice?.label === o.label ? st.outcomeOn : st.outcome} onClick={() => { setChoice(o); if (demo) setNotes(demo.prefill(o)); }} aria-pressed={choice?.label === o.label}>
              <b>{o.label}</b><span>{named(o.means, hyps)}</span>
              {(o.rules_out || []).length > 0 && <span className={st.fx}><i>Rules out</i> {o.rules_out.map(area).join(", ")}</span>}
              {(o.keeps || []).length > 0 && <span className={st.fx}><i>Leaves</i> {o.keeps.map(area).join(", ")}</span>}
              {o.then && <span className={st.fx}><i>Then</i> {named(o.then, hyps)}</span>}
            </button>))}
        </div>
        <button className={choice?.result === "cannot_do" ? st.cantOn : st.cant} onClick={() => setChoice(cant)}>I can't do this — find another way</button>
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

/** A recorded investigation walked through step by step: Veyra reads the log, hands over the next action, takes the
 *  engineer's result, continues, and closes with a lesson. Nothing is written. */
function Playback({ pb }: { pb: Any }) {
  const invs: Any[] = pb.investigations;
  const [stage, setStage] = useState(0);
  const [shown, setShown] = useState(0);
  const [phase, setPhase] = useState<"running" | "result" | "closing" | "closed">("running");
  const [mine, setMine] = useState<Any[]>([]);
  const inv = invs[stage];
  useEffect(() => {
    if (phase !== "running") return;
    const steps = inv.steps || [];
    const t = setTimeout(() => (shown < steps.length ? setShown(shown + 1) : setPhase("result")), shown < steps.length ? 850 : 700);
    return () => clearTimeout(t);
  }, [phase, shown, stage]); // eslint-disable-line react-hooks/exhaustive-deps
  const top = () => { try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { /* ignore */ } };
  if (phase === "running") {
    return <Live job={{ step: "Veyra is investigating", steps: (inv.steps || []).slice(0, shown) }}
      subtitle={stage === 0 ? "Reading the log and running the checks it can do itself." : "Continuing with what you found."} />;
  }
  const recordedFinding = (o: Any) => (pb.findings || []).find((f: Any) => String(f.observations || "").startsWith(o.label));
  const demo: Demo = {
    prefill: (o) => { const f = recordedFinding(o); return f ? String(f.observations).replace(/^.*?\)\.\s*/, "") : ""; },
    submit: (choice, notes) => {
      const r = inv.result || {};
      setMine([...mine, { id: `demo-${mine.length}`, recorded_at: new Date().toISOString(), action: r.next_action?.action,
        result: choice.result === "cannot_do" ? "cannot_do" : "found", observations: [choice.result === "cannot_do" ? null : choice.label, notes || null].filter(Boolean).join(". ") }]);
      if (choice.result !== "cannot_do" && stage + 1 < invs.length) { setStage(stage + 1); setShown(0); setPhase("running"); top(); }
      else setPhase("closing");
    },
  };
  const close = (invs[invs.length - 1].recorded || {}).close || {};
  const footer = phase === "closing" ? (
    <ReviewConfirm inv={inv} submit={async () => { setPhase("closed"); }} />
  ) : phase === "closed" ? (
    <div className={st.lesson}>
      <h2 className={st.h2}>Case closed · lesson saved</h2>
      {close.cause && <p className={st.lessonNote}><b>Cause:</b> {close.cause}</p>}
      {close.fix && <p className={st.lessonNote}><b>Fix:</b> {close.fix}</p>}
      {close.applies_when && <p className={st.lessonNote}><b>Reused only when:</b> {close.applies_when}</p>}
      <p className={st.fine}>Written by Veyra from the investigation and your two answers.</p>
      <div className={st.row}><button className={st.ghost} onClick={() => { setStage(0); setShown(0); setMine([]); setPhase("running"); top(); }}>Replay from the start</button></div>
    </div>
  ) : null;
  return <ResultView inv={inv} findings={mine}
    prev={stage > 0 ? (invs[stage - 1].result || {}).hypotheses : null}
    next={phase === "result" ? <NextAction caseId="" call={async () => null} next={inv.result?.next_action} entry="" closed={false} onRecorded={() => undefined} demo={demo} hyps={inv.result?.hypotheses} /> : null}
    footer={footer} />;
}


/** The model refers to hypotheses by id (h1, h2); people read their names. */
function named(text: string | undefined, hyps: Any[] | undefined): string {
  if (!text || !hyps?.length) return text || "";
  return text.replace(/\bh(\d{1,2})\b/g, (m) => (hyps.find((h: Any) => h.id === m) || {}).area || m);
}

const H_STATUS: Record<string, string> = { leading: "Leading", open: "Open", weakened: "Weakened", ruled_out: "Ruled out" };
const H_ORDER: Record<string, number> = { leading: 0, open: 1, weakened: 2, ruled_out: 3 };

/** Where the fault can be: the hypotheses Veyra is weighing, and what the latest evidence did to them. */
function FaultSpace({ hyps, prev }: { hyps: Any[]; prev?: Any[] | null }) {
  const before: Record<string, string> = Object.fromEntries((prev || []).map((h: Any) => [h.id, h.status]));
  const moved = prev ? hyps.filter((h) => before[h.id] && before[h.id] !== h.status) : [];
  const ruled = hyps.filter((h) => h.status === "ruled_out").length;
  const sorted = [...hyps].sort((a, b) => (H_ORDER[a.status] ?? 9) - (H_ORDER[b.status] ?? 9));
  return (
    <div className={st.block}>
      <div className={st.spaceHead}>
        <h2 className={st.h2}>Where the fault can be</h2>
        <span className={st.dim}>{hyps.length - ruled} of {hyps.length} still possible</span>
      </div>
      {moved.length > 0 && (
        <p className={st.changed}>Your result changed the picture: {moved.map((h) => `${h.area} ${({ ruled_out: "ruled out", leading: "now leading", weakened: "weakened", open: "reopened" } as Any)[h.status]}`).join(" · ")}</p>
      )}
      <ul className={st.space}>
        {sorted.map((h) => (
          <li key={h.id} className={`${st.hyp} ${st["h_" + h.status] || ""} ${before[h.id] && before[h.id] !== h.status ? st.hMoved : ""}`}>
            <span className={st.hStatus}>{H_STATUS[h.status] || h.status}</span>
            <span className={st.hBody}><b className={st.hArea}>{h.area}</b><span className={st.hClaim}>{named(h.claim, hyps)}</span>
              {(h.status === "leading" || (before[h.id] && before[h.id] !== h.status)) && <span className={st.hWhy}>{named(h.because, hyps)}{(h.lines || []).length ? ` · line${h.lines.length > 1 ? "s" : ""} ${h.lines.slice(0, 4).join(", ")}` : ""}</span>}</span>
          </li>))}
      </ul>
    </div>
  );
}

/** Close by review: is the conclusion right, and did the machine recover? Veyra writes the rest. */
function ReviewConfirm({ inv, submit }: { inv: Any; submit: (answers: Any) => Promise<void> }) {
  const r = inv.result || {};
  const [open, setOpen] = useState(false);
  const [conclusion, setConclusion] = useState<string | null>(null);
  const [recovered, setRecovered] = useState<string | null>(null);
  const [correction, setCorrection] = useState("");
  const [fix, setFix] = useState(r.next_action?.action || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <div className={st.closeRow}><button className={st.ghost} onClick={() => setOpen(true)}>Review &amp; close</button><span className={st.fine}>Two questions. Veyra writes the record and the lesson.</span></div>;
  const pick = (v: string | null, set: (x: string) => void, opts: [string, string][]) => (
    <div className={st.row}>{opts.map(([k, l]) => <button key={k} className={v === k ? st.choiceOn : st.choice} onClick={() => set(k)} aria-pressed={v === k}>{l}</button>)}</div>);
  async function go() {
    setBusy(true); setErr(null);
    try { await submit({ conclusion, recovered, correction: correction.trim() || null, fix: recovered === "yes" ? fix.trim() || null : null }); }
    catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className={st.lesson}>
      <h2 className={st.h2}>Review &amp; close</h2>
      <p className={st.quote}>{r.explanation || r.headline}</p>
      <p className={st.ask2}>Is this conclusion right?</p>
      {pick(conclusion, setConclusion, [["correct", "Yes"], ["partly", "Partly"], ["wrong", "No"]])}
      {conclusion && conclusion !== "correct" && <label className={st.wide}>What is right?<textarea className={st.text} rows={2} value={correction} onChange={(e) => setCorrection(e.target.value)} /></label>}
      <p className={st.ask2}>Did the machine recover?</p>
      {pick(recovered, setRecovered, [["yes", "Yes"], ["not_yet", "Not yet"], ["unknown", "Don't know"]])}
      {recovered === "yes" && <label className={st.wide}>What fixed it?<input className={st.input} value={fix} onChange={(e) => setFix(e.target.value)} /></label>}
      <div className={st.row}><button className={st.primary} disabled={!conclusion || !recovered || busy || (conclusion === "partly" && !correction.trim())} onClick={go}>{busy ? "Closing…" : "Confirm and close"}</button>
        <button className={st.ghost} onClick={() => setOpen(false)}>Not yet</button></div>
      {err && <p className={st.err}>{err}</p>}
    </div>
  );
}

const CLASS_LABEL: Record<string, string> = { root_cause_candidate: "Possible cause", anomaly: "Anomaly", effect: "Effect of the failure", routine: "Routine" };

/** The log as Veyra read it: events grouped and classified, with the original lines. One click marks an event normal for this machine. */
export function LogEvidence({ caseId, call }: { caseId: string; call: Call }) {
  const [inv, setInv] = useState<Any>(null);
  const [log, setLog] = useState<string[]>([]);
  const [notes, setNotes] = useState<Record<string, Any>>({});
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    call(`/engine/cases/${caseId}/investigation`).then((d) => {
      const pb = d.playback?.investigations; setInv(pb?.length ? pb[pb.length - 1] : d.investigation);
      setNotes(Object.fromEntries((d.notes || []).map((n: Any) => [n.pattern, n])));
    }).catch((e) => setErr(e.message));
    call(`/engine/cases/${caseId}/log`).then((d) => setLog(d.lines || [])).catch(() => setLog([]));
  }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!inv) return <section className={st.surface}><p className={st.dim}>{err || "Loading…"}</p></section>;
  const events: Any[] = (inv.result || {}).events || [];
  const cls = (e: Any) => (notes[e.what]?.verdict === "normal" ? "routine" : notes[e.what]?.verdict === "relevant" && e.class === "routine" ? "anomaly" : e.class);
  async function mark(e: Any, verdict: string) {
    const body = { pattern: e.what, verdict, scope: "machine", lines: (e.lines || []).slice(0, 50) };
    setNotes({ ...notes, [e.what]: body });
    try { await call(`/engine/cases/${caseId}/event-note`, { method: "POST", body: JSON.stringify(body) }); } catch (x: Any) { setErr(x.message); }
  }
  const groups = ["root_cause_candidate", "anomaly", "effect", "routine"].map((c) => [c, events.filter((e) => cls(e) === c)] as const).filter(([, l]) => l.length);
  const raw = (n: number) => log[n - 1] ?? "";
  return (
    <section className={st.surface}>
      <header className={st.head}>
        <h1 className={st.headline} style={{ fontSize: 24 }}>The log, as Veyra read it</h1>
        <p className={st.fine}>{log.length} lines · {events.length} groups of events · routine events need no attention</p>
      </header>
      {groups.map(([c, list]) => {
        const body = (
          <ul className={st.events}>
            {list.map((e: Any, i: number) => (
              <li key={i} className={st.event}>
                <div className={st.eventHead}><b>{e.what}</b>
                  {c !== "routine" ? <button className={st.mini} onClick={() => mark(e, "normal")}>Normal for this machine</button>
                    : notes[e.what]?.verdict === "normal" ? <button className={st.mini} onClick={() => mark(e, "relevant")}>Undo</button>
                    : <button className={st.mini} onClick={() => mark(e, "relevant")}>Not routine</button>}
                </div>
                {e.why && <span className={st.shows}>{e.why}</span>}
                <ul className={st.lines}>{(e.lines || []).slice(0, 3).map((n: number) => <li key={n}><code className={st.ln}>{n}</code><div><code className={st.raw}>{raw(n)}</code></div></li>)}</ul>
                {(e.lines || []).length > 3 && <span className={st.fine}>+{e.lines.length - 3} more lines like this</span>}
              </li>))}
          </ul>);
        return c === "routine"
          ? <details key={c} className={st.fold}><summary>{CLASS_LABEL[c]} ({list.length})</summary>{body}</details>
          : <div key={c} className={st.block}><h2 className={st.h2}>{CLASS_LABEL[c]} ({list.length})</h2>{body}</div>;
      })}
      {log.length > 0 && <details className={st.fold}><summary>Full log ({log.length} lines)</summary>
        <ol className={st.full}>{log.map((l, i) => <li key={i}><code className={st.ln}>{i + 1}</code><code className={st.rawPlain}>{l}</code></li>)}</ol></details>}
      {err && <p className={st.err}>{err}</p>}
    </section>
  );
}

const STEP_ICON: Record<string, string> = { reported: "Reported", investigation: "Veyra", action: "Engineer", note: "Engineer", closure: "Closed" };

/** The case record Veyra keeps itself: decision → action → evidence → outcome, nobody types it. */
export function AutoRecord({ caseId, call, children }: { caseId: string; call: Call; children?: React.ReactNode }) {
  const [rec, setRec] = useState<Any>(null);
  const [inv, setInv] = useState<Any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    call(`/engine/cases/${caseId}/record`).then(setRec).catch((e) => setErr(e.message));
    call(`/engine/cases/${caseId}/investigation`).then((d) => setInv(d.investigation)).catch(() => undefined);
  }, [caseId, call]);
  useEffect(() => { load(); }, [load]);
  if (!rec) return <section className={st.surface}><p className={st.dim}>{err || "Loading…"}</p></section>;
  const s = rec.summary || {};
  return (
    <section className={st.surface}>
      <header className={st.head}>
        <h1 className={st.headline} style={{ fontSize: 24 }}>Case record</h1>
        <p className={st.fine}>Written by Veyra as the investigation happened{rec.integrity ? " · record intact" : " · INTEGRITY CHECK FAILED"}</p>
      </header>
      <div className={st.stats}>
        <div><b>{s.rounds}</b><span>investigation rounds</span></div>
        <div><b>{s.checks_run}</b><span>checks run by Veyra</span></div>
        <div><b>{s.engineer_actions}</b><span>actions by engineers</span></div>
        <div><b>{s.closed ? (s.recovered === "resolved" ? "Recovered" : "Closed") : "Open"}</b><span>{s.closed ? "conclusion confirmed" : "investigation"}</span></div>
      </div>
      <ol className={st.chain}>
        {(rec.chain || []).map((c: Any, i: number) => (
          <li key={i} className={st["c_" + c.type] || ""}>
            <span className={st.cWho}>{STEP_ICON[c.type] || c.type}</span>
            <div>
              <b>{c.title}</b> <span className={st.when}>{new Date(c.at).toLocaleString()}{c.by && c.type !== "investigation" ? ` · ${c.by}` : ""}</span>
              {c.detail && <span className={st.result}>{c.detail}</span>}
              {c.type === "investigation" && <span className={st.about}>{c.checks} checks{c.seconds ? ` in ${c.seconds} s` : ""}{(c.standing || []).length ? ` · still possible: ${c.standing.join(", ")}` : ""}{c.next_action ? ` · next: ${c.next_action}` : ""}</span>}
              {c.changes && (c.changes.ruled_out.length + c.changes.leading.length + c.changes.weakened.length) > 0 && (
                <span className={st.changedSmall}>{[c.changes.ruled_out.length ? `ruled out ${c.changes.ruled_out.join(", ")}` : "", c.changes.leading.length ? `now leading ${c.changes.leading.join(", ")}` : "", c.changes.weakened.length ? `weakened ${c.changes.weakened.join(", ")}` : ""].filter(Boolean).join(" · ")}</span>)}
            </div>
          </li>))}
      </ol>
      {!s.closed && inv && <ReviewConfirm inv={inv} submit={async (a) => { await call(`/engine/cases/${caseId}/review`, { method: "POST", body: JSON.stringify(a) }); load(); }} />}
      {children && <details className={st.fold}><summary>Full ledger and integrity details</summary>{children}</details>}
      {err && <p className={st.err}>{err}</p>}
    </section>
  );
}
