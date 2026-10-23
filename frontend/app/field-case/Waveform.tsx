"use client";
// Evidence plots: the recorded samples behind a finding, drawn from the case's own data (GET .../series).
// A finding says what a rule saw; the plot lets an engineer see it too: the held value against its normal range,
// or the command against what the machine did. No smoothing: the server keeps each bucket's first/low/high sample.
import { useEffect, useMemo, useState } from "react";
import s from "./field-case.module.css";

type Any = any; // engine records are versioned JSON
type Load = (path: string) => Promise<Any>;
export type Trace = { signal: string; scope?: string | null; label: string; role: "measured" | "command" };
export type PlotSpec = {
  key: string; title: string; traces: Trace[]; from: number; to: number; zero: number;
  region?: { from: number; to: number; label: string }; band?: { lo: number; hi: number; label: string }; marker?: { at: number; label: string };
};

// Which recorded signals form a commanded/measured pair (mirrors the engine's tracking pairs).
const PAIRS: Record<string, [string, string, boolean]> = {
  attitude: ["control.attitude_setpoint", "vehicle.attitude", true], joint_position: ["joint.position_command", "joint.position", true],
  body_velocity: ["control.velocity_setpoint", "vehicle.velocity", false], turn_rate: ["control.yaw_rate_setpoint", "vehicle.yaw_rate", false],
};
const epoch = (iso?: string) => (iso ? new Date(iso).getTime() / 1000 : NaN);

/** The plot for one observation of the run, or null when the finding has no recorded samples to show. */
export function plotFor(run: Any, rule: string, text: string): PlotSpec | null {
  if (rule === "R-OUTPUT-STUCK") {
    const f = (run.findings?.actuator_outputs?.findings || []).find((x: Any) => x.rule === "STUCK" && text.startsWith(`${x.channel} output held`));
    if (!f) return null;
    const end = epoch(f.end), held = end - f.t_start;
    return { key: `out-${f.channel}`, title: `${f.channel} output`, zero: f.t_start, from: f.t_start - 20, to: end + 10,
      traces: [{ signal: "actuator.output", scope: f.channel, label: `${f.channel} output`, role: "measured" }],
      region: { from: f.t_start, to: end, label: `held at ${f.held_value}${f.unit === "us" ? " µs" : f.unit ? ` ${f.unit}` : ""} for ${held.toFixed(1)} s` },
      band: f.reference_range ? { lo: f.reference_range[0], hi: f.reference_range[1], label: "normal range" } : undefined };
  }
  if (rule === "R-TRACKING") {
    const f = (run.findings?.tracking?.findings || []).find((x: Any) => text.startsWith(`${x.axis} stopped following`));
    // Runs recorded before the pair id was stored: roll/pitch/yaw are attitude; otherwise match the measured signal.
    const pairId = f && (f.pair || (["roll", "pitch", "yaw"].includes(f.axis) ? "attitude" : Object.keys(PAIRS).find((k) => PAIRS[k][1] === f.measured)));
    const pair = pairId && PAIRS[pairId];
    if (!f || !pair) return null;
    const scope = pair[2] ? f.axis : null;
    return { key: `trk-${f.axis}`, title: `${f.axis}: command vs measured`, zero: f.t_start, from: f.t_start - 20, to: f.t_start + 25,
      traces: [{ signal: pair[0], scope, label: "commanded", role: "command" }, { signal: pair[1], scope, label: "measured", role: "measured" }],
      marker: { at: f.t_start, label: "stopped following" } };
  }
  return null;
}

const W = 640, H = 190, L = 52, R = 12, T = 22, B = 26;

function ticks(lo: number, hi: number, n = 3) {
  const step = (hi - lo) / (n - 1 || 1);
  return Array.from({ length: n }, (_, i) => lo + i * step);
}
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));

export function Waveform({ caseId, spec, load, compact = false }: { caseId: string; spec: PlotSpec; load: Load; compact?: boolean }) {
  const [series, setSeries] = useState<Any[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setSeries(null); setError("");
    Promise.all(spec.traces.map((t) => load(`/engine/cases/${caseId}/series?signal=${encodeURIComponent(t.signal)}${t.scope ? `&scope=${encodeURIComponent(t.scope)}` : ""}`
      + `&start=${spec.from}&end=${spec.to}&max_points=${compact ? 240 : 400}`)))
      .then((r) => { if (live) setSeries(r); }).catch((e) => { if (live) setError(e?.message || "Could not load the samples"); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- spec is rebuilt each render; its key, from and to identify it
  }, [caseId, spec.key, spec.from, spec.to, compact, load]);

  const geo = useMemo(() => {
    if (!series) return null;
    const all = series.flatMap((x: Any) => x.points.map((p: number[]) => p[1]));
    if (spec.band) all.push(spec.band.lo, spec.band.hi);
    if (!all.length) return null;
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    const x = (t: number) => L + ((t - spec.from) / (spec.to - spec.from)) * (W - L - R);
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    return { lo, hi, x, y };
  }, [series, spec]);

  const rawUnit = series?.find((x: Any) => x.unit)?.unit || "";
  const unit = rawUnit === "us" ? "µs" : rawUnit === "deg" ? "degrees" : rawUnit;
  const total = series?.reduce((n: number, x: Any) => n + x.samples, 0) ?? 0;
  const thinned = series?.some((x: Any) => x.thinned);
  const relTicks = [-20, -10, 0, 10, 20].filter((d) => spec.zero + d >= spec.from && spec.zero + d <= spec.to);

  if (error) return <p className={s.note}>Plot unavailable: {error}</p>;
  if (!series) return <div className={s.plot} aria-busy="true"><div className={s.plotLoading}>Loading the recorded samples…</div></div>;
  if (!geo || total === 0) return <p className={s.note}>No recorded samples of {spec.title} in this window.</p>;
  const { x, y } = geo;
  return (
    <figure className={`${s.plot} ${compact ? s.plotCompact : ""}`}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${spec.title}, ${total} recorded samples from ${Math.round(spec.from - spec.zero)} to +${Math.round(spec.to - spec.zero)} s around the finding`}>
        {spec.band && <g>
          <rect x={L} width={W - L - R} y={y(spec.band.hi)} height={Math.max(1, y(spec.band.lo) - y(spec.band.hi))} className={s.plotBand} />
          <text x={W - R - 4} y={y(spec.band.hi) - 4} textAnchor="end" className={s.plotBandLabel}>{spec.band.label}</text>
        </g>}
        {spec.region && <g>
          <rect x={x(spec.region.from)} width={Math.max(2, x(spec.region.to) - x(spec.region.from))} y={T} height={H - T - B} className={s.plotRegion} />
          <text x={x(spec.region.from) + 4} y={T - 7} className={s.plotRegionLabel}>{spec.region.label}</text>
        </g>}
        {spec.marker && <g>
          <line x1={x(spec.marker.at)} x2={x(spec.marker.at)} y1={T} y2={H - B} className={s.plotMarker} />
          <text x={x(spec.marker.at) + 4} y={T - 7} className={s.plotRegionLabel}>{spec.marker.label}</text>
        </g>}
        {ticks(geo.lo, geo.hi).map((v) => <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={s.plotGrid} />
          <text x={L - 6} y={y(v) + 4} textAnchor="end" className={s.plotTick}>{fmt(v)}</text>
        </g>)}
        {relTicks.map((d) => <text key={d} x={x(spec.zero + d)} y={H - 8} textAnchor="middle" className={s.plotTick}>{d === 0 ? "0 s" : `${d > 0 ? "+" : "−"}${Math.abs(d)}`}</text>)}
        {series.map((ser: Any, i: number) => {
          const tr = spec.traces[i];
          const pts = ser.points.map((p: number[]) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
          return <polyline key={tr.label} points={pts} className={tr.role === "command" ? s.plotCommand : s.plotMeasured} />;
        })}
      </svg>
      <figcaption className={s.plotCaption}>
        {spec.traces.map((t) => <span key={t.label} className={t.role === "command" ? s.keyCommand : s.keyMeasured}>{t.label}</span>)}
        {spec.band && <span className={s.keyBand}>{spec.band.label}</span>}
        <span className={s.dim}>{unit && `${unit} · `}seconds from the finding · {total.toLocaleString("en")} recorded samples{thinned ? ", thinned for display (peaks kept)" : ""}</span>
      </figcaption>
    </figure>
  );
}
