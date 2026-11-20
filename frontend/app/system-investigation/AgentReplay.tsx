"use client";

// Replay of a recorded run of the Veyra agent deciding each step and calling Veyra's engine
// tools (the system-level method and the battery-physics method among them). Every note, tool call, result and timing is
// taken from the run record; the chart on the right follows the tool being called. Played from the record, not re-run.

import { useEffect, useMemo, useRef, useState } from "react";
import s from "./system.module.css";
import { Plot, type View } from "./Replay";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

type Tool = { id: string; tool: string; title: string; summary?: string; ms?: number; method?: string; view?: View };
type Turn = { n: number; ms: number; note?: string; tools: Tool[] };
type Ev = { kind: "think"; turn: number; ms: number; note?: string } | { kind: "tool"; turn: number; tool: Tool } | { kind: "report" };

const METHOD: Record<string, string> = { system: "System-level method", physics: "Battery-physics method", engine: "Veyra engine", report: "Report" };

// Which part of the chart a tool call is about. Falls back to the previous view.
function viewFor(t: Tool, prev: View): View {
  if (t.view) return t.view;
  const name = t.tool, sum = `${t.title} ${t.summary || ""}`.toLowerCase();
  if (/repeat|restart|re-?authoris|closure/.test(sum)) return { window: "repeat", thresholds: true, closures: true };
  if (name === "physical_system_audit") return { window: "fault", events: true, thresholds: true };
  if (/charge|soc|integrat|capacity|coulomb/.test(sum) || name === "physical_checks") return { window: "fault", soc: true, band: true, thresholds: true };
  if (/weak|lowest cell|highest cell|align|imbalance/.test(sum)) return { window: "fault", thresholds: true, emphHi: true };
  if (/threshold|fault level|protect|contactor/.test(sum)) return { window: "fault", events: true, thresholds: true };
  if (name === "physical_plausibility" || name === "physical_hypotheses") return { window: "fault", soc: true, thresholds: true };
  if (name === "submit_report") return { window: "fault", soc: true, band: true, thresholds: true };
  return prev;
}

const clock = (ms: number) => { const t = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
const SPEEDS = [{ k: "fast", label: "8×", f: 8 }, { k: "real", label: "Real time", f: 1 }];

export default function AgentReplay({ d }: { d: Any }) {
  const run = d.agent_run;
  const turns: Turn[] = run.turns;
  const events = useMemo(() => {
    const out: Ev[] = [];
    for (const t of turns) {
      out.push({ kind: "think", turn: t.n, ms: t.ms, note: t.note });
      for (const tool of t.tools) out.push({ kind: "tool", turn: t.n, tool });
    }
    out.push({ kind: "report" });
    return out;
  }, [turns]);
  // Real elapsed time at the end of each event (model time + tool time, as recorded).
  const endAt = useMemo(() => { let acc = 0; return events.map((e) => { acc += e.kind === "think" ? e.ms : e.kind === "tool" ? e.tool.ms || 0 : 0; return acc; }); }, [events]);
  const views = useMemo(() => { let v: View = { window: d.series.window || "fault" }; return events.map((e) => { if (e.kind === "tool") v = viewFor(e.tool, v); else if (e.kind === "report") v = { window: "fault", soc: true, band: true, thresholds: true }; return v; }); }, [events, d.series.window]);

  const [i, setI] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[0]);
  const [thinkFrac, setThinkFrac] = useState(1);
  const logRef = useRef<HTMLOListElement | null>(null);

  // Advance: a model step lasts its recorded time (divided by the speed); a tool result stays long enough to read.
  const fracRef = useRef(1);
  fracRef.current = thinkFrac;
  const thinking = i >= 0 && events[i]?.kind === "think" && thinkFrac < 1;
  useEffect(() => {
    if (!playing) return;
    const e = events[i];
    if (e && e.kind === "think" && fracRef.current < 1) {
      const dur = Math.max(900, e.ms / speed.f), t0 = performance.now(), f0 = fracRef.current;
      let raf = 0;
      const tick = () => { const f = Math.min(1, f0 + (performance.now() - t0) / dur); setThinkFrac(f); if (f < 1) raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }
    if (i >= events.length - 1) { setPlaying(false); return; }
    const dwell = i < 0 ? 200 : e.kind === "think" ? 500 : e.kind === "tool" ? (speed.f > 1 ? 1500 : Math.max(400, e.tool.ms || 0)) : 0;
    const id = window.setTimeout(() => { setThinkFrac(events[i + 1].kind === "think" ? 0 : 1); setI(i + 1); }, dwell);
    return () => window.clearTimeout(id);
  }, [playing, i, thinking, events, speed]);

  useEffect(() => {
    const list = logRef.current, el = list?.querySelector<HTMLElement>(`[data-i="${i}"]`);
    if (list && el) list.scrollTo({ top: Math.max(0, el.offsetTop - list.clientHeight + el.offsetHeight + 16), behavior: "smooth" });
  }, [i, thinking]);

  const cur = i >= 0 ? events[i] : null;
  const nowMs = i < 0 ? 0 : cur?.kind === "think" ? endAt[i] - (cur.ms * (1 - thinkFrac)) : endAt[i];
  const view: View = i < 0 ? { window: d.series.window || "fault" } : views[i];
  const toolsDone = events.slice(0, i + 1).filter((e) => e.kind === "tool").length;
  const callsDone = events.slice(0, i + 1).filter((e) => e.kind === "think").length;
  const done = i >= events.length - 1;
  const start = () => { if (done) { setI(-1); setThinkFrac(1); } setPlaying(true); };
  const jump = (k: number) => { setPlaying(false); setThinkFrac(1); setI(Math.max(-1, Math.min(events.length - 1, k))); };
  const rep = run.report || {};

  return (
    <section className={`${s.panel} ${s.replay}`}>
      <header className={s.replayHead}>
        <div>
          <h3>Watch the agent investigate</h3>
          <p className={s.limit}>
            Recorded run: the Veyra agent decides each step and calls Veyra&apos;s engine tools, including the system-level
            and battery-physics methods. Notes, tool calls, results and timings come from the run record ({run.date}); the replay plays
            them back without re-running the agent. Whole run, including reading the logs: {Math.round(run.run_seconds / 60)} minutes.
          </p>
        </div>
        <div className={s.controls}>
          <div className={s.speed2} role="group" aria-label="Playback speed">
            {SPEEDS.map((x) => <button key={x.k} className={x.k === speed.k ? s.speedOn : s.speedOff} onClick={() => setSpeed(x)}>{x.label}</button>)}
          </div>
          <button className={s.ctl} onClick={() => jump(i - 1)} disabled={i <= 0} aria-label="Previous step">←</button>
          {playing ? <button className={s.ctlMain} onClick={() => setPlaying(false)}>Pause</button>
            : <button className={s.ctlMain} onClick={start}>{i < 0 ? "▶ Run" : done ? "▶ Again" : "▶ Continue"}</button>}
          <button className={s.ctl} onClick={() => jump(i + 1)} disabled={done} aria-label="Next step">→</button>
        </div>
      </header>
      <div className={s.meters}>
        <span><b>{clock(nowMs)}</b> / {clock(run.seconds * 1000)} agent time</span>
        <span><b>{callsDone}</b> / {turns.length} agent steps</span>
        <span><b>{toolsDone}</b> / {turns.reduce((a, t) => a + t.tools.length, 0)} tool calls</span>
        <span className={s.legend}>{Object.entries(run.hypotheses || {}).map(([k, v]) => `${k} = ${v}`).join(" · ")}</span>
        <div className={s.track}><div style={{ width: `${Math.min(100, (nowMs / (run.seconds * 1000)) * 100)}%` }} /></div>
      </div>
      <div className={s.replayBody}>
        <ol className={s.log} ref={logRef}>
          {i < 0 && <li className={s.logIdle}>Press Run to replay the agent&apos;s investigation of this case.</li>}
          {events.slice(0, i + 1).map((e, k) => {
            if (e.kind === "think") {
              const live = k === i && thinkFrac < 1;
              return (
                <li key={k} data-i={k} className={s.logThink}>
                  <span className={s.who}>Veyra agent · step {e.turn}</span>
                  {live ? <span className={s.thinking}>thinking… {((e.ms * thinkFrac) / 1000).toFixed(1)} s<i style={{ width: `${thinkFrac * 100}%` }} /></span>
                    : <>{e.note ? <p className={s.agentNote}>{e.note}</p> : null}<span className={s.took}>{(e.ms / 1000).toFixed(1)} s</span></>}
                </li>);
            }
            if (e.kind === "tool") {
              const t = e.tool;
              return (
                <li key={k} data-i={k} className={`${s.logTool} ${k === i ? s.logNow : ""}`} onClick={() => jump(k)}>
                  <span className={`${s.method} ${s["m_" + (t.method || "engine")] || ""}`}>{METHOD[t.method || "engine"]}</span>
                  <code className={s.toolName}>{t.tool}</code>
                  <b>{t.title}</b>
                  {t.summary && <p>{t.summary}</p>}
                </li>);
            }
            return (
              <li key={k} data-i={k} className={s.logReport}>
                <span className={s.who}>Agent&apos;s report</span>
                <b>{rep.headline}</b>
                {rep.explanation && <p>{rep.explanation}</p>}
                {rep.next_action && <p><b>Next:</b> {rep.next_action}</p>}
                {(rep.safety || []).map((x: string, j: number) => <p key={j}><b>Safety:</b> {x}</p>)}
                {d.real_case && <div className={s.after}><span className={s.who}>Afterwards, from the manufacturer</span><p>{d.real_case.outcome.statement}</p></div>}
              </li>);
          })}
        </ol>
        <div className={s.stage}>
          <Plot d={d} view={view} />
          <p className={s.stageCaption}>{({ fault: "Fault window", repeat: "Restarts after the shutdown", reference: "Reference window, before the incident" } as Record<string, string>)[view.window] || view.window}: lowest cell (indigo), highest cell (grey){view.soc ? ", displayed SOC (green, right axis)" : ""}. The chart follows the tool the agent is calling.</p>
        </div>
      </div>
      {run.benchmark && <Benchmark b={run.benchmark} done={done} />}
    </section>
  );
}

function Benchmark({ b, done }: { b: Any; done: boolean }) {
  const rows: Any[] = b.rows || [];
  const max = Math.max(...rows.map((r) => r.seconds));
  const fmt = (x: number) => (x < 90 ? `${Math.round(x)} s` : x < 3 * 3600 ? `${Math.round(x / 60)} min` : `≈ ${Math.round(x / 86400)} days`);
  return (
    <div className={`${s.bench} ${done ? s.benchDone : ""}`}>
      <h4>Benchmark on this case</h4>
      <div className={s.benchRows}>
        {rows.map((r, k) => (
          <div key={r.label} className={s.benchRow}>
            <span className={s.benchWho}>{r.label}</span>
            <span className={s.benchBar}><i className={k === rows.length - 1 ? s.benchC : s.benchV} style={{ width: `max(6px, ${(r.seconds / max) * 100}%)` }} /></span>
            <span className={s.benchVal}>{r.display || fmt(r.seconds)}</span>
            <p>{r.detail}</p>
          </div>))}
      </div>
      <div className={s.benchPoints}>
        {(b.points || []).map((x: Any) => <div key={x.title}><b>{x.title}</b><p>{x.text}</p></div>)}
      </div>
      {b.note && <p className={s.benchNote}>{b.note}</p>}
    </div>
  );
}
