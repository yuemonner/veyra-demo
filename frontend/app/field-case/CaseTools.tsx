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
import { Together, type DiscussionData } from "./Together";
import { AGENT_HOST, guardInvestigation, kindOf, KIND_LABEL, type Kind } from "../../lib/claim-scope";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
type Call = (path: string, init?: RequestInit) => Promise<Any>;
export type { Discussed, Feedback };
export type Talk = { thread: Discussed[]; feedback: Record<string, Feedback> };
export const EMPTY_TALK: Talk = { thread: [], feedback: {} };

// ---------------------------------------------------------------------------------------------------------------
// The case's material, loaded once: the original lines (a log case) and the latest investigation.

export type Material = { lines: string[]; log: X.AgentLog | null; inv: Any | null; investigation: Any | null; loaded: boolean; guard: ((inv: Any) => Any) | null };
const NO_MATERIAL: Material = { lines: [], log: null, inv: null, investigation: null, loaded: false, guard: null };

export function useMaterial(caseId: string | null, call: Call, logCase: boolean): Material {
  const [m, setM] = useState<Material>(NO_MATERIAL);
  useEffect(() => {
    setM(NO_MATERIAL);
    if (!caseId || !logCase) { setM((x) => ({ ...x, loaded: true })); return; }
    let live = true;
    Promise.all([call(`/engine/cases/${caseId}/log`).catch(() => ({ lines: [] })), call(`/engine/cases/${caseId}/investigation`).catch(() => ({}))]).then(([l, d]) => {
      if (!live) return;
      const lines: string[] = l.lines || [];
      const pb = d.playback?.investigations;
      const log = X.looksLikeAgentLog(lines) ? X.parseAgentLog(lines) : null;
      // An agent-side log can only show what the agent received and sent: every statement is held to that scope.
      const guard = log ? (inv: Any) => guardInvestigation(inv, AGENT_HOST) : null;
      const raw = pb?.length ? pb[pb.length - 1] : d.investigation || null;
      const investigation = guard && raw ? guard(raw) : raw;
      setM({ lines, log, inv: investigation?.result || null, investigation, loaded: true, guard });
    });
    return () => { live = false; };
  }, [caseId, logCase, call]);
  return m;
}

/** Hypotheses with the engineer's feedback applied: what the engineer checked overrides what the data alone suggested. */
export function withFeedback(hyps: Any[] | undefined, fb: Record<string, Feedback>): Any[] {
  const confirmed = (hyps || []).some((h: Any) => fb[h.id]?.verdict === "confirmed");
  return (hyps || []).map((h: Any) => {
    const f = fb[h.id];
    if ((!f || f.verdict === "unsure") && confirmed && h.status === "leading") return { ...h, status: "open", because: "Was leading; your check points to another area", lines: [] };
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

/** The log case's data, drawn: what the agent received and sent on one time axis, the first departure from a healthy
 *  session in one line, and the decoded protocol detail folded into an engineering view. */
export function DataPanel({ m, onAsk }: { m: Material; onAsk?: (q: string) => void }) {
  if (!m.log || !m.log.packets.length) return null;
  const log = m.log;
  const ex = X.expectations(log);
  const fails = ex.filter((e) => e.ok === false);
  const zero = log.packets.find((p) => p.allZero && p.dir === "to_agent");
  const s = log.sessions[0];
  const rep = X.repeats(log).find((g) => !g.packets[0].allZero);
  const span = (log.packets[log.packets.length - 1].t - log.packets[0].t) * 1000;
  const bytes = rep ? X.answer(log, "what is the 13 byte packet")[0] : null;
  return (
    <div className={t.panel}>
      <div className={t.panelHead}>
        <h2 className={t.h2}>What the agent&apos;s log shows</h2>
        <span className={t.dim}>{log.packets.length} packets in {span.toFixed(0)} ms · recorded on the host</span>
      </div>
      <figure className={t.figure}>
        <Svg svg={timelineSvg(log, zero ? [zero.n] : [])} label="Every packet the agent received and sent, over time" />
        <figcaption><Tag k="observed" />{s && s.closed != null ? `The agent closed the session ${((s.closed - (s.opened ?? s.closed)) * 1000).toFixed(0)} ms after opening it, right after receiving` : "The agent received"}
          {zero ? ` a ${zero.len}-byte all-zero packet (outlined, line ${zero.line}).` : "."}{rep ? ` It then received the same ${rep.len}-byte packet ${rep.count} times, which is not a valid message.` : ""}</figcaption>
      </figure>
      {fails.length > 0 && <p className={t.first}><span className={t.badPill}>{fails.length} of {ex.length} healthy-session checks fail</span> First: {fails[0].observed.replace(/^No\. /, "")}</p>}
      <details className={t.eng}>
        <summary><b>Engineering view</b> <span className={t.dim}>decoded packets, protocol checks{bytes ? ", the repeating bytes" : ""}</span></summary>
        <div className={t.engBody}>
          <h3 className={t.h3}>A healthy session, against this log</h3>
          <p className={t.note}>Left: how a micro-ROS (DDS-XRCE) session behaves when it works (protocol knowledge). Right: what the agent&apos;s log shows.</p>
          <DataTable table={{ cols: ["A healthy session", "This log"], rows: ex.map((e) => [e.expected, `${e.ok === false ? "✕" : e.ok ? "✓" : "•"} ${e.observed}`]) }} />
          <h3 className={t.h3}>Every packet, decoded ({log.packets.length})</h3>
          <p className={t.note}>&quot;Received&quot; and &quot;sent&quot; are from the agent&apos;s side. What the board transmitted is not in this log.</p>
          <DataTable table={X.packetTable(log)} mono={[1, 2, 5]} />
          {bytes?.table && <><h3 className={t.h3}>The repeating {rep!.len}-byte packet</h3><DataTable table={bytes.table} mono={[1, 2]} />
            <Points points={bytes.points} /></>}
        </div>
      </details>
      {onAsk && <p className={t.invite}>Disagree, or suspect something else? <button className={t.link} onClick={() => onAsk("")}>Debug it with Veyra</button>. It checks what you suggest against these bytes.</p>}
    </div>
  );
}

const TAG_CLASS: Record<string, string> = { observed: "kObs", inferred: "kInf", reported: "kRep", hypothesis: "kHyp", lead: "kHyp", settle: "kSet", view: "kSet" };
const TAG_LABEL: Record<string, string> = { ...KIND_LABEL, lead: "Lead, not established", settle: "Would settle it", view: "Veyra's current view" };
export function Tag({ k }: { k: string }) {
  return <span className={`${t.tag} ${t[TAG_CLASS[k] || "kInf"]}`}>{TAG_LABEL[k] || k}</span>;
}
type AnyPoint = string | { k: string; t: string };
const asPoint = (p: AnyPoint) => (typeof p === "string" ? { k: kindOf(p) as string, t: p } : p);
export function Points({ points }: { points: AnyPoint[] }) {
  if (!points.length) return null;
  return <ul className={t.points}>{points.map((p, i) => { const q = asPoint(p); return <li key={i}><Tag k={q.k} /> {q.t}</li>; })}</ul>;
}
export type { Kind };

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
      {onAsk && <p className={t.invite}>Think a spike matters, or that a flagged axis is normal? <button className={t.link} onClick={() => onAsk("")}>Debug it with Veyra</button>.</p>}
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
// Where an answer came from. Fixed checks are deterministic code over the case's data; they never call a model.
const SOURCE: Record<string, string> = { check: "Fixed check · ran in your browser · no AI", ai: "AI investigator", feedback: "Your check · applied by fixed rules" };
const PROPOSING = /\b(i think|i suspect|maybe|might be|could it be|could be|probably|my guess|i bet|isn'?t it|it'?s the|it is the|blame)\b/i;

function proposals(m: Material, run: Any, fb: Record<string, Feedback>): Proposal[] {
  if (m.inv?.hypotheses?.length) {
    return withFeedback(m.inv.hypotheses, {}).filter((h: Any) => h.status !== "ruled_out" || fb[h.id]).map((h: Any) => ({ id: h.id, area: h.area, claim: h.claim, status: h.status,
      ask: h.status === "leading" ? "Veyra's leading explanation. Does your evidence agree?" : "Still possible. Have you checked it?" }));
  }
  if (run) return K.unusual(run).slice(0, 5).map((o, i) => ({ id: `f${i}:${o.rule}`, area: o.text.split(/[:(]/)[0].slice(0, 60), claim: o.text, status: "open", ask: "Veyra flags this. A real fault, or normal for this machine?" }));
  return [];
}

export function Discuss({ open, onClose, caseId, call, m, run, labels, talk, setTalk, agent, readOnly, onJob, together }: {
  open: boolean; onClose: () => void; caseId: string; call: Call; m: Material; run: Any; labels: Record<string, string>; talk: Talk; setTalk: (f: (t: Talk) => Talk) => void;
  agent: Any; readOnly: boolean; onJob: (jobId: string) => void; together?: { data: DiscussionData; reload: () => Promise<void> };
}) {
  const hasTogether = !!together?.data && ((together.data.rounds || []).length > 0 || !!together.data.agent?.configured);
  const [mode, setMode] = useState<"together" | "checks">("together");
  // A log no fixed check was written for (a decoded CAN log, say) has only the open-ended discussion.
  const hasChecks = !!m.log || !!run;
  const tab = hasTogether ? (hasChecks ? mode : "together") : "checks";
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
        const view = rel.length ? `${rel.map((h: Any) => `${h.area} is ${String(h.status).replace("_", " ")}`).join("; ")} (from the investigation).` : "";
        return { at: now(), who: "veyra", engine: "check", source: SOURCE.check, title: proposing && a.claim ? `You suggested: ${a.claim.toLowerCase()}` : a.title, verdict: a.verdict, text: a.text,
          points: [...a.points, ...(view ? [{ k: "view", t: view }] : []), ...(a.settle ? [{ k: "settle", t: a.settle }] : [])], table: a.table, lines: a.lines } as Discussed;
      });
    } else if (run) {
      replies = K.answerRun(run, question, labels).map((a) => ({ at: now(), who: "veyra", engine: "check", source: SOURCE.check, title: a.title, verdict: a.verdict, text: a.text, points: a.points, table: a.table,
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
        setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", engine: "ai", source: `${SOURCE.ai}${agent?.model ? ` · ${agent.model}` : ""}`, verdict: r.premise?.status === "supported" ? "supports" : r.premise?.status === "not_supported" ? "against" : "cannot_tell",
          text: r.answer?.text || "No answer came back.", points: r.premise?.explanation ? [r.premise.explanation] : [] }] }));
      } catch (err: Any) {
        setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", engine: "ai", source: SOURCE.ai, text: `The AI investigator could not answer: ${err.message}` }] }));
      } finally { setBusy(false); }
      return;
    }
    setTalk((x) => ({ ...x, thread: [...x.thread, { at: now(), who: "veyra", engine: "check", source: SOURCE.check, verdict: "cannot_tell", title: "No fixed check covers that",
      text: logMode ? "The fixed checks only answer questions they were written for, and none matches this one. They can check these right now:" : "No fixed check matched a signal or finding in this recording. Try naming a joint, an axis or an output, or one of these:",
      points: canAI && !logMode ? [] : [{ k: "view", t: "Open questions like this one need the AI investigator, which is off on the public demo. Nothing was sent to a model." }] }] }));
  }

  function saveFeedback() {
    if (!pending) return;
    const p = props.find((x) => x.id === pending.id);
    if (!p) return;
    const f: Feedback = { id: p.id, area: p.area, verdict: pending.verdict, note: note.trim(), at: now() };
    const nextFb = { ...talk.feedback, [p.id]: f };
    const after = withFeedback(m.inv?.hypotheses, nextFb);
    const standing = after.filter((h: Any) => h.status !== "ruled_out");
    const lead = after.find((h: Any) => nextFb[h.id]?.verdict === "confirmed") || after.find((h: Any) => h.status === "leading");
    const points: (string | { k: string; t: string })[] = [];
    let verdict: string = "facts";
    if (logMode && m.log) {
      const onArea = X.CHECKS.map((c) => c.run(m.log!)).filter((a): a is X.Answer => !!a && !!a.about && a.about.test(p.area));
      const against = onArea.filter((a) => a.verdict === "against"), fits = onArea.filter((a) => a.verdict === "supports");
      if (pending.verdict === "confirmed" && against.length) { verdict = "against"; points.push(`Worth a second look: the log points the other way. ${against[0].text} ${(against[0].points.find((x) => x.k === "inferred") ?? against[0].points[0])?.t ?? ""}`); }
      else if (pending.verdict === "confirmed" && fits.length) { verdict = "supports"; points.push(`The log fits: ${fits[0].text}`); }
      else if (pending.verdict === "ruled_out" && against.length) { verdict = "supports"; points.push(`The log agrees: ${against[0].text} ${(against[0].points.find((x) => x.k === "inferred") ?? against[0].points[0])?.t ?? ""}`); }
      else if (pending.verdict === "ruled_out" && fits.length) { verdict = "cannot_tell"; points.push(`Recorded, but note the log fits it: ${fits[0].text} Your bench check outranks the log here.`); }
    }
    if (m.inv?.hypotheses?.length) points.push({ k: "view", t: standing.length ? `Still possible: ${standing.map((h: Any) => h.area).join(", ")}.` : "Nothing is left standing. The cause is outside what Veyra listed, so the next round has to widen the search." });
    if (lead && pending.verdict !== "unsure") points.push({ k: "view", t: `Leading now: ${lead.area}.` });
    if (!lead && standing.length && m.inv?.hypotheses?.length) points.push({ k: "view", t: "No explanation leads any more; the next check should split the ones still standing." });
    const text = pending.verdict === "unsure" ? `Noted as unsure. ${p.area} keeps its status.` : `Recorded as your finding: ${p.area} ${pending.verdict === "ruled_out" ? "ruled out" : "confirmed"}${f.note ? ` (${f.note})` : ""}. Where the fault can be has been updated.`;
    setTalk((x) => ({ feedback: nextFb, thread: [...x.thread,
      { at: now(), who: "engineer", text: `${p.area}: ${FLABEL[pending.verdict].toLowerCase()}${f.note ? `. ${f.note}` : ""}` },
      { at: now(), who: "veyra", engine: "feedback", source: SOURCE.feedback, verdict, title: "Your check, applied", text, points }] }));
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
            <h2 className={t.dTitle}>Debug with Veyra</h2>
            {tab === "together" ? <p className={t.dSub}>Bring a guess or a bench result. The AI investigator tests it against the original data, says which parts hold, and proposes the next check.</p> : <p className={t.dSub}>{logMode ? "Suggest a cause or ask a question. Fixed checks test it against the decoded log and cite the lines."
              : "Ask about a joint, an axis or an output. Fixed checks answer from this machine's healthy behaviour and the findings in this recording."}</p>}
            {tab === "checks" && <div className={t.modes}>
              <span className={t.modeOn}><i aria-hidden>●</i> Fixed checks: {logMode ? `${X.CHECKS.length} written for this kind of log` : "baselines and findings"}, run in your browser, no AI</span>
              <span className={canAI ? t.modeOn : t.modeOff}><i aria-hidden>{canAI ? "●" : "○"}</i> AI investigator: {canAI ? `on${agent?.model ? ` · ${agent.model}` : ""}${logMode ? " (for new investigation rounds)" : " (for other questions)"}` : "off on the public demo"}</span>
            </div>}
          </div>
          <button className={t.x} onClick={onClose} aria-label="Close the discussion">×</button>
        </header>
        {hasTogether && hasChecks && (
          <div className={t.tabs} role="tablist" aria-label="How to discuss">
            <button role="tab" aria-selected={tab === "together"} className={tab === "together" ? t.tabOn : t.tab} onClick={() => setMode("together")}>
              Investigate together<small>AI investigator · open-ended</small></button>
            <button role="tab" aria-selected={tab === "checks"} className={tab === "checks" ? t.tabOn : t.tab} onClick={() => setMode("checks")}>
              Quick checks<small>fixed · in your browser · no AI</small></button>
          </div>)}
        {tab === "together" && together?.data ? (
          <div className={t.dBody}><Together caseId={caseId} call={call} lines={m.lines} data={together.data} reload={together.reload} readOnly={readOnly} /></div>
        ) : <>

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
          {logMode && <details className={t.fixedList}><summary>What the fixed checks can answer ({X.CHECKS.length})</summary>
            <ul>{X.CHECKS.map((c) => <li key={c.id}>{c.title}</li>)}</ul>
            <p className={t.fine}>Each is deterministic code over the bytes in this log. Every statement it returns is labelled: observed in the log, inferred from it, or a lead.</p></details>}
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
          <p className={t.fine}>{logMode ? "Fixed checks only. Nothing you type here is sent to an AI model."
            : canAI ? "Fixed checks first; questions they don't cover go to the AI investigator." : "Fixed checks only. Nothing you type here is sent to an AI model."}</p>
        </footer>
        </>}
      </aside>
    </>
  );
}

function Message({ d, log, run, caseId, call }: { d: Discussed & { plots?: Any[]; engine?: string }; log: X.AgentLog | null; run: Any; caseId: string; call: Call }) {
  if (d.who === "engineer") return <li className={t.me}><span className={t.who}>You</span><p>{d.text}</p></li>;
  const quoted = log && d.lines?.length ? [...new Set(d.lines)].filter((n) => n > 0 && n <= log.lines.length).slice(0, 4) : [];
  const highlight = log && d.lines?.length ? log.packets.filter((p) => d.lines!.includes(p.line)).map((p) => p.n) : [];
  return (
    <li className={t.veyra}>
      <span className={`${t.src} ${d.engine === "ai" ? t.srcAI : d.engine === "feedback" ? t.srcYou : t.srcCheck}`}>{d.source || "Veyra"}</span>
      {d.verdict && <span className={`${t.verdict} ${t["v_" + d.verdict] || ""}`}>{VLABEL[d.verdict] || d.verdict}</span>}
      {d.title && <b className={t.mTitle}>{d.title}</b>}
      <p>{d.text}</p>
      <Points points={d.points || []} />
      {log && highlight.length > 0 && highlight.length < log.packets.length && <Svg svg={timelineSvg(log, highlight)} label="The packets this answer refers to, outlined" />}
      {d.table && <DataTable table={d.table} max={8} />}
      {quoted.length > 0 && <div className={t.quotes}>{quoted.map((n) => <div key={n} className={t.q}><code className={t.ln}>{n}</code><code className={t.raw}>{log!.lines[n - 1]}</code></div>)}</div>}
      {run && (d.plots || []).slice(0, 1).map((p: Any, i: number) => { const spec = plotFor(run, p.rule, p.text); return spec ? <Waveform key={i} caseId={caseId} spec={spec} load={call} compact /> : null; })}
    </li>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// The report.

export function useReport({ caseId, data, m, talk, rounds = [] }: { caseId: string | null; data: Any; m: Material; talk: Talk; rounds?: Any[] }) {
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
      dataSections.push({ title: "Every packet the agent received and sent", svg: timelineSvg(m.log, zero ? [zero.n] : []), caption: `Recorded by the agent on the host: received above the line, sent below. Bar height is bytes. Red: every byte zero. Amber: not a valid XRCE message. What the board transmitted is not observed in this log.`, table: X.packetTable(m.log) });
      const ex = X.expectations(m.log);
      normal.push({ title: "A healthy micro-ROS session, against this log", intro: "Left column: protocol knowledge. Right column: computed from this log.", table: { cols: ["A healthy session", "This log"], rows: ex.map((e) => [e.expected, `${e.ok === false ? "✕" : e.ok ? "✓" : "•"} ${e.observed}`]) } });
      const r = X.repeats(m.log)[0];
      if (r) { const a = X.answer(m.log, "what is the 13 byte packet")[0]; if (a?.table) dataSections.push({ title: `The repeating ${r.len}-byte packet`, caption: `${a.text} ${a.points.map((p) => `[${p.k}] ${p.t}`).join(" ")}`, table: a.table }); }
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
      data: dataSections, normal, feedback: Object.values(talk.feedback), log: m.lines, scope: m.investigation?.scope,
      discussion: [...rounds.flatMap((r: Any) => [
        { at: r.recorded_at, who: "engineer" as const, text: `${r.message_kind === "result" ? "Result: " : "Guess: "}${r.message}${r.recorded ? " (scripted for this demo)" : ""}` },
        ...(r.result ? [{ at: r.recorded_at, who: "veyra" as const, engine: "ai", source: `AI investigator · ${r.model || ""}${r.recorded ? " · recorded" : ""}`, title: "Tested against the data", text: r.result.reply,
          points: [...(r.result.parts || []).map((p: Any) => ({ k: p.kind, t: `[${p.verdict.replace("_", " ")}] ${p.claim}: ${p.why}${(p.evidence || []).length ? ` (lines ${p.evidence.map((e: Any) => e.line).join(", ")})` : ""}` })),
            ...(r.result.next_check ? [{ k: "settle", t: `Next check: ${r.result.next_check.action}` }] : [])] }] : []),
      ]), ...talk.thread],
    });
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = reportFileName(c.title || "", caseId);
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }, [caseId, data, m, talk, rounds]);
}
