"use client";

// The product view of a case: the Veyra agent investigating (played back from its recorded run), the signals it is looking
// at, the findings as they appear, the result, and what the customer keeps afterwards. Details live in the report tab.

import { useEffect, useMemo, useRef, useState } from "react";
import w from "./workspace.module.css";
import { Plot, type View } from "./Replay";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
type Tool = { id: string; tool: string; title: string; summary?: string; ms?: number; method?: string; view?: View };
type Ev = { kind: "think"; turn: number; ms: number; note?: string } | { kind: "tool"; turn: number; tool: Tool } | { kind: "report" };

// What each engine tool does, in plain words.
const LABEL: Record<string, string> = {
  overview: "Read the evidence", case_context: "Read the case context", physical_checks: "Checked the battery physics",
  physical_hypotheses: "Listed possible causes", physical_earlier_knowledge: "Looked for earlier verified cases",
  inquiry_plan: "Planned what to separate", inquiry_signals: "Listed usable signals", inquiry_dynamics: "Screened signal behaviour",
  physical_system_audit: "Audited the vehicle as a system", physical_plausibility: "Weighed what each cause would require",
  physical_next_checks: "Chose the decisive next check", lessons: "Read lessons from past investigations", inquiry_ledger: "Reviewed tests so far",
  inquiry_compute: "Ran a calculation", inquiry_predict: "Wrote down a prediction to test", inquiry_test: "Tested its prediction",
  inquiry_distinguishable: "Marked what the data cannot separate", search: "Searched the event log", submit_report: "Wrote the report",
};
const METHOD: Record<string, string> = { system: "System-level method", physics: "Battery physics" };

function viewFor(t: Tool, prev: View): View {
  if (t.view) return t.view;
  if (t.tool === "physical_checks") return { window: "fault", soc: true, band: true, thresholds: true };
  if (t.tool === "submit_report") return { window: "fault", soc: true, band: true, thresholds: true };
  return prev;
}
const clock = (ms: number) => { const t = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
const SPEEDS = [{ k: "fast", label: "8×", f: 8 }, { k: "real", label: "Real time", f: 1 }];

export default function Workspace({ d }: { d: Any }) {
  const run = d.agent_run, rc = d.real_case, bm = run.benchmark || {};
  const events = useMemo(() => {
    const out: Ev[] = [];
    for (const t of run.turns) { out.push({ kind: "think", turn: t.n, ms: t.ms, note: t.note }); for (const tool of t.tools) out.push({ kind: "tool", turn: t.n, tool }); }
    out.push({ kind: "report" });
    return out;
  }, [run]);
  const endAt = useMemo(() => { let acc = 0; return events.map((e) => { acc += e.kind === "think" ? e.ms : e.kind === "tool" ? e.tool.ms || 0 : 0; return acc; }); }, [events]);
  const views = useMemo(() => { let v: View = { window: "fault" }; return events.map((e) => { if (e.kind === "tool") v = viewFor(e.tool, v); else if (e.kind === "report") v = { window: "fault", soc: true, band: true, thresholds: true }; return v; }); }, [events]);
  const idxOf = useMemo(() => { const m: Record<string, number> = {}; events.forEach((e, k) => { if (e.kind === "tool") m[e.tool.id] = k; }); return m; }, [events]);

  const [i, setI] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[0]);
  const [frac, setFrac] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const fracRef = useRef(1); fracRef.current = frac;
  const feedRef = useRef<HTMLOListElement | null>(null);
  const done = i >= events.length - 1;
  const thinking = i >= 0 && events[i]?.kind === "think" && frac < 1;

  // Start by itself, as a live product would, unless the visitor prefers reduced motion.
  useEffect(() => {
    let reduce = false;
    try { reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { /* ignore */ }
    if (!reduce) { const id = window.setTimeout(() => setPlaying(true), 900); return () => window.clearTimeout(id); }
  }, []);

  useEffect(() => {
    if (!playing) return;
    const e = events[i];
    if (e && e.kind === "think" && fracRef.current < 1) {
      const dur = Math.max(900, e.ms / speed.f), t0 = performance.now(), f0 = fracRef.current;
      let raf = 0;
      const tick = () => { const f = Math.min(1, f0 + (performance.now() - t0) / dur); setFrac(f); if (f < 1) raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }
    if (i >= events.length - 1) { setPlaying(false); return; }
    const dwell = i < 0 ? 200 : e.kind === "think" ? 450 : e.kind === "tool" ? (speed.f > 1 ? 1100 : Math.max(400, e.tool.ms || 0)) : 0;
    const id = window.setTimeout(() => { setFrac(events[i + 1].kind === "think" ? 0 : 1); setI(i + 1); }, dwell);
    return () => window.clearTimeout(id);
  }, [playing, i, thinking, events, speed]);

  useEffect(() => {
    const list = feedRef.current, el = list?.querySelector<HTMLElement>(`[data-i="${i}"]`);
    if (list && el) list.scrollTo({ top: Math.max(0, el.offsetTop - list.clientHeight + el.offsetHeight + 24), behavior: "smooth" });
  }, [i, thinking]);

  const cur = i >= 0 ? events[i] : null;
  const nowMs = i < 0 ? 0 : cur?.kind === "think" ? endAt[i] - cur.ms * (1 - frac) : endAt[i];
  const view: View = i < 0 ? { window: "fault" } : views[i];
  const isEng = (e: Ev) => e.kind === "tool" && e.tool.tool !== "submit_report";
  const toolsDone = events.slice(0, i + 1).filter(isEng).length, toolsAll = events.filter(isEng).length;
  const stepsDone = events.slice(0, i + 1).filter((e) => e.kind === "think").length;
  const shown = (run.findings || []).filter((f: Any) => (idxOf[f.after] ?? 1e9) <= i);
  const status = i < 0 ? "Ready" : done ? "Investigation complete" : "Investigating";
  const restart = () => { setOpen(null); setFrac(1); setI(-1); setPlaying(true); };
  const skip = () => { setPlaying(false); setFrac(1); setI(events.length - 1); };
  const rep = run.report || {};
  const rows: Any[] = bm.rows || [];

  return (
    <div className={w.app}>
      <header className={w.bar}>
        <a href="#live" className={w.brand}><span className={w.mark}>V</span>Veyra</a>
        <div className={w.caseSel}><span className={w.caseDot} />High-voltage loss · autonomous vehicle<span className={w.caseMeta}>real field case, anonymised</span></div>
        <nav className={w.tabs} aria-label="View">
          <a href="#live" aria-current="page">Investigation</a>
          <a href="#report">Engineering report</a>
          <a href="#synthetic">Synthetic example</a>
        </nav>
        <span className={w.grow} />
        <span className={`${w.status} ${done ? w.statusDone : i >= 0 ? w.statusLive : ""}`}><i />{status}</span>
      </header>

      <div className={w.claim}>
        <b>{Math.round(rc.timing.total_s)} seconds</b> to surface the decisive SOC inconsistency. <b>{Math.round(run.run_seconds / 60)} minutes</b> for the full AI investigation, with {toolsAll} engineering tool calls.
      </div>

      {/* One slim strip for the case and the run, so the agent's work keeps the screen. */}
      <div className={w.strip}>
        <div className={w.stripCase}>
          <span className={w.reported}><span>Reported</span> “{d.case.reported}”</span>
          <span className={w.chips}>
            <span>BMS + VCU logs · {(rc?.values || "").split(" ")[0]} values</span>
            <span>No vehicle access</span><span>No supplier data</span><span>No manufacturer rules</span>
          </span>
        </div>
        <div className={w.stripRun}>
          <span className={w.meters}>
            <span><b>{clock(nowMs)}</b> agent time</span>
            <span><b>{stepsDone}/{run.turns.length}</b> steps</span>
            <span><b>{toolsDone}/{toolsAll}</b> tool calls</span>
          </span>
          {done ? <button className={w.primary} onClick={restart}>Run again</button>
            : playing ? <button className={w.primary} onClick={() => setPlaying(false)}>Pause</button>
              : <button className={w.primary} onClick={() => setPlaying(true)}>{i < 0 ? "Start" : "Resume"}</button>}
          {!done && <button className={w.ghost} onClick={skip}>Skip to result</button>}
          <span className={w.speed} role="group" aria-label="Playback speed">
            {SPEEDS.map((x) => <button key={x.k} className={x.k === speed.k ? w.on : ""} onClick={() => setSpeed(x)}>{x.label}</button>)}
          </span>
        </div>
        <div className={w.stripTrack} title="Played back from the Veyra agent's recorded run on this case"><div style={{ width: `${Math.min(100, (Math.max(0, i + 1) / events.length) * 100)}%` }} /></div>
      </div>

      <div className={w.grid}>

        <section className={`${w.card} ${w.feedCard}`}>
          <h2>Agent activity</h2>
          <ol className={w.feed} ref={feedRef}>
            {i < 0 && <li className={w.idle}>The agent is about to open the case.</li>}
            {events.slice(0, i + 1).map((e, k) => {
              if (e.kind === "think") {
                const live = k === i && frac < 1;
                return (
                  <li key={k} data-i={k} className={w.think}>
                    <span className={w.avatar}>V</span>
                    <div>
                      <span className={w.who}>Veyra agent</span>
                      {live ? <span className={w.dots}>thinking<i /><i /><i /></span> : e.note && <p>{e.note}</p>}
                    </div>
                  </li>);
              }
              if (e.kind === "tool") {
                const t = e.tool, isOpen = open === t.id;
                return (
                  <li key={k} data-i={k} className={`${w.tool} ${k === i ? w.toolNow : ""}`}>
                    <button onClick={() => setOpen(isOpen ? null : t.id)} aria-expanded={isOpen}>
                      <span className={w.tick} aria-hidden>{k === i && playing ? "" : "✓"}</span>
                      <span className={w.toolLabel}>{t.tool === "submit_report" && /returned/.test(t.title) ? "Report sent back by Veyra's checks for correction" : LABEL[t.tool] || t.title}</span>
                      {t.method && METHOD[t.method] && <span className={`${w.chip} ${t.method === "system" ? w.chipSys : w.chipPhys}`}>{METHOD[t.method]}</span>}
                    </button>
                    {isOpen && <div className={w.detail}><b>{t.title}</b>{t.summary && <p>{t.summary}</p>}</div>}
                  </li>);
              }
              return (
                <li key={k} data-i={k} className={w.think}>
                  <span className={w.avatar}>V</span>
                  <div><span className={w.who}>Veyra agent</span><p>Report written. The result is ready.</p></div>
                </li>);
            })}
          </ol>
        </section>

        <div className={w.right}>
          <section className={w.card}>
            <h2>Signals <span className={w.sub}>{view.window === "repeat" ? "restarts after the shutdown" : view.window === "reference" ? "before the incident" : "around the shutdown"}</span></h2>
            <Plot d={d} view={view} />
            <p className={w.legend}><i className={w.lLo} />lowest cell <i className={w.lHi} />highest cell {view.soc && <><i className={w.lSoc} />displayed charge</>}</p>
          </section>
          <section className={w.card}>
            <h2>Findings <span className={w.sub}>{shown.length} so far</span></h2>
            {shown.length === 0 && <p className={w.fine}>Findings appear here as the agent confirms them.</p>}
            <ul className={w.findings}>
              {shown.map((f: Any) => (
                <li key={f.title} className={w["f_" + f.kind] || ""}>
                  <b>{f.title}</b>{f.badge && <span className={w.badge}>{f.badge}</span>}
                  <p>{f.text}</p>
                </li>))}
            </ul>
          </section>
        </div>
      </div>

      {done && (
        <section className={w.result} aria-live="polite">
          <header>
            <h2>Investigation complete</h2>
            <span className={w.fine}>Full AI investigation in {Math.round(run.run_seconds / 60)} minutes, from the raw logs, without the vehicle or supplier data.</span>
          </header>
          <div className={w.three}>
            <div><h3>What happened</h3><p>The battery protection cut high voltage on a parked vehicle. The displayed charge was wrong: the pack held far less than it showed.</p></div>
            <div><h3>Still possible</h3><p>The SOC estimate drifted, usable capacity is low, or the current sensor under-reads. The logs alone cannot separate them.</p></div>
            <div><h3>Verify next</h3><p>{(rep.next_action || "").split(". ")[0]}. Read-only, no vehicle contact. Hold high voltage until every cell has been checked.</p></div>
          </div>
          <div className={w.bench}>
            <div><b>{Math.round(rc.timing.total_s)} s</b><span>to surface the decisive SOC inconsistency</span></div>
            <div><b>{Math.round(run.run_seconds / 60)} min</b><span>full AI investigation</span></div>
            <div className={w.benchThem}><b>≈ 2 days</b><span>{rows[rows.length - 1]?.label || "the manufacturer"}, with its battery supplier</span></div>
          </div>
          {rc?.outcome && <p className={w.confirmed}><b>Confirmed afterwards by the manufacturer:</b> SOC drift after a long time without a full charge. The check Veyra put first is where it was found.</p>}
        </section>
      )}

      <section className={w.after}>
        <p className={w.kicker}>From one investigation</p>
        <h2>A failure solved. Engineering knowledge retained.</h2>
        <p className={w.afterLede}>What the customer gets after the investigation.</p>
        <div className={w.three}>
          <div><h3>Evidence-backed investigation</h3><p>What happened, which explanations can still hold, and what to verify next.</p></div>
          <div><h3>Verified engineering knowledge</h3><p>The mechanism your engineers confirm afterwards, and where it applies, kept as a private knowledge asset.</p></div>
          <div><h3>Reusable checks</h3><p>The relevant engineering checks run again on new records of the same kind. How widely they apply, and how often they raise a false alarm, still has to be validated.</p></div>
        </div>
        <p className={w.closing}>Every failure makes your engineering system more capable.</p>
        <p className={w.closingSub}>Hand a field failure to Veyra, and your team does not have to walk the whole investigation again. Every past failure leaves a capability you can reuse.</p>
      </section>
    </div>
  );
}
