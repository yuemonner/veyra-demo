"use client";

// System-level investigation and physical checks on a SYNTHETIC battery incident.
// The data is computed by Veyra's deterministic engine (scripts/demo/make_system_investigation.py) and served as a static
// file, so the page needs no API and shows no customer data. Every statement below is the engine's own text.

import { useEffect, useMemo, useState } from "react";
import s from "./system.module.css";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const SIGNAL: Record<string, string> = {
  bms_fault_level: "BMS fault level", bms_state: "BMS state", power_state: "Vehicle power", dcdc_state: "DC/DC converter",
  main_negative_closed: "Main − contactor", main_positive_closed: "Main + contactor", precharge_closed: "Precharge contactor",
};
const CHECK: Record<string, string> = {
  charge_accounting: "Charge balance", ocv_consistency: "Rested voltage against SOC", pack_cell_consistency: "Pack against cells",
  protection_sequence: "Protection sequence", relaxation: "Recovery at rest", restart_eligibility: "Restart after protection",
  weak_cell_attribution: "Weak cell against whole pack",
};
const OUTCOME: Record<string, [string, string]> = {
  PASS: ["Consistent", s.ok], VIOLATION: ["Contradiction found", s.bad], INDETERMINATE: ["Needs more data", s.warn], NOT_APPLICABLE: ["Not applicable", s.muted],
};
const STATUS: Record<string, string> = {
  observed: "Observed", open: "Open", insufficient_alone: "Not enough on its own", weakened: "Weakened", excluded: "Excluded", confirmed: "Confirmed",
};
const ROLE: Record<string, string> = {
  observed_symptom: "What happened", protective_response: "Protective response", upstream_cause: "Possible cause", contributing_factor: "Contributing factor",
};
const GRADE: Record<string, string> = {
  leading: "Leads", leading_if_flat_ocv: "Check first if the chemistry has a flat voltage curve", possible: "Possible",
  contributing_timing_only: "Sets the timing only",
};
const FLAG: Record<string, [string, string]> = {
  deviation: ["Deviation from the declared rule", s.bad], safety: ["Safety", s.bad], question: ["Question for the manufacturer", s.warn],
  review: ["Review", s.warn],
};

const sec = (t: number | null | undefined) => (t == null ? "" : `${t < 0 ? "−" : ""}${Math.abs(t).toFixed(Math.abs(t) % 1 === 0 ? 0 : 2)} s`);
// The rules here are declared for the synthetic machine, so the engine's "customer-confirmed" reads as "declared".
const say = (t?: string) => (t || "").replace(/Customer-confirmed rule/g, "Declared rule").replace(/Customer-confirmed/g, "Declared");
const ev = (e: Any) => (e ? `${e.file}, row ${e.row}` : "");

function Evidence({ items }: { items?: Any[] }) {
  if (!items || !items.length) return null;
  return <div className={s.refs}>{items.map((e: Any, i: number) => <span key={i} className={s.ref} title={`${e.source} · ${e.field} = ${e.value}`}>{ev(e)}</span>)}</div>;
}

function Badge({ flag }: { flag?: string | null }) {
  if (!flag || !FLAG[flag]) return null;
  return <span className={`${s.badge} ${FLAG[flag][1]}`}>{FLAG[flag][0]}</span>;
}

/* Lowest and highest cell voltage through the fault window, with the inferred protection thresholds and the shutdown. */
function CellChart({ data }: { data: Any }) {
  const W = 760, H = 260, L = 46, R = 14, T = 14, B = 34;
  const lo: number[][] = data.series.lines.v_cell_min?.points || [];
  const hi: number[][] = data.series.lines.v_cell_max?.points || [];
  const rows: Any[] = data.system.thresholds.rows || [];
  const all = [...lo, ...hi];
  if (!all.length) return null;
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
  const v0 = 2.3, v1 = Math.max(3.4, ...all.map((p) => p[1]));
  const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - v0) / (v1 - v0)) * (H - T - B);
  const path = (pts: number[][]) => pts.filter((p) => p[1] >= v0).map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const ticks = [2.4, 2.6, 2.8, 3.0, 3.2, 3.4].filter((v) => v <= v1);
  const tTicks: number[] = [];
  for (let t = Math.ceil(t0 / 300) * 300; t <= t1; t += 300) tTicks.push(t);
  const open = data.series.open_t;
  return (
    <figure className={s.chart}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Lowest and highest cell voltage through the fault window">
        {ticks.map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={s.grid} /><text x={L - 8} y={y(v) + 4} className={s.axis} textAnchor="end">{v.toFixed(1)}</text></g>)}
        {tTicks.map((t) => <text key={t} x={x(t)} y={H - 12} className={s.axis} textAnchor="middle">{t} s</text>)}
        {rows.map((r: Any) => r.inferred_threshold != null && (
          <g key={r.level}>
            <line x1={L} x2={W - R} y1={y(r.inferred_threshold)} y2={y(r.inferred_threshold)} className={s.threshold} />
            <text x={W - R - 4} y={y(r.inferred_threshold) - 4} className={s.thresholdLabel} textAnchor="end">level {r.level} ≈ {r.inferred_threshold} V</text>
            <circle cx={x(r.t)} cy={y(r.signal_value)} r={4} className={s.levelDot} />
          </g>))}
        {open != null && <g><line x1={x(open)} x2={x(open)} y1={T} y2={H - B} className={s.open} /><text x={x(open) + 6} y={T + 12} className={s.openLabel}>contactors open</text></g>}
        <path d={path(hi)} className={s.hi} />
        <path d={path(lo)} className={s.lo} />
      </svg>
      <figcaption className={s.caption}>
        <span><i className={s.keyLo} /> Lowest cell</span><span><i className={s.keyHi} /> Highest cell</span>
        <span><i className={s.keyTh} /> Thresholds inferred from the record</span>
        <span>Seconds from the BMS level-1 alarm</span>
      </figcaption>
    </figure>
  );
}

export default function SystemInvestigation() {
  const [data, setData] = useState<Any>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/demo/system-investigation.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setData).catch((e) => setError(String(e)));
  }, []);

  const hyps = useMemo(() => (data ? (data.explanations.hypotheses as Any[]) : []), [data]);

  if (error) return <div className={s.page}><p className={s.note}>The investigation could not be loaded ({error}). Reload the page.</p></div>;
  if (!data) return <div className={s.page}><p className={s.note}>Loading the investigation…</p></div>;

  const sys = data.system, phys = data.physics, ex = data.explanations, nx = data.next;
  const align = phys.alignment.value || {};
  const lead = ex.leading || ex.conditional_leading;

  return (
    <div className={s.page}>
      <header className={s.topbar}>
        <a href="/field-case" className={s.brand}><span className={s.mark}>V</span>Veyra</a>
        <span className={s.crumb}>System-level investigation</span>
        <span className={s.spacer} />
        <a href="/field-case" className={s.quiet}>Field cases</a>
      </header>

      <main className={s.main}>
        <section className={s.hero}>
          <p className={s.tags}><span className={s.tag}>Synthetic recording</span><span className={s.tag}>Computed by Veyra&apos;s engine</span><span className={s.tag}>Every number traces to a file and row</span></p>
          <h1>{data.case.title}</h1>
          <p className={s.lede}>Reported: “{data.case.reported}” {data.case.machine}.</p>
          <div className={s.summary}>
            <div><b>What the record shows</b><p>{sys.symptom.statement}</p></div>
            <div><b>What the protection did</b><p>{say((sys.restarts.findings.find((f: Any) => f.id === "protection_not_latched") || {}).statement)}</p></div>
            <div><b>Battery physics</b><p>{phys.alignment.statement?.split(": it sets")[0]}: it sets the timing, not the size.</p></div>
            {lead && <div><b>Check first</b><p>{lead.statement}</p></div>}
          </div>
        </section>

        <h2 className={s.part}><span>1</span>System-level investigation</h2>
        <p className={s.partLede}>Veyra reads the whole machine before the battery: the reported symptom against the record, the order in which BMS, VCU, DC/DC and contactors acted, which signals are real measurements, and what happened at every restart.</p>

        <section className={s.panel}>
          <h3>The symptom against the record</h3>
          <p>{sys.symptom.statement}</p>
          <dl className={s.facts}>
            <div><dt>Drive requested</dt><dd>{sys.symptom.value?.drive_requested ? "Yes" : "No"}</dd></div>
            <div><dt>Vehicle moved</dt><dd>{sys.symptom.value?.moved ? "Yes" : "No"}</dd></div>
            <div><dt>Speed samples read</dt><dd>{(sys.symptom.value?.speed_frames ?? 0).toLocaleString("en")}</dd></div>
          </dl>
        </section>

        <section className={s.panel}>
          <h3>Fault sequence</h3>
          <CellChart data={data} />
          <div className={s.scroll}>
            <table className={s.table}>
              <thead><tr><th>Time</th><th>Signal</th><th>Change</th><th>Source</th></tr></thead>
              <tbody>{sys.sequence.events.map((e: Any, i: number) => (
                <tr key={i}><td className={s.num}>{sec(e.t)}</td><td>{SIGNAL[e.role] || e.role}</td><td>{e.from} → {e.to}</td><td className={s.src}>{ev(e.evidence?.[0])}</td></tr>))}
              </tbody>
            </table>
          </div>
          {sys.sequence.findings.map((f: Any) => <p key={f.id} className={s.finding}>{f.statement}</p>)}
        </section>

        <section className={s.panel}>
          <h3>Protection thresholds, inferred from the record</h3>
          <p>{sys.thresholds.statement}</p>
          <div className={s.scroll}>
            <table className={s.table}>
              <thead><tr><th>Level</th><th>Raised at</th><th>Lowest cell then</th><th>Threshold about</th><th>Delay</th></tr></thead>
              <tbody>{sys.thresholds.rows.map((r: Any) => (
                <tr key={r.level}><td>{r.level}</td><td className={s.num}>{sec(r.t)}</td><td className={s.num}>{r.signal_value} V</td><td className={s.num}>{r.inferred_threshold} V</td><td className={s.num}>{r.delay_s != null ? `${r.delay_s} s` : ""}</td></tr>))}
              </tbody>
            </table>
          </div>
          <p className={s.limit}>{sys.thresholds.limits.join(" ")}</p>
        </section>

        <section className={s.panel}>
          <h3>Restarts and high-voltage authorisation</h3>
          <p className={s.rule}>Rule declared for this machine: {data.case.rules.join(" ")}</p>
          <div className={s.scroll}>
            <table className={s.table}>
              <thead><tr><th>Closure</th><th>Time</th><th>Fault level at closure</th><th>Weakest cell at closure</th><th>Lowest while on</th><th>Ended by another shutdown</th></tr></thead>
              <tbody>{sys.restarts.rows.map((r: Any) => (
                <tr key={r.n}><td>{r.n}</td><td className={s.num}>{sec(r.t)}</td><td>{r.fault_level_at_close}</td><td className={s.num}>{r.min_cell_v_at_close} V</td><td className={s.num}>{r.lowest_cell_v_while_on} V</td><td>{r.ended_by_protective_shutdown != null ? `Yes, at ${sec(r.ended_by_protective_shutdown)}` : "No"}</td></tr>))}
              </tbody>
            </table>
          </div>
          {sys.restarts.findings.map((f: Any) => <div key={f.id} className={s.findingRow}><Badge flag={f.flag} /><p>{say(f.statement)}</p><Evidence items={f.evidence} /></div>)}
        </section>

        <section className={s.panel}>
          <h3>Which signals can be trusted</h3>
          {sys.signals.items.length ? sys.signals.items.map((i: Any) => <div key={i.id} className={s.findingRow}><Badge flag={i.flag} /><p>{i.statement}</p><Evidence items={i.evidence} /></div>)
            : <p>No copied, clamped or saturated signal was found.</p>}
        </section>

        <h2 className={s.part}><span>2</span>Physical checks</h2>
        <p className={s.partLede}>Each check tests one physical law against the record and says what it could not decide. Charge is counted, cells are compared by charge rather than by time, and the weakest cell is weighed against the whole pack.</p>

        <section className={s.checks}>
          {phys.checks.map((c: Any) => {
            const o = OUTCOME[c.outcome] || [c.outcome, s.muted];
            return <article key={c.id} className={s.check}><header><h4>{CHECK[c.id] || c.id}</h4><span className={`${s.badge} ${o[1]}`}>{o[0]}</span></header><p>{c.summary}</p></article>;
          })}
        </section>

        <section className={s.panel}>
          <h3>Weakest cell against the whole pack</h3>
          <p>{phys.alignment.statement}</p>
          <dl className={s.facts}>
            <div><dt>Lead of the weakest cell</dt><dd>{align.offset_range_ah ? `${align.offset_range_ah[0]}–${align.offset_range_ah[1]} Ah` : ""}</dd></div>
            <div><dt>At the load in the record</dt><dd>about {align.minutes_at_typical_load} min</dd></div>
            <div><dt>Share of the SOC discrepancy</dt><dd>about {Math.round((align.share_of_discrepancy || 0) * 100)}%</dd></div>
          </dl>
          <Evidence items={phys.alignment.evidence} />
        </section>

        {phys.onset && <section className={s.panel}><h3>When the SOC error arose</h3><p>{phys.onset.statement}</p></section>}

        <h2 className={s.part}><span>3</span>Explanations and the next check</h2>
        <p className={s.partLede}>Explanations stay open until evidence separates them. A leading explanation is labelled with its basis: evidence from this record, or reference knowledge that still needs confirming.</p>

        {lead && <section className={`${s.panel} ${s.lead}`}><h3>{ex.leading ? "Leading explanation" : "Check first, on one condition"}</h3><p>{lead.statement}</p></section>}

        {nx.safety_holds.length > 0 && (
          <section className={`${s.panel} ${s.hold}`}>
            <h3>Before anything else</h3>
            {nx.safety_holds.map((h: Any) => <div key={h.id}><b>{h.title}</b><p>{h.why}</p></div>)}
          </section>)}

        <section className={s.hyps}>
          {hyps.filter((h: Any) => h.role !== "observed_symptom").map((h: Any) => (
            <article key={h.id} className={`${s.hyp} ${h.status === "weakened" || h.status === "excluded" ? s.dim : ""}`}>
              <header><span className={s.role}>{ROLE[h.role] || h.role}</span><span className={s.status}>{STATUS[h.status] || h.status}{h.grade ? ` · ${GRADE[h.grade] || h.grade}` : ""}</span></header>
              <h4>{h.label}</h4>
              {h.record_evidence?.length > 0 && <ul className={s.evlist}>{h.record_evidence.slice(0, 3).map((e: Any, i: number) => <li key={i} data-dir={e.direction}>{e.claim}</li>)}</ul>}
              {!h.record_evidence?.length && h.status_reason && <p>{h.status_reason}</p>}
            </article>))}
        </section>


        <section className={s.panel}>
          <h3>Next checks, most decisive first</h3>
          <ol className={s.next}>
            {nx.recommended.map((r: Any) => (
              <li key={r.id}><b>{r.title}</b><span className={s.meta}>{r.hv_state === "none" ? "No vehicle contact" : "Needs the vehicle"}</span>
                {r.distinguishes_labels?.length > 0 && <p>Separates: {r.distinguishes_labels.join("; ")}.</p>}</li>))}
          </ol>
        </section>

        <footer className={s.foot}>
          Synthetic recording, generated from first principles; no customer data. Computed by Veyra&apos;s engine ({data.case.engine.audit}, {data.case.engine.expert_layer}).
        </footer>
      </main>
    </div>
  );
}
