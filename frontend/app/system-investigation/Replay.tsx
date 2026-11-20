"use client";

// Step-by-step replay of the engine's run on this case. Every finding and number is the engine's own output (the same
// file the page below renders); the chart on the right changes with each step. It is a recording of a deterministic
// engine run played at reading speed, not a live run and not an AI model.

import { useEffect, useMemo, useRef, useState } from "react";
import s from "./system.module.css";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
type Pt = number[];

export type View = {
  window: string; soc?: boolean; thresholds?: boolean; events?: boolean; band?: boolean; closures?: boolean; emphHi?: boolean;
  note?: string; range?: number[];
};
type Beat = { stage: string; title: string; text: string; view: View; after?: boolean };

const STAGE_LABEL: Record<string, string> = {
  read_logs: "reading the logs", system_audit: "system-level audit", physical_checks: "physical checks", explanations: "weighing causes",
  next_check: "next check", after: "afterwards",
};
const short = (t?: string, n = 260) => {
  const x = (t || "").replace(/\s+/g, " ").trim();
  if (x.length <= n) return x;
  const cut = x.slice(0, n), end = cut.lastIndexOf(". ");
  return end > n * 0.5 ? cut.slice(0, end + 1) : `${cut.replace(/[ ,;:]+\S*$/, "")}…`;
};
const secs = (v: number) => (v < 0.1 ? "under 0.1 s" : `${v.toFixed(1)} s`);
const first = (t?: string) => short((t || "").split(/(?<=\.)\s/)[0], 220);

function beatsFor(d: Any): Beat[] {
  const sy = d.system, ph = d.physics, ex = d.explanations, nx = d.next, rc = d.real_case;
  const ca = (ph.checks || []).find((c: Any) => c.id === "charge_accounting");
  const hyps: Any[] = ex.hypotheses || [];
  const by = (st: string) => hyps.filter((h) => h.status === st).map((h) => h.label);
  const lead = ex.leading || ex.conditional_leading;
  const al = ph.alignment?.value || {};
  const nRestarts = (sy.restarts?.rows || []).length;
  const safety = (sy.signals?.items || []).find((i: Any) => i.flag === "safety");
  const B: Beat[] = [
    { stage: "read_logs", title: "Read the vehicle's own logs",
      text: rc ? `${rc.values}, from three time windows. No new sensors, no test drive.` : "The BMS and VCU logs of the incident, as recorded.",
      view: { window: "fault" } },
    { stage: "system_audit", title: "Check the reported symptom against the record", text: short(sy.symptom?.statement, 300),
      view: { window: "fault", note: "no drive request · vehicle stationary" } },
    { stage: "system_audit", title: "Rebuild the fault sequence across BMS, VCU, DC/DC and contactors",
      text: `${(sy.sequence?.events || []).length} state changes put in order around the shutdown. ${first((sy.sequence?.findings || [])[0]?.statement)}`,
      view: { window: "fault", events: true } },
    { stage: "system_audit", title: "Infer the protection thresholds from the timing",
      text: (sy.thresholds?.rows || []).map((r: Any) => `level ${r.level} ≈ ${r.inferred_threshold} V after ${r.delay_s} s`).join(", ") + ". Inferred, for the manufacturer's specification to confirm.",
      view: { window: "fault", events: true, thresholds: true } },
    { stage: "system_audit", title: "Check which signals are real measurements",
      text: `${(sy.signals?.items || []).length} signal finding${(sy.signals?.items || []).length === 1 ? "" : "s"}: copies, clamped or saturated readings are not counted as independent evidence.${safety ? " " + first(safety.statement) : ""}`,
      view: { window: "fault", events: true, thresholds: true } },
    { stage: "physical_checks", title: "Count the charge against the displayed SOC", text: short(ca?.summary, 300),
      view: { window: "fault", thresholds: true, soc: true, band: true } },
    { stage: "physical_checks", title: "Weigh the weakest cell against the whole pack",
      text: `The weakest cell runs ${al.offset_range_ah ? `${al.offset_range_ah[0]}–${al.offset_range_ah[1]} Ah` : ""} ahead of the strongest (about ${al.minutes_at_typical_load} min at this load): about ${Math.round((al.share_of_discrepancy || 0) * 100)}% of the shortfall. It sets the timing, not the size.`,
      view: { window: "fault", thresholds: true, emphHi: true } },
  ];
  if (nRestarts) B.push({ stage: "system_audit", title: `Follow every restart after the shutdown (${nRestarts})`,
    text: short((sy.restarts?.findings || []).map((f: Any) => f.statement).join(" "), 320), view: { window: "repeat", thresholds: true, closures: true } });
  B.push({ stage: "explanations", title: "Weigh the possible causes",
    text: `Open: ${by("open").join("; ")}. Not enough on its own: ${by("insufficient_alone").join("; ") || "none"}. Weakened: ${by("weakened").join("; ") || "none"}.${lead ? " " + first(lead.statement) : ""}`,
    view: { window: "fault", soc: true, thresholds: true } });
  B.push({ stage: "next_check", title: "Choose what to do next",
    text: `${(nx.safety_holds || []).map((h: Any) => `Safety first: ${h.title}.`).join(" ")} First check: ${(nx.recommended || [])[0]?.title || ""}, with no vehicle contact.`,
    view: { window: "fault", soc: true, thresholds: true } });
  if (rc) B.push({ stage: "after", after: true, title: "Afterwards: what the manufacturer confirmed", text: `${short(rc.outcome.statement, 600)} Fix: ${rc.outcome.fix}`,
    view: { window: "fault", soc: true, band: true } });
  return B;
}

export function Plot({ d, view }: { d: Any; view: View }) {
  const W = 640, H = 300, L = 40, R = 40, T = 16, Bm = 30;
  const lines = view.window === (d.series.window || "fault") ? d.series.lines : (d.series.by_window || {})[view.window] || {};
  // Cell readings outside 1.5–4.5 V (0 V while the BMS is not reporting, 65.535 V sentinel) are not voltages: left out.
  const ok = (p: Pt) => p[1] >= 1.5 && p[1] <= 4.5;
  const rawLo: Pt[] = lines.v_cell_min?.points || [], rawHi: Pt[] = lines.v_cell_max?.points || [], soc: Pt[] = lines.soc?.points || [];
  const lo = rawLo.filter(ok), hi = rawHi.filter(ok);
  const dropped = rawLo.length + rawHi.length - lo.length - hi.length;
  const all = [...lo, ...hi];
  if (!all.length) return null;
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
  const v0 = Math.min(2.3, ...all.map((p) => p[1])) , v1 = Math.max(3.3, ...all.map((p) => p[1]));
  const x = (t: number) => L + ((t - t0) / Math.max(1e-9, t1 - t0)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - v0) / (v1 - v0)) * (H - T - Bm);
  const ys = (p: number) => T + (1 - p / 100) * (H - T - Bm);
  // A gap of more than 20 s in the record (vehicle asleep, logger off) breaks the line instead of drawing across it.
  const path = (pts: Pt[], f: (v: number) => number) => pts.filter((p) => p[0] >= t0 && p[0] <= t1)
    .map((p, i, a) => `${i === 0 || p[0] - a[i - 1][0] > 20 ? "M" : "L"}${x(p[0]).toFixed(1)},${f(p[1]).toFixed(1)}`).join("");
  const rows: Any[] = d.system.thresholds.rows || [];
  const ca = (d.physics.checks || []).find((c: Any) => c.id === "charge_accounting");
  const m = /at (-?[\d.]+) s/.exec(((ca?.calculations || [])[0] || {}).name || "");
  const bandT = m ? Number(m[1]) : null;
  const open = d.series.open_t;
  const closures: Any[] = (d.system.restarts.rows || []).filter((r: Any) => r.window === view.window);
  const on = (b?: boolean) => ({ opacity: b ? 1 : 0, transition: "opacity 600ms ease" });
  const span = t1 - t0, step = span > 2400 ? 600 : span > 900 ? 300 : span > 300 ? 60 : 20;
  const ticks: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) ticks.push(t);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={s.plot} role="img" aria-label={`Cell voltages in the ${view.window} window`}>
      {[2.4, 2.6, 2.8, 3.0, 3.2].filter((v) => v <= v1).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={s.grid} /><text x={L - 6} y={y(v) + 4} className={s.axis} textAnchor="end">{v.toFixed(1)}</text></g>)}
      {ticks.map((t) => <text key={t} x={x(t)} y={H - 10} className={s.axis} textAnchor="middle">{t} s</text>)}
      <g style={on(view.band && bandT != null && bandT >= t0 && bandT <= t1)}>
        {bandT != null && <><rect x={x(bandT) - 6} y={T} width={12} height={H - T - Bm} className={s.band} /><text x={x(bandT) - 10} y={T + 12} className={s.bandLabel} textAnchor="end">SOC corrected, charge not explained</text></>}
      </g>
      {view.range && (() => { const a = Math.max(t0, view.range[0]), b = Math.min(t1, view.range[1]); return b > a ? (
        <g><rect x={x(a)} y={T} width={x(b) - x(a)} height={H - T - Bm} className={s.range} /><text x={x(a) + 4} y={H - Bm - 6} className={s.rangeLabel}>computed over this span</text></g>) : null; })()}
      <g style={on(view.thresholds)}>
        {rows.map((r: Any) => r.inferred_threshold != null && <g key={r.level}><line x1={L} x2={W - R} y1={y(r.inferred_threshold)} y2={y(r.inferred_threshold)} className={s.threshold} /><text x={W - R - 2} y={y(r.inferred_threshold) - 4} className={s.thresholdLabel} textAnchor="end">L{r.level} ≈ {r.inferred_threshold} V</text></g>)}
      </g>
      <g style={on(view.events && view.window === (d.series.window || "fault"))}>
        {rows.map((r: Any) => r.t >= t0 && r.t <= t1 && <g key={`e${r.level}`}><line x1={x(r.t)} x2={x(r.t)} y1={T} y2={H - Bm} className={s.eventLine} /><text x={x(r.t) + 3} y={H - Bm - 4} className={s.eventLabel}>L{r.level}</text></g>)}
        {open != null && open >= t0 && open <= t1 && <g><line x1={x(open)} x2={x(open)} y1={T} y2={H - Bm} className={s.open} /><text x={x(open) + 4} y={T + 12} className={s.openLabel}>contactors open</text></g>}
      </g>
      <g style={on(view.closures)}>
        {closures.map((r: Any) => <g key={r.n}><line x1={x(r.t)} x2={x(r.t)} y1={T} y2={H - Bm} className={s.closure} /><text x={x(r.t) > W - R - 50 ? x(r.t) - 3 : x(r.t) + 3} y={T + 12} className={s.closureLabel} textAnchor={x(r.t) > W - R - 50 ? "end" : "start"}>HV on #{r.n}</text></g>)}
      </g>
      <g style={on(view.soc)}>
        {soc.length > 0 && <path d={path(soc, ys)} className={s.soc} />}
        {[0, 50, 100].map((p) => <text key={p} x={W - R + 6} y={ys(p) + 4} className={s.axisSoc}>{p}%</text>)}
      </g>
      <path d={path(hi, y)} className={view.emphHi ? s.hiStrong : s.hi} />
      <path d={path(lo, y)} className={s.lo} />
      {view.note && <text x={L + 8} y={T + 14} className={s.plotNote}>{view.note}</text>}
      {dropped > 0 && <text x={W - R} y={H - Bm - 6} className={s.axis} textAnchor="end">{dropped} invalid cell readings (0 V, 65.535 V) not drawn</text>}
    </svg>
  );
}

export default function Replay({ d }: { d: Any }) {
  const beats = useMemo(() => beatsFor(d), [d]);
  const [i, setI] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const listRef = useRef<HTMLOListElement | null>(null);
  const stages: Record<string, number> = d.real_case?.timing?.stages_s || {};
  const firstOf = useMemo(() => { const seen = new Set<string>(); return beats.map((b) => { const f = !seen.has(b.stage); seen.add(b.stage); return f; }); }, [beats]);

  useEffect(() => { if (!playing) return; const id = window.setTimeout(() => { if (i >= beats.length - 1) setPlaying(false); else setI(i + 1); }, i < 0 ? 300 : 4200); return () => window.clearTimeout(id); }, [playing, i, beats.length]);
  // Keep the current step in view inside the list only; never move the page under the reader.
  useEffect(() => {
    const list = listRef.current, el = list?.querySelector<HTMLElement>(`[data-i="${i}"]`);
    if (!list || !el) return;
    // The step just reached goes to the top of the list, with the tail of the previous one above it.
    list.scrollTo({ top: Math.max(0, el.offsetTop - 56), behavior: "smooth" });
  }, [i]);

  const cur = beats[Math.max(0, i)];
  const start = () => { if (i >= beats.length - 1) setI(-1); setPlaying(true); };
  return (
    <section className={`${s.panel} ${s.replay}`}>
      <header className={s.replayHead}>
        <div>
          <h3>Watch the investigation</h3>
          <p className={s.limit}>Replay of the engine&apos;s recorded run on this data{d.real_case ? ` (${d.real_case.timing.total_s} s)` : ""}, played at reading speed. Every line is the engine&apos;s own output; no AI model is called in these steps.</p>
        </div>
        <div className={s.controls}>
          <button className={s.ctl} onClick={() => { setPlaying(false); setI(Math.max(0, i - 1)); }} disabled={i <= 0} aria-label="Previous step">←</button>
          {playing ? <button className={s.ctlMain} onClick={() => setPlaying(false)}>Pause</button>
            : <button className={s.ctlMain} onClick={start}>{i < 0 ? "▶ Start" : i >= beats.length - 1 ? "▶ Again" : "▶ Continue"}</button>}
          <button className={s.ctl} onClick={() => { setPlaying(false); setI(Math.min(beats.length - 1, i + 1)); }} disabled={i >= beats.length - 1} aria-label="Next step">→</button>
        </div>
      </header>
      <div className={s.replayBody}>
        <ol className={s.steps} ref={listRef}>
          {beats.map((b, k) => (
            <li key={k} data-i={k} className={`${k > i ? s.stepHidden : ""} ${k === i ? s.stepNow : ""}`} onClick={() => { setPlaying(false); setI(k); }}>
              <span className={s.stepMeta}>{b.after ? "afterwards, from the manufacturer" : `${STAGE_LABEL[b.stage] || b.stage}${firstOf[k] && stages[b.stage] != null ? ` · whole stage ${secs(stages[b.stage])}` : ""}`}</span>
              <b>{b.title}</b>
              {k <= i && <p>{b.text}</p>}
            </li>))}
        </ol>
        <div className={s.stage}>
          <Plot d={d} view={i < 0 ? { window: d.series.window || "fault" } : cur.view} />
          <p className={s.stageCaption}>{i < 0 ? "Press Start to replay the investigation. The chart shows the lowest and highest cell voltage." : `${cur.view.window === (d.series.window || "fault") ? "Fault window" : "Restarts after the shutdown"}: lowest cell (indigo), highest cell (grey)${cur.view.soc ? ", displayed SOC (green, right axis)" : ""}.`}</p>
        </div>
      </div>
    </section>
  );
}
