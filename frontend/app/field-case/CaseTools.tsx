"use client";
/* Everything an engineer uses to look at the data and argue with Veyra about it:
   - DataPanel: plots and tables of what the log contains, decoded from its bytes, and a healthy session set against it.
   - NormalPanel: for a recording, each axis against this machine's own healthy behaviour, and what was set aside as routine.
   - Discuss: the engineer proposes a cause or asks a question and Veyra checks it against the data at once; Veyra
     proposes and the engineer verifies, and that feedback reshapes where the fault can be.
   - downloadReport: the case as one HTML file a person or an AI assistant can read. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import t from "./tools.module.css";
import { plotFor, Waveform } from "./Waveform";
import * as X from "../../lib/xrce-log";
import * as K from "../../lib/run-knowledge";
import { packetTimeline, ratioBars } from "../../lib/svg-chart";
import { buildReport, reportFileName, type Discussed, type Feedback } from "../../lib/case-report";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
type Call = (path: string, init?: RequestInit) => Promise<Any>;
export type { Discussed, Feedback };
export type Talk = { thread: Discussed[]; feedback: Record<string, Feedback> };
export const EMPTY_TALK: Talk = { thread: [], feedback: {} };

// ---------------------------------------------------------------------------------------------------------------
// The case's material, loaded once: the original lines (a log case) and the latest investigation.

export type Material = { lines: string[]; log: X.AgentLog | null; inv: Any | null; investigation: Any | null; loaded: boolean };

export function useMaterial(caseId: string | null, call: Call, logCase: boolean): Material {
  const [m, setM] = useState<Material>({ lines: [], log: null, inv: null, investigation: null, loaded: false });
  useEffect(() => {
    setM({ lines: [], log: null, inv: null, investigation: null, loaded: false });
    if (!caseId || !logCase) { setM((x) => ({ ...x, loaded: true })); return; }
    let live = true;
    Promise.all([call(`/engine/cases/${caseId}/log`).catch(() => ({ lines: [] })), call(`/engine/cases/${caseId}/investigation`).catch(() => ({}))]).then(([l, d]) => {
      if (!live) return;
      const lines: string[] = l.lines || [];
      const pb = d.playback?.investigations;
      const investigation = pb?.length ? pb[pb.length - 1] : d.investigation || null;
      setM({ lines, log: X.looksLikeAgentLog(lines) ? X.parseAgentLog(lines) : null, inv: investigation?.result || null, investigation, loaded: true });
    });
    return () => { live = false; };
  }, [caseId, logCase, call]);
  return m;
}

/** Hypotheses with the engineer's feedback applied: what the engineer checked overrides what the data alone suggested. */
export function withFeedback(hyps: Any[] | undefined, fb: Record<string, Feedback>): Any[] {
  return (hyps || []).map((h: Any) => {
    const f = fb[h.id];
    if (!f || f.verdict === "unsure") return h;
    return { ...h, status: f.verdict === "ruled_out" ? "ruled_out" : "leading", because: `Your check: ${f.note || (f.verdict === "ruled_out" ? "not this" : "it is this")}`, lines: [] };
  });
}

const timelineSvg = (log: X.AgentLog, highlight?: number[]) => {
  const s = log.sessions[0];
  return packetTimeline(log.packets.map((p) => ({ n: p.n, t: p.t, len: p.len, dir: p.dir, bad: p.complete && !p.xrce.wellFormed, zero: p.allZero, line: p.line, complete: p.complete })),
    { session: s && s.opened != null ? { from: s.opened, to: s.closed ?? s.opened, label: s.closed != null ? `session open ${((s.closed - s.opened) * 1000).toFixed(0)} ms` : "session open" } : null, highlight });
};

function Svg({ svg, label }: { svg: string; label: string }) {
  return <div className={t.svg} role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function DataTable({ table, max = 0, mono = [] }: { table: X.Table | K.Table; max?: number; mono?: number[] }) {
  const [all, setAll] = useState(false);
  const rows = max && !all ? table.rows.slice(0, max) : table.rows;
  return (
    <div className={t.tableWrap}>
      <table className={t.table}>
        <thead><tr>{table.cols.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => {
          const s = String(c);
          const cls = s.startsWith("✕") ? t.bad : s.startsWith("✓") ? t.good : /outside normal/.test(s) ? t.bad : "";
          return <td key={j} className={`${cls} ${mono.includes(j) ? t.mono : ""}`}>{c}</td>; })}</tr>)}</tbody>
      </table>
      {max > 0 && table.rows.length > max && <button className={t.more} onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${table.rows.length} rows`}</button>}
    </div>
  );
}

/** The log case's data, drawn: every packet on a time axis, decoded, and a healthy session set against this one. */
export function DataPanel({ m, onAsk }: { m: Material; onAsk?: (q: string) => void }) {
  if (!m.log || !m.log.packets.length) return null;
  const log = m.log;
  const ex = X.expectations(log);
  const fail = ex.filter((e) => e.ok === false).length;
  const zero = log.packets.find((p) => p.allZero && p.dir === "to_agent");
  const s = log.sessions[0];
  const span = (log.packets[log.packets.length - 1].t - log.packets[0].t) * 1000;
  return (
    <div className={t.panel}>
      <div className={t.panelHead}>
        <h2 className={t.h2}>What the data shows</h2>
        <span className={t.dim}>decoded from the bytes in the log · {log.packets.length} packets · {span.toFixed(0)} ms</span>
      </div>
      <figure className={t.figure}>
        <Svg svg={timelineSvg(log, zero ? [zero.n] : [])} label="Every packet between the board and the agent, over time" />
        <figcaption>{s && s.closed != null ? `The session lasted ${((s.closed - (s.opened ?? s.closed)) * 1000).toFixed(0)} ms. ` : ""}{zero ? `The ${zero.len}-byte all-zero packet (outlined, line ${zero.line}) ended it. ` : ""}
          Then the board kept sending a {X.repeats(log)[0]?.len ?? ""}-byte packet that is not a valid message. Hover a bar for its line.</figcaption>
      </figure>
      <details className={t.fold}>
        <summary>Every packet, decoded ({log.packets.length})</summary>
        <DataTable table={X.packetTable(log)} mono={[1, 2, 5]} />
      </details>
      <div className={t.subhead}>
        <h3 className={t.h3}>A healthy session, against this one</h3>
        <span className={fail ? t.badPill : t.goodPill}>{fail} of {ex.length} expectations fail</span>
      </div>
      <p className={t.note}>Left: how a micro-ROS (DDS-XRCE) session behaves when it works. Right: what this log shows. Knowing what normal looks like is what makes the first failure stand out.</p>
      <DataTable table={{ cols: ["A healthy session", "This log"], rows: ex.map((e) => [e.expected, `${e.ok === false ? "✕" : e.ok ? "✓" : "•"} ${e.observed}`]) }} />
      {onAsk && <p className={t.invite}>Disagree, or suspect something else? <button className={t.link} onClick={() => onAsk("")}>Discuss it with Veyra</button>. It checks what you suggest against these bytes.</p>}
    </div>
  );
}

/** A recording: every axis against its own healthy range, the routine set aside, the outputs against their range. */
export function NormalPanel({ run, onAsk }: { run: Any; labels?: Record<string, string>; onAsk?: (q: string) => void }) {
  const A = K.axes(run);
  const routine = K.treatedAsNormal(run);
  const outT = K.outputRanges(run);
  const bad = A.filter((r) => r.status === "unusual");
  if (!A.length && !routine.length && !outT) return null;
  const sources = [...new Set(A.map((r) => r.source))];
  return (
    <section className={t.section}>
      <div className={t.panelHead}>
        <h2 className={t.h2}>Normal for this machine, against this case</h2>
        {A.length > 0 && <span className={bad.length ? t.badPill : t.goodPill}>{bad.length} of {A.length} axes outside normal</span>}
      </div>
      <p className={t.note}>Noise and short spikes (a foot touching down, a bump, an unexpected contact) are not faults. Veyra judges each axis against this machine&apos;s own healthy data{sources.length ? ` (${sources.join("; ")})` : ""}, at the same command size, and only flags a sustained departure.</p>
      {A.length > 0 && <>
        <figure className={t.figure}>
          <Svg svg={ratioBars(A.map((r) => ({ label: r.axis, value: r.ratio, flagged: r.status === "unusual", note: `${r.statistic} error ${r.now.toFixed(2)} vs healthy ${r.normal.toFixed(2)} ${r.unit}` })), { limitLabel: "same as healthy" })}
            label="Each axis's tracking error against its healthy error" />
          <figcaption>Bars: this case&apos;s tracking error ÷ the same machine&apos;s healthy error (1.0 = the same). Red: the tracking rule found a sustained departure (rolling 3 s median above its limit for ≥ 2 s). A bar slightly past 1.0 without red is noise inside normal.</figcaption>
        </figure>
        <details className={t.fold}><summary>The numbers ({A.length} axes)</summary><DataTable table={K.axisTable(A)} /></details>
      </>}
      {routine.length > 0 && <>
        <h3 className={t.h3}>Set aside as routine, and why ({routine.reduce((n, g) => n + g.count, 0)} events)</h3>
        <DataTable table={{ cols: ["Events", "Example", "Why it is normal here"], rows: routine.map((g) => [g.count, g.example, g.reason]) }} />
        <p className={t.note}>Routine events stay in the record. Nothing is hidden; they just do not count as evidence of a fault.</p>
      </>}
      {outT && <><h3 className={t.h3}>Actuator outputs against their normal range</h3><DataTable table={outT} /></>}
      {onAsk && <p className={t.invite}>Think a spike matters, or that a flagged axis is normal? <button className={t.link} onClick={() => onAsk("")}>Discuss it with Veyra</button>.</p>}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Discussion.

type Proposal = { id: string; area: string; claim: string; status: string; ask: string };
const VLABEL: Record<string, string> = { supports: "Data agrees", against: "Data disagrees", cannot_tell: "Data can't tell", facts: "From the data",
  unusual: "Unusual for this machine", normal: "Normal for this machine" };
const FLABEL: Record<string, string> = { confirmed: "It is this", ruled_out: "Not this", unsure: "Not sure" };
const now = () => new Date().toISOString();
const PROPOSING = /\b(i think|i suspect|maybe|might be|could it be|could be|probably|my guess|i bet|isn'?t it|it'?s the|it is the|blame)\b/i;

function proposals(m: Material, run: Any, fb: Record<string, Feedback>): Proposal[] {
  if (m.inv?.hypotheses?.length) {
    return withFeedback(m.inv.hypotheses, {}).filter((h: Any) => h.status !== "ruled_out" || fb[h.id]).map((h: Any) => ({ id: h.id, area: h.area, claim: h.claim, status: h.status,
      ask: h.status === "leading" ? "Veyra's leading explanation. Does your evidence agree?" : "Still possible. Have you checked it?" }));
  }
  if (run) return K.unusual(run).slice(0, 5).map((o, i) => ({ id: `f${i}:${o.rule}`, area: o.text.split(/[:(]/)[0].slice(0, 60), claim: o.text, status: "open", ask: "Veyra flags this. A real fault, or normal for this machine?" }));
  return [];
}

export function Discuss({ open, onClose, caseId, call, m, run, labels, talk, setTalk, agent, readOnly, onJob }: {
  open: boolean; onClose: () => void; caseId: string; call: Call; m: Material; run: Any; labels: Record<string, string>; talk: Talk; setTalk: (f: (t: Talk) => Talk) => void;
  agent: Any; readOnly: boolean; onJob: (jobId: string) => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ id: string; verdict: Feedback["verdict"] } | null>(null);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logMode = !!m.log;
  const hyps = withFeedback(m.inv?.hypotheses, talk.feedback);
  const props = proposals(m, run, talk.feedback);
  const suggestions = logMode ? X.CHECKS.filter((c) => c.id !== "summary").map((c) => c.ask).slice(0, 6) : run ? K.RUN_SUGGESTIONS(run) : [];
  const canAI = !!agent?.configured && !readOnly;

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 80); }, [open]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [talk.thread.length]);
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);

  const related = useCallback((about: RegExp | null) => (about ? hyps.filter((h: Any) => about.test(h.area)) : []), [hyps]);

  async function ask(text: string) {
    const question = text.trim();
    if (!question) return;
    setQ("");
    const mine: Discussed = { at: now(), who: "engineer", text: question };
    const proposing = PROPOSING.test(question);
    let replies: Discussed[] = [];
    if (logMode && m.log) {
      replies = X.answer(m.log, question).map((a) => {
        const rel = related(a.about);
        const view = rel.length ? `Veyra's current view: ${rel.map((h: Any) => `${h.area} is ${String(h.status).replace("_", " ")}`).join("; ")}.` : "";
        return { at: now(), who: "veyra", source: "checked on the log", title: proposing && a.claim ? `You suggested: ${a.claim.toLowerCase()}` : a.title, verdict: a.verdict, text: a.text,
          points: [...a.points, ...(view ? [view] : []), ...(a.settle ? [`What would settle it: ${a.settle}`] : [])], table: a.table, lines: a.lines } as Discussed;
      });
    } else if (run) {
      replies = K.answerRun(run, question, labels).map((a) => ({ at: now(), who: "veyra", source: "checked on the recording", title: a.title, verdict: a.verdict, text: a.text, points: a.points, table: a.table,
        plots: a.plots } as Discussed & { plots?: Any }));
    }
    setTalk((x) => ({ ...x, thread: [...x.thread, mine, ...replies] }));
    if (replies.length) return;
    if (canAI && !logMode) {
      setBusy(true);
      try {
        await call(`/engine/cases/${caseId}/agent`, { method: "POST", body: JSON.stringify({ actor: "Veyra agent", question }) });
        const led = await call(`/engine/cases/${caseId}/ledger`);
        const e = [...(led.entries || led || [])].reverse().find((x: Any) => x.kind === "agent_report" && x.payload?.question === question);
        const r = e?.payload?.report || {};
        setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", source: "AI investigator", verdict: r.premise?.status === "supported" ? "supports" : r.premise?.status === "not_supported" ? "against" : "cannot_tell",
          text: r.answer?.text || "No answer came back.", points: r.premise?.explanation ? [r.premise.explanation] : [] }] }));
      } catch (err: Any) {
        setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", text: `The AI investigator could not answer: ${err.message}` }] }));
      } finally { setBusy(false); }
      return;
    }
    setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", verdict: "cannot_tell", title: "Not something this data can answer yet",
      text: logMode ? "I check questions against the decoded log. That one doesn't map to anything in it. These I can check right now:" : "I couldn't match that to a signal or finding in this recording. Try naming a joint, an axis or an output, or one of these:",
      points: canAI || logMode ? [] : ["Free-form questions go to Veyra's AI investigator, which is switched off on this public demo."] }] }));
  }

  function saveFeedback() {
    if (!pending) return;
    const p = props.find((x) => x.id === pending.id);
    if (!p) return;
    const f: Feedback = { id: p.id, area: p.area, verdict: pending.verdict, note: note.trim(), at: now() };
    const nextFb = { ...talk.feedback, [p.id]: f };
    const after = withFeedback(m.inv?.hypotheses, nextFb);
    const standing = after.filter((h: Any) => h.status !== "ruled_out");
    const lead = after.find((h: Any) => h.status === "leading");
    const points: string[] = [];
    let verdict: string = "facts";
    if (logMode && m.log) {
      const onArea = X.CHECKS.map((c) => c.run(m.log!)).filter((a): a is X.Answer => !!a && !!a.about && a.about.test(p.area));
      const against = onArea.filter((a) => a.verdict === "against"), fits = onArea.filter((a) => a.verdict === "supports");
      if (pending.verdict === "confirmed" && against.length) { verdict = "against"; points.push(`Worth a second look: the log points the other way. ${against[0].text} ${against[0].points[0] || ""}`); }
      else if (pending.verdict === "confirmed" && fits.length) { verdict = "supports"; points.push(`The log fits: ${fits[0].text}`); }
      else if (pending.verdict === "ruled_out" && against.length) { verdict = "supports"; points.push(`The log agrees: ${against[0].text} ${against[0].points[0] || ""}`); }
      else if (pending.verdict === "ruled_out" && fits.length) { verdict = "cannot_tell"; points.push(`Recorded, but note the log fits it: ${fits[0].text} Your bench check outranks the log here.`); }
    }
    if (m.inv?.hypotheses?.length) points.push(standing.length ? `Still possible: ${standing.map((h: Any) => h.area).join(", ")}.` : "Nothing is left standing. The cause is outside what Veyra listed, so the next round has to widen the search.");
    if (lead && pending.verdict !== "unsure") points.push(`Leading now: ${lead.area}.`);
    if (!lead && standing.length && m.inv?.hypotheses?.length) points.push("No explanation leads any more; the next check should split the ones still standing.");
    const text = pending.verdict === "unsure" ? `Noted as unsure. ${p.area} keeps its status.` : `Recorded as your finding: ${p.area} ${pending.verdict === "ruled_out" ? "ruled out" : "confirmed"}${f.note ? ` (${f.note})` : ""}. Where the fault can be has been updated.`;
    setTalk((x) => ({ feedback: nextFb, thread: [...x.thread,
      { at: now(), who: "engineer", text: `${p.area}: ${FLABEL[pending.verdict].toLowerCase()}${f.note ? `. ${f.note}` : ""}` },
      { at: now(), who: "veyra", source: "your feedback", verdict, title: "Your check, applied", text, points }] }));
    setPending(null); setNote("");
  }

  async function sendToVeyra() {
    const fbList = Object.values(talk.feedback);
    if (!fbList.length || !m.investigation) return;
    setBusy(true);
    try {
      const obs = fbList.map((f) => `${f.area}: ${f.verdict === "ruled_out" ? "ruled out" : f.verdict === "confirmed" ? "confirmed" : "unsure"}${f.note ? ` (${f.note})` : ""}`).join("; ");
      const out = await call(`/engine/cases/${caseId}/finding`, { method: "POST", body: JSON.stringify({ action: "Engineer feedback from the discussion", outcome_selected: null,
        result: "found", observations: `Engineer checked: ${obs}`, investigation_entry: m.investigation.entry_id }) });
      setSent(now());
      if (out.job_id) onJob(out.job_id);
    } catch (e: Any) {
      setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", text: `Could not send it: ${e.message}` }] }));
    } finally { setBusy(false); }
  }

  if (!open) return null;
  const last = talk.thread[talk.thread.length - 1];
  return (
    <>
      <div className={t.scrim} onClick={onClose} aria-hidden />
      <aside className={t.drawer} role="dialog" aria-modal="false" aria-label="Discuss this case with Veyra">
        <header className={t.dHead}>
          <div>
            <h2 className={t.dTitle}>Discuss with Veyra</h2>
            <p className={t.dSub}>{logMode ? "Suggest a cause or ask a question. Veyra checks it against the decoded log in your browser, in a fraction of a second, and cites the lines."
              : "Ask about a joint, an axis or an output. Veyra answers from this machine's healthy behaviour and the findings in this recording."}</p>
          </div>
          <button className={t.x} onClick={onClose} aria-label="Close the discussion">×</button>
        </header>

        <div className={t.dBody}>
          {props.length > 0 && (
            <details className={t.propose} open={!talk.thread.length}>
              <summary>Veyra proposes, you verify ({props.length})</summary>
              <p className={t.note}>{logMode ? "Tell Veyra what you have checked on the bench. Your answer reshapes where the fault can be and goes into the report as your finding."
                : "Confirm or reject what Veyra flagged. Your answer goes into the report as your finding."}</p>
              <ul className={t.props}>
                {props.map((p) => {
                  const f = talk.feedback[p.id];
                  const shown = hyps.find((h: Any) => h.id === p.id);
                  return (
                    <li key={p.id} className={t.prop}>
                      <div className={t.propHead}><b>{p.area}</b>{shown && <span className={`${t.hs} ${t["hs_" + shown.status] || ""}`}>{String(shown.status).replace("_", " ")}</span>}</div>
                      <span className={t.propClaim}>{p.claim}</span>
                      {f ? <span className={t.done}>You said: {FLABEL[f.verdict]}{f.note ? `. ${f.note}` : ""} <button className={t.link} onClick={() => setTalk((x) => { const fb = { ...x.feedback }; delete fb[p.id]; return { ...x, feedback: fb }; })}>Undo</button></span>
                        : <>
                          <span className={t.propAsk}>{p.ask}</span>
                          <div className={t.row}>{(["confirmed", "ruled_out", "unsure"] as const).map((v) => (
                            <button key={v} className={pending?.id === p.id && pending.verdict === v ? t.chipOn : t.chip} aria-pressed={pending?.id === p.id && pending.verdict === v}
                              onClick={() => { setPending({ id: p.id, verdict: v }); setNote(""); }}>{logMode ? FLABEL[v] : v === "confirmed" ? "Real fault" : v === "ruled_out" ? "Normal for this machine" : "Not sure"}</button>))}</div>
                          {pending?.id === p.id && (
                            <div className={t.noteForm}>
                              <label className={t.label} htmlFor={`fb-${p.id}`}>What did you see? (optional, it helps the next step)</label>
                              <input id={`fb-${p.id}`} className={t.input} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. buffer valid at the send call, zeros in tcpdump"
                                onKeyDown={(e) => { if (e.key === "Enter") saveFeedback(); }} />
                              <div className={t.row}><button className={t.primary} onClick={saveFeedback}>Record</button><button className={t.ghost} onClick={() => setPending(null)}>Cancel</button></div>
                            </div>)}
                        </>}
                    </li>);
                })}
              </ul>
              {Object.keys(talk.feedback).length > 0 && logMode && (
                canAI && m.investigation
                  ? <div className={t.row}><button className={t.primary} disabled={busy || !!sent} onClick={sendToVeyra}>{sent ? "Sent. Veyra is investigating" : busy ? "Sending…" : "Send to Veyra to investigate again"}</button></div>
                  : <p className={t.note}>On a workspace with the AI investigator, this sends your checks to Veyra and it runs the next round. On this public demo your checks update the picture here and go into the report.</p>)}
            </details>
          )}

          {!talk.thread.length && !props.length && <p className={t.note}>Start with one of the questions below, or write your own suspicion in plain words.</p>}
          <ol className={t.thread} aria-live="polite">
            {talk.thread.map((d, i) => <Message key={i} d={d} log={m.log} run={run} caseId={caseId} call={call} />)}
            {busy && <li className={t.veyra}><span className={t.who}>Veyra</span><p className={t.dim}>Working on it…</p></li>}
          </ol>
          {last?.who === "veyra" && last.verdict === "cannot_tell" && last.title?.startsWith("Not something") && (
            <div className={t.chips}>{suggestions.map((s) => <button key={s} className={t.chip} onClick={() => ask(s)}>{s}</button>)}</div>)}
          <div ref={endRef} />
        </div>

        <footer className={t.dFoot}>
          {!talk.thread.length && <div className={t.chips}>{suggestions.map((s) => <button key={s} className={t.chip} onClick={() => ask(s)}>{s}</button>)}</div>}
          <form className={t.form} onSubmit={(e) => { e.preventDefault(); ask(q); }}>
            <label htmlFor="discuss-q" className={t.hidden}>Your question or suspected cause</label>
            <textarea id="discuss-q" ref={inputRef} className={t.ask} rows={2} value={q} maxLength={600} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q); } }}
              placeholder={logMode ? "e.g. I think a bad switch is corrupting the packets" : "e.g. Is the left front knee just noisy from contact?"} />
            <button className={t.primary} type="submit" disabled={!q.trim() || busy}>Check</button>
          </form>
          <p className={t.fine}>{logMode ? "Checks run on the decoded log in this browser. Nothing is sent anywhere."
            : `Answers come from this case's baselines and findings.${canAI ? " Other questions go to the AI investigator." : " Free-form questions need the AI investigator, which is off on the public demo."}`}</p>
        </footer>
      </aside>
    </>
  );
}

function Message({ d, log, run, caseId, call }: { d: Discussed & { plots?: Any[] }; log: X.AgentLog | null; run: Any; caseId: string; call: Call }) {
  if (d.who === "engineer") return <li className={t.me}><span className={t.who}>You</span><p>{d.text}</p></li>;
  const quoted = log && d.lines?.length ? [...new Set(d.lines)].filter((n) => n > 0 && n <= log.lines.length).slice(0, 4) : [];
  const highlight = log && d.lines?.length ? log.packets.filter((p) => d.lines!.includes(p.line)).map((p) => p.n) : [];
  return (
    <li className={t.veyra}>
      <span className={t.who}>Veyra{d.source ? ` · ${d.source}` : ""}</span>
      {d.verdict && <span className={`${t.verdict} ${t["v_" + d.verdict] || ""}`}>{VLABEL[d.verdict] || d.verdict}</span>}
      {d.title && <b className={t.mTitle}>{d.title}</b>}
      <p>{d.text}</p>
      {(d.points || []).length > 0 && <ul className={t.points}>{d.points!.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      {log && highlight.length > 0 && highlight.length < log.packets.length && <Svg svg={timelineSvg(log, highlight)} label="The packets this answer refers to, outlined" />}
      {d.table && <DataTable table={d.table} max={8} />}
      {quoted.length > 0 && <div className={t.quotes}>{quoted.map((n) => <div key={n} className={t.q}><code className={t.ln}>{n}</code><code className={t.raw}>{log!.lines[n - 1]}</code></div>)}</div>}
      {run && (d.plots || []).slice(0, 1).map((p: Any, i: number) => { const spec = plotFor(run, p.rule, p.text); return spec ? <Waveform key={i} caseId={caseId} spec={spec} load={call} compact /> : null; })}
    </li>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// The report.

export function useReport({ caseId, data, m, talk }: { caseId: string | null; data: Any; m: Material; talk: Talk }) {
  return useMemo(() => () => {
    if (!caseId || !data) return;
    const run = data.latest_run;
    const c = data.case || {};
    const inv = m.inv || {};
    const labels: Record<string, string> = Object.fromEntries(Object.entries(run?.machine_profile?.domains || {}).map(([k, v]: [string, Any]) => [k, v.label]));
    const dataSections: { title: string; svg?: string; caption?: string; table?: X.Table }[] = [];
    const normal: { title: string; intro?: string; table: X.Table }[] = [];
    if (m.log) {
      const zero = m.log.packets.find((p) => p.allZero);
      dataSections.push({ title: "Every packet between the board and the agent", svg: timelineSvg(m.log, zero ? [zero.n] : []), caption: "Bar height is bytes. Red: every byte zero. Amber: not a valid XRCE message.", table: X.packetTable(m.log) });
      const ex = X.expectations(m.log);
      normal.push({ title: "A healthy micro-ROS session, against this log", intro: "Left column: protocol knowledge. Right column: computed from this log.", table: { cols: ["A healthy session", "This log"], rows: ex.map((e) => [e.expected, `${e.ok === false ? "✕" : e.ok ? "✓" : "•"} ${e.observed}`]) } });
      const r = X.repeats(m.log)[0];
      if (r) { const a = X.answer(m.log, "what is the 13 byte packet")[0]; if (a?.table) dataSections.push({ title: `The repeating ${r.len}-byte packet`, caption: `${a.text} ${a.points.join(" ")}`, table: a.table }); }
    } else if (run) {
      const A = K.axes(run);
      if (A.length) dataSections.push({ title: "Tracking error against this machine's healthy error", svg: ratioBars(A.map((r) => ({ label: r.axis, value: r.ratio, flagged: r.status === "unusual" })), { limitLabel: "same as healthy" }),
        caption: "1.0 = the same as healthy data. Red: a sustained departure found by the tracking rule.", table: K.axisTable(A) });
      const outT = K.outputRanges(run);
      if (outT) dataSections.push({ title: "Actuator outputs", table: outT });
      const g = K.treatedAsNormal(run);
      if (g.length) normal.push({ title: "Set aside as routine, and why", table: { cols: ["Events", "Example", "Why it is normal here"], rows: g.map((x) => [x.count, x.example, x.reason]) } });
      const un = K.unusual(run);
      if (un.length) normal.push({ title: "Unusual for this machine", table: { cols: ["Area", "Finding"], rows: un.map((o) => [labels[o.domain] || o.domain, o.text]) } });
    }
    const html = buildReport({
      title: c.title || caseId, caseId, url: typeof window !== "undefined" ? window.location.href : undefined, synthetic: !!c.synthetic,
      provenance: c.synthetic ? "Synthetic data, not field evidence" : (data.imports || []).map((i: Any) => i.filename).filter(Boolean).join(", "),
      status: inv.status ? ({ cause_probable: "Probable cause", cause_unknown: "Cause not established", no_failure_found: "No failure found" } as Any)[inv.status] || inv.status : undefined,
      headline: inv.headline || c.title, explanation: inv.explanation, confidence: inv.status === "cause_probable" ? inv.confidence : undefined,
      hypotheses: withFeedback(inv.hypotheses, talk.feedback), nextAction: inv.next_action, evidence: inv.evidence, checked: inv.checked, effects: inv.effects, unknowns: inv.unknowns,
      steps: m.investigation?.steps, investigator: m.investigation?.mode === "agent" ? `Investigated by ${m.investigation?.model || "the AI investigator"}; every quoted line verified against the file` : undefined,
      data: dataSections, normal, discussion: talk.thread, feedback: Object.values(talk.feedback), log: m.lines,
    });
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = reportFileName(c.title || "", caseId);
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }, [caseId, data, m, talk]);
}
