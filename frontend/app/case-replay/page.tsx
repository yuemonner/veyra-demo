"use client";

// Guided engineering replay (LOCAL ONLY). Reads /engineering/* from the local API, which answers only when
// VEYRA_ENGINEERING_LOCAL=true and the request comes from loopback. Confidential cases never leave this machine.
// Every claim shows its basis: evidence (file + row), analysis (formula), historical (an earlier report), assumption.

import { useCallback, useEffect, useMemo, useState } from "react";
import s from "./replay.module.css";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8050";

type Ref = { recording: string; window: string; file: string; row: number; t: number; source: string; field: string; value: number | null };
type Claim = { text: string; basis: "evidence" | "analysis" | "historical" | "assumption"; refs: Ref[]; [k: string]: Any };

const BASIS: Record<string, string> = { evidence: "Evidence", analysis: "Analysis", historical: "Historical", assumption: "Assumption" };
const STATUS: Record<string, string> = {
  observed: "Observed", open: "Open", insufficient_alone: "Insufficient alone", weakened: "Weakened", excluded: "Excluded", confirmed: "Confirmed",
};
const ROLE: Record<string, string> = {
  observed_symptom: "Observed symptom", protective_response: "Protective response", upstream_cause: "Candidate upstream cause", contributing_factor: "Contributing factor",
};
const OUTCOME_CLASS: Record<string, string> = { PASS: s.ok, VIOLATION: s.bad, INDETERMINATE: s.warn, NOT_APPLICABLE: s.muted };

async function get(path: string) {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

// Writes name the engineer (x-veyra-actor). This is a local identity recorded with each entry, not authentication.
async function post(path: string, body: Any, actor: string) {
  const r = await fetch(`${API}${path}`, { method: "POST", cache: "no-store", headers: { "content-type": "application/json", "x-veyra-actor": actor }, body: JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) {
    let msg = text;
    try { const j = JSON.parse(text); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep text */ }
    throw new Error(msg);
  }
  return JSON.parse(text);
}

const fmtT = (t: number) => `${t < 0 ? "−" : ""}${Math.abs(t).toFixed(t % 1 === 0 ? 0 : 3)} s`;

export default function CaseReplay() {
  const [cases, setCases] = useState<Any[] | null>(null);
  const [caseId, setCaseId] = useState<string>("");
  const [pack, setPack] = useState<Any>(null);
  const [error, setError] = useState<string>("");
  const [step, setStep] = useState<string>("facts");
  const [view, setView] = useState<"case" | "recordings">("case");
  const [qual, setQual] = useState("none");
  const [drawer, setDrawer] = useState<Ref | null>(null);
  const [actor, setActorState] = useState("");
  useEffect(() => { try { setActorState(window.localStorage.getItem("veyra-engineer") ?? ""); } catch { /* storage unavailable */ } }, []);
  const setActor = (v: string) => { setActorState(v); try { window.localStorage.setItem("veyra-engineer", v); } catch { /* storage unavailable */ } };
  const refreshCases = useCallback(() => get("/engineering/cases").then((d) => setCases(d.cases)).catch(() => undefined), []);
  const openCase = (id: string) => { refreshCases(); setCaseId(id); setStep("facts"); setView("case"); };
  const reload = useCallback(() => get(`/engineering/cases/${encodeURIComponent(caseId)}/replay?qualification=${qual}`).then(setPack), [caseId, qual]);

  useEffect(() => {
    get("/engineering/cases").then((d) => {
      setCases(d.cases);
      const q = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
      const fromUrl = q?.get("case") ?? null;
      if (q?.get("step")) setStep(q.get("step") as string);
      if (q?.get("view") === "recordings") setView("recordings");
      if (q?.get("qualification")) setQual(q.get("qualification") as string);
      setCaseId(fromUrl || d.cases.find((c: Any) => !c.synthetic)?.id || d.cases[0]?.id || "");
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!caseId) return;
    setPack(null);
    setError("");
    get(`/engineering/cases/${encodeURIComponent(caseId)}/replay?qualification=${qual}`).then(setPack).catch((e) => setError(String(e)));
  }, [caseId, qual]);

  if (error) return <main className={s.page}><div className={s.errorBox}><h1>Replay unavailable</h1><p>{error}</p>
    <p className={s.dim}>This page reads the local engineering API. Start it with <code>scripts/case_replay_local.sh</code> (it sets VEYRA_ENGINEERING_LOCAL=true and binds to 127.0.0.1).</p></div></main>;
  if (cases && view === "recordings") return <main className={s.page}>
    <ViewTabs view={view} setView={setView} />
    <Recordings actor={actor} setActor={setActor} onOpen={openCase} />
  </main>;
  if (!cases || !pack) return <main className={s.page}><p className={s.dim}>Loading the case and running the checks…</p></main>;

  const sec = (id: string) => pack.sections.find((x: Any) => x.id === id);
  const steps = [...pack.sections.map((x: Any) => ({ id: x.id, n: x.n, title: x.title })), { id: "history", n: "H", title: "Earlier report, reconciled" }, { id: "trace", n: "T", title: "Full trace" }];
  const idx = steps.findIndex((x) => x.id === step);

  return (
    <main className={s.page}>
      <ViewTabs view={view} setView={setView} />
      <header className={s.head}>
        <div className={s.headRow}>
          <div>
            <p className={s.kicker}>Guided engineering replay</p>
            <h1 className={s.title}>{pack.case.title}</h1>
          </div>
          <label className={s.casePick}>Case
            <select value={caseId} onChange={(e) => { setCaseId(e.target.value); setStep("facts"); }}>
              {cases.map((c) => <option key={c.id} value={c.id}>{c.synthetic ? "SYNTHETIC · " : c.confidential ? "Confidential · " : ""}{c.recording ? `Recording · ${c.title}` : c.id}</option>)}
            </select>
          </label>
        </div>
        <div className={pack.case.synthetic ? s.bannerSynthetic : s.bannerConfidential}>{pack.banner}</div>
        <dl className={s.meta}>
          <div><dt>Windows</dt><dd>{pack.case.windows.join(" · ")}</dd></div>
          <div><dt>Signal profile</dt><dd>{pack.case.profile}</dd></div>
          <div><dt>Engine</dt><dd>{pack.trace.engine}</dd></div>
          <div><dt>AI investigator</dt><dd>{AGENT_MODE[pack.agent.mode] ?? pack.agent.mode}{pack.agent.model ? ` (${pack.agent.model})` : ""}{pack.agent.at ? `, ${new Date(pack.agent.at).toLocaleString()}` : ""}. Step 3 shows it.</dd></div>
        </dl>
      </header>

      <div className={s.layout}>
        <nav className={s.rail} aria-label="Walkthrough steps">
          {steps.map((x) => (
            <button key={x.id} className={x.id === step ? s.railOn : s.railItem} onClick={() => setStep(x.id)}>
              <span className={s.railN}>{x.n}</span><span>{x.title}</span>
            </button>
          ))}
        </nav>

        <section className={s.main}>
          {step === "facts" && <Facts sec={sec("facts")} onRef={setDrawer} />}
          {step === "contradiction" && <Contradiction sec={sec("contradiction")} onRef={setDrawer} />}
          {step === "causes" && <Investigator caseId={caseId} agent={pack.agent} actor={actor} setActor={setActor} reload={reload} />}
          {step === "causes" && <Causes sec={sec("causes")} onRef={setDrawer} onOpen={openCase} />}
          {step === "next" && <Next sec={sec("next")} qual={qual} setQual={setQual} />}
          {step === "scenario" && <Scenario sec={sec("scenario")} caseId={caseId} actor={actor} setActor={setActor} reload={reload}
            hypotheses={sec("causes").assessment.hypotheses} plan={sec("next").plans.hv_qualified ?? sec("next").plans.none} />}
          {step === "history" && <History h={pack.history} />}
          {step === "trace" && <Trace pack={pack} />}
          <div className={s.pager}>
            <button disabled={idx <= 0} onClick={() => setStep(steps[idx - 1].id)}>← {idx > 0 ? steps[idx - 1].title : ""}</button>
            <button disabled={idx >= steps.length - 1} onClick={() => setStep(steps[idx + 1].id)}>{idx < steps.length - 1 ? steps[idx + 1].title : ""} →</button>
          </div>
        </section>
      </div>
      {drawer && <EvidenceDrawer caseId={caseId} r={drawer} onClose={() => setDrawer(null)} />}
    </main>
  );
}

function Basis({ b }: { b: string }) {
  return <span className={`${s.basis} ${s["basis_" + b]}`}>{BASIS[b] ?? b}</span>;
}

function Refs({ refs, onRef }: { refs: Ref[]; onRef: (r: Ref) => void }) {
  if (!refs?.length) return null;
  return <span className={s.refs}>{refs.slice(0, 4).map((r, i) => (
    <button key={i} className={s.ref} onClick={() => onRef(r)} title={`${r.source} · ${r.field} = ${r.value}`}>{r.file || r.window}:{r.row} · {fmtT(r.t)}</button>
  ))}</span>;
}

function ClaimList({ claims, onRef }: { claims: Claim[]; onRef: (r: Ref) => void }) {
  return <ul className={s.claims}>{claims.map((c, i) => (
    <li key={i} className={s.claim}><Basis b={c.basis} /><div><p>{c.text}</p>
      {c.assumptions?.length ? <p className={s.assume}>Assumes: {c.assumptions.join("; ")}</p> : null}
      <Refs refs={c.refs} onRef={onRef} /></div></li>
  ))}</ul>;
}

function SectionHead({ n, title, lead }: { n: number | string; title: string; lead: string }) {
  return <div className={s.secHead}><span className={s.secN}>{n}</span><div><h2>{title}</h2><p className={s.lead}>{lead}</p></div></div>;
}

function Facts({ sec, onRef }: { sec: Any; onRef: (r: Ref) => void }) {
  const man = sec.integrity.manifest;
  return <>
    <SectionHead n={1} title={sec.title} lead="What the recording shows, before any interpretation. Each statement links to the rows it comes from." />
    <ClaimList claims={sec.claims} onRef={onRef} />
    <h3 className={s.h3}>Timeline</h3>
    <table className={s.table}><thead><tr><th>Window</th><th>Time</th><th>What was recorded</th><th>Basis</th><th>Rows</th></tr></thead>
      <tbody>{sec.timeline.map((e: Any, i: number) => (
        <tr key={i}><td>{e.window}</td><td className={s.num}>{fmtT(e.t)}</td><td>{e.what}</td><td><Basis b={e.basis} /></td><td><Refs refs={e.refs} onRef={onRef} /></td></tr>
      ))}</tbody></table>
    <h3 className={s.h3}>Data integrity</h3>
    <div className={s.note}>
      {man?.sha256 ? <><p>Uploaded recording <code>{man.file}</code>, SHA-256 {String(man.sha256).slice(0, 16)}…, read where the recording ingestion stored it ({man.manifest}).</p>
        <p>Signal profile <code>{man.profile}</code> bound {Object.entries(man.bindings || {}).map(([k, v]) => `${k} → ${v}`).join(", ")}. Time: {man.time}.</p>
        {man.profile_note && <p><b>Read with care:</b> {man.profile_note}</p>}
        {(man.notes || []).map((n: string, i: number) => <p key={i}>{n}</p>)}</> : man ? <p>{man.verified.length} file(s) verified against {man.manifest} by SHA-256{man.mismatched.length ? `; MISMATCHED: ${man.mismatched.join(", ")}` : ""}{man.not_in_manifest.length ? `; not in manifest: ${man.not_in_manifest.join(", ")}` : ""}.</p> : <p>No manifest for this case (synthetic data is generated in memory).</p>}
      {Object.entries(sec.integrity.excluded_files || {}).map(([k, v]) => <p key={k}>Not read: <code>{k}</code>: {String(v)}</p>)}
      <p className={s.dim}>Recording digest {String(sec.integrity.recording_digest).slice(0, 16)}… · nothing resampled, interpolated or carried across gaps.</p>
    </div>
  </>;
}

function Contradiction({ sec, onRef }: { sec: Any; onRef: (r: Ref) => void }) {
  const [w, setW] = useState<string>(() => (sec.charts.find((c: Any) => c.episodes.length) || sec.charts[0]).window);
  const [zoom, setZoom] = useState(true);
  const chart = sec.charts.find((c: Any) => c.window === w);
  return <>
    <SectionHead n={2} title={sec.title} lead="Reported SOC against the charge the current sensor measured. A change that measured charge cannot explain is a physical contradiction to resolve, not a cause." />
    <div className={`${s.verdict} ${OUTCOME_CLASS[sec.check_outcome]}`}><strong>Charge accounting: {sec.check_outcome}</strong><span>{sec.check_summary}</span></div>
    <div className={s.tabs}>
      {sec.charts.map((c: Any) => <button key={c.window} className={c.window === w ? s.tabOn : s.tab} onClick={() => setW(c.window)}>{c.window}{c.episodes.length ? " ●" : ""}</button>)}
      {chart.episodes.length > 0 && <label className={s.toggle}><input type="checkbox" checked={zoom} onChange={(e) => setZoom(e.target.checked)} /> around the correction</label>}
    </div>
    <ChartPanel chart={chart} zoom={zoom} />
    <ClaimList claims={sec.claims} onRef={onRef} />
    {sec.quantities?.note && <div className={s.callout}><strong>Two different numbers.</strong> {sec.quantities.note}
      <ul>{Object.entries(sec.quantities).filter(([k]) => k !== "note").map(([k, v]: Any) => <li key={k}><b>{v.value} pp</b>: {v.meaning}{v.estimated ? " (includes an estimate for unrecorded time)" : ""}</li>)}</ul></div>}
    <h3 className={s.h3}>Calculations</h3>
    {sec.calculations.map((c: Any, i: number) => <div key={i} className={s.calc}><p className={s.calcName}>{c.name} <Basis b="analysis" /></p><code>{c.formula}</code>
      <p className={s.dim}>{Object.entries(c.inputs).map(([k, v]) => `${k} = ${v}`).join(" · ")} → <b>{String(c.value)} {c.unit}</b></p><p className={s.dim}>{c.provenance}</p></div>)}
  </>;
}

const COLORS: Record<string, string> = { soc: "var(--sr-thread)", pack_current: "var(--sr-ink-2)", min_cell_voltage: "var(--sr-fault)", max_cell_voltage: "var(--sr-verified)" };
const LABELS: Record<string, string> = { soc: "SOC reported", pack_current: "Pack current (discharge +)", min_cell_voltage: "Lowest cell", max_cell_voltage: "Highest cell" };

function ChartPanel({ chart, zoom }: { chart: Any; zoom: boolean }) {
  const all = chart.series.flatMap((x: Any) => x.points.filter((p: Any) => p.v !== null).map((p: Any) => p.t));
  let x0 = Math.min(...all), x1 = Math.max(...all);
  if (zoom && chart.episodes.length) {
    x0 = Math.max(x0, chart.episodes[0].from - 140);
    x1 = Math.min(x1, chart.episodes[chart.episodes.length - 1].to + 220);
  }
  const panels: { title: string; unit: string; lines: { key: string; label: string; color: string; dash?: string; pts: Any[] }[] }[] = [];
  const soc = chart.series.find((x: Any) => x.role === "soc");
  if (soc) panels.push({ title: "SOC", unit: "%", lines: [{ key: "soc", label: LABELS.soc, color: COLORS.soc, pts: soc.points },
    ...(chart.predicted?.points?.length ? [{ key: "pred", label: "SOC predicted by measured charge (analysis)", color: "var(--sr-anomaly)", dash: "5 4", pts: chart.predicted.points }] : [])] });
  const cur = chart.series.find((x: Any) => x.role === "pack_current");
  if (cur) panels.push({ title: "Pack current", unit: "A", lines: [{ key: "i", label: LABELS.pack_current, color: COLORS.pack_current, pts: cur.points }] });
  const cells = chart.series.filter((x: Any) => x.role.endsWith("cell_voltage"));
  if (cells.length) panels.push({ title: "Cell voltage", unit: "V", lines: cells.map((x: Any) => ({ key: x.role, label: LABELS[x.role], color: COLORS[x.role], pts: x.points })) });
  return <div className={s.chartWrap}>{panels.map((p) => <SignalChart key={p.title} panel={p} x0={x0} x1={x1} episodes={chart.episodes} markers={chart.markers} />)}
    <p className={s.dim}>Lines break where the record has a gap: nothing is drawn across unrecorded time. Shaded: SOC change not explained by measured charge. Dashed amber: analysis, not a recorded signal.</p></div>;
}

function SignalChart({ panel, x0, x1, episodes, markers }: { panel: Any; x0: number; x1: number; episodes: Any[]; markers: Any[] }) {
  const W = 860, H = 150, L = 52, R = 12, T = 22, B = 22;
  const vals = panel.lines.flatMap((l: Any) => l.pts.filter((p: Any) => p.v !== null && p.t >= x0 && p.t <= x1).map((p: Any) => p.v));
  if (!vals.length) return null;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo || 1) * 0.1; lo -= pad; hi += pad;
  const x = (t: number) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const paths = panel.lines.map((l: Any) => {
    let d = "", pen = false;
    for (const p of l.pts) {
      if (p.v === null) { pen = false; continue; }
      if (p.t < x0 || p.t > x1) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
      pen = true;
    }
    return { ...l, d };
  });
  const ticks = Array.from({ length: 6 }, (_, i) => x0 + ((x1 - x0) * i) / 5);
  return <figure className={s.chart}>
    <figcaption><b>{panel.title}</b> <span className={s.dim}>{panel.unit}</span>
      <span className={s.legend}>{panel.lines.map((l: Any) => <span key={l.key}><i style={{ background: l.color, opacity: l.dash ? 0.7 : 1 }} />{l.label}</span>)}</span></figcaption>
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={panel.title}>
      {episodes.map((e: Any, i: number) => e.to >= x0 && e.from <= x1 && <rect key={i} x={x(Math.max(e.from, x0))} y={T} width={Math.max(2, x(Math.min(e.to, x1)) - x(Math.max(e.from, x0)))} height={H - T - B} className={s.episode}><title>{e.label}</title></rect>)}
      {[0, 1, 2, 3].map((k) => { const v = lo + ((hi - lo) * k) / 3; return <g key={k}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={s.grid} /><text x={L - 6} y={y(v) + 3} className={s.axis} textAnchor="end">{v.toFixed(Math.abs(hi - lo) < 5 ? 2 : 0)}</text></g>; })}
      {ticks.map((t, i) => <text key={i} x={x(t)} y={H - 6} className={s.axis} textAnchor="middle">{t.toFixed(0)} s</text>)}
      {markers.filter((m: Any) => m.t >= x0 && m.t <= x1).map((m: Any, i: number) => <line key={i} x1={x(m.t)} x2={x(m.t)} y1={T} y2={H - B} className={s.marker}><title>{fmtT(m.t)}: {m.label}</title></line>)}
      {paths.map((p: Any) => <path key={p.key} d={p.d} fill="none" stroke={p.color} strokeWidth={1.6} strokeDasharray={p.dash} />)}
    </svg>
  </figure>;
}

function Causes({ sec, onRef, onOpen }: { sec: Any; onRef: (r: Ref) => void; onOpen: (id: string) => void }) {
  const a = sec.assessment;
  const k = sec.knowledge;
  const groups = ["observed_symptom", "protective_response", "upstream_cause", "contributing_factor"];
  const priorFor = (hid: string) => (k?.items ?? []).filter((i: Any) => i.hypothesis_id === hid && i.applicability !== "does not apply");
  return <>
    <SectionHead n={3} title={sec.title} lead="Symptom, protective response and candidate causes are kept apart. Statuses are words, not probabilities; only an engineer-verified outcome can confirm or exclude." />
    <div className={s.callout}>{a.summary ?? a.note}</div>
    {a.family_scope && <p className={s.small}>Hypothesis family: {a.family_scope.applies ? "battery-electric vehicle" : "none applies"} · machine class {a.family_scope.machine_class ?? "not stated"} · {(a.family_scope.reasons ?? []).join("; ")}</p>}
    <Knowledge k={k} onOpen={onOpen} />
    {groups.map((g) => {
      const hs = a.hypotheses.filter((h: Any) => h.role === g);
      if (!hs.length) return null;
      return <div key={g}><h3 className={s.h3}>{ROLE[g]}</h3><div className={s.cards}>{hs.map((h: Any) => (
        <article key={h.id} className={s.card}>
          <header><span className={s.hid}>{h.id}</span><h4>{h.label}</h4><span className={`${s.status} ${s["st_" + h.status]}`}>{STATUS[h.status] ?? h.status}</span></header>
          <p>{h.statement}</p>
          <p className={s.reason}>{h.status_reason}</p>
          {priorFor(h.id).map((i: Any) => <p key={i.case_id} className={s.prior}>Earlier verified: <b>{i.result}</b> in {i.case_id}{i.source_synthetic ? " (SYNTHETIC)" : ""} by {i.verified_by} ({i.applicability}). Not a result here: it stays {STATUS[h.status] ?? h.status} until verified in this case.</p>)}
          {h.supporting.length > 0 && <Evid title="Supporting" items={h.supporting} onRef={onRef} />}
          {h.contradicting.length > 0 && <Evid title="Against / limits" items={h.contradicting} onRef={onRef} />}
          {h.missing.length > 0 && <div className={s.missing}><b>Missing</b><ul>{h.missing.map((m: string, i: number) => <li key={i}>{m}</li>)}</ul></div>}
        </article>))}</div></div>;
    })}
  </>;
}

const AGENT_MODE: Record<string, string> = {
  agent: "Ran", not_run: "Not run yet: everything shown is deterministic", blocked: "Not run: real (non-SYNTHETIC) data needs the data owner's permission",
  unavailable: "Not run: no model is configured", agent_incomplete: "Stopped without a report", agent_failed: "Model call failed", failed: "Failed",
};
const VERDICT_CLASS: Record<string, string> = { accepted: s.ok, downgraded: s.warn, flagged: s.warn, rejected: s.bad };

function Investigator({ caseId, agent, actor, setActor, reload }: { caseId: string; agent: Any; actor: string; setActor: (v: string) => void; reload: () => Promise<void> }) {
  const [st, setSt] = useState<Any>(null);
  const { busy, msg, run } = useAction(actor, reload);
  const path = `/engineering/cases/${encodeURIComponent(caseId)}`;
  const load = useCallback(() => get(`${path}/investigation`).then((x) => { setSt(x); return x; }).catch(() => null), [path]);
  useEffect(() => { load(); }, [load, agent?.investigation_id]);
  useEffect(() => {
    if (!st?.running) return;
    const t = setInterval(async () => { const x = await load(); if (x && !x.running) { clearInterval(t); reload(); } }, 2000);
    return () => clearInterval(t);
  }, [st?.running, load, reload]);
  const claims: Any[] = agent?.constrained_claims ?? [];
  const report = agent?.agent_report;
  return <div className={s.knowledge}>
    <h3 className={s.h3}>AI investigator</h3>
    <p className={s.dim}>It reasons only with Veyra's tools: the physical checks, the derived facts, the hypothesis assessment, earlier verified knowledge, scenario results on this and other recordings, and the next-check plan. Every statement is then held to the checks. Its statements are proposals: one becomes a tracked hypothesis only when an engineer adopts it, and a cause only when an engineer verifies it.</p>
    <p><b>{AGENT_MODE[agent?.mode] ?? agent?.mode}</b>{agent?.model ? ` · ${agent.model}` : ""}{agent?.trigger ? ` · ${agent.trigger === "ingest" ? "started when the recording arrived" : agent.trigger === "requested" ? "requested by an engineer" : agent.trigger}` : ""}{agent?.at ? ` · ${new Date(agent.at).toLocaleString()}` : ""}</p>
    {agent?.reason && agent.mode !== "agent" && <p className={s.note}>{agent.reason}</p>}
    {st && <p className={s.small}>{st.agent_rule}</p>}
    <div className={s.btnRow}>
      <button disabled={busy || !!st?.running} onClick={() => run("Run the investigation", async () => { await post(`${path}/investigate`, {}, actor); await load(); })}>{st?.running ? "Investigating…" : agent?.mode && agent.mode !== "not_run" ? "Run the investigation again" : "Run the investigation"}</button>
      {!actor.trim() && <label className={s.small}>Engineer <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="your name" maxLength={80} /></label>}
    </div>
    <Msg m={msg} />
    {agent?.trace?.length > 0 && <details className={s.traceItem}><summary>Tools it called ({agent.trace.length})</summary>
      <ol className={s.small}>{agent.trace.map((t: Any, i: number) => <li key={i}><code>{t.tool}</code>{t.input && Object.keys(t.input).length ? ` ${JSON.stringify(t.input)}` : ""}{t.error ? " (error)" : ""}</li>)}</ol></details>}
    {report && <div className={s.callout}><b>Its summary:</b> {report.summary}{report.next_check ? <> · <b>its next check:</b> {report.next_check}</> : null}</div>}
    {claims.length > 0 && <table className={s.table}><thead><tr><th>Its statement</th><th>Held to the checks</th><th></th></tr></thead><tbody>
      {claims.map((c: Any, i: number) => <tr key={i}>
        <td>{c.hypothesis ? <span className={s.hid}>{c.hypothesis}</span> : null} {c.text}{c.asserted_status ? <div className={s.small}>it said: {c.asserted_status}</div> : null}</td>
        <td><span className={`${s.outcome} ${VERDICT_CLASS[c.verdict] ?? ""}`}>{c.verdict}</span>{c.reasons?.length ? <div className={s.small}>{c.reasons.join("; ")}</div> : null}</td>
        <td>{c.verdict !== "rejected" && c.hypothesis && agent.investigation_id ?
          <button className={s.smallBtn} disabled={busy} onClick={() => run(`Adopt ${c.hypothesis} as a proposal`, () => post(`${path}/investigation/adopt`, { investigation_id: agent.investigation_id, claim_index: i }, actor))}>Adopt as proposal</button> : null}</td>
      </tr>)}</tbody></table>}
  </div>;
}

function Knowledge({ k, onOpen }: { k: Any; onOpen: (id: string) => void }) {
  if (!k) return null;
  return <div className={s.knowledge}>
    <h3 className={s.h3}>Earlier engineering knowledge</h3>
    <p className={s.dim}>{k.rule}</p>
    {k.items.length === 0 && <p className={s.note}>No engineer-verified result from an earlier case shares a finding with this one{k.considered_cases ? ` (${k.considered_cases} earlier case(s) share a finding but have nothing verified)` : ""}.</p>}
    {k.items.length > 0 && <table className={s.table}><thead><tr><th>Earlier case</th><th>Verified</th><th>Applies here?</th><th>Why</th><th>Used for</th></tr></thead><tbody>
      {k.items.map((i: Any, n: number) => <tr key={n}><td><button className={s.linkBtn} onClick={() => onOpen(i.case_id)}>{i.case_id}</button>{i.source_synthetic ? <span className={s.synth}>SYNTHETIC</span> : null}</td>
        <td><span className={s.hid}>{i.hypothesis_id}</span> {i.result} by {i.verified_by}<div className={s.small}>{i.basis}{i.settled_by?.length ? ` · settled by ${i.settled_by.join(", ")}` : ""}</div></td>
        <td><span className={`${s.outcome} ${i.applicability === "applies" ? s.ok : i.applicability === "partly applies" ? s.warn : s.muted}`}>{i.applicability}</span></td>
        <td className={s.small}>{i.reasons.join("; ")}</td><td className={s.small}>{i.use}</td></tr>)}</tbody></table>}
    {k.unverified.length > 0 && <details className={s.traceItem}><summary>Unverified hypotheses from earlier cases ({k.unverified.length}): context only, never used</summary>
      <ul className={s.small}>{k.unverified.map((u: Any, n: number) => <li key={n}>{u.case_id} · {u.hypothesis_id} · {u.stage.replaceAll("_", " ")}</li>)}</ul></details>}
  </div>;
}

function Evid({ title, items, onRef }: { title: string; items: Any[]; onRef: (r: Ref) => void }) {
  return <div className={s.evid}><b>{title}</b><ul>{items.map((it: Any, i: number) => (
    <li key={i}><span className={s.src}>{it.basis}</span> {it.claim} <Refs refs={it.evidence} onRef={onRef} /></li>))}</ul></div>;
}

function Next({ sec, qual, setQual }: { sec: Any; qual: string; setQual: (q: string) => void }) {
  const plan = sec.plans[qual] ?? sec.plans.none;
  return <>
    <SectionHead n={4} title={sec.title} lead="Which check separates the explanations still open, what each explanation predicts it will show, and who may do it. Safety is a gate, not a score." />
    <div className={s.tabs}>{[["none", "Requester: engineer, not HV-qualified"], ["hv_qualified", "Requester: HV-qualified engineer"]].map(([k, l]) =>
      <button key={k} className={k === qual ? s.tabOn : s.tab} onClick={() => setQual(k)}>{l}</button>)}</div>
    {plan.safety_holds.map((h: Any) => <div key={h.id} className={s.hold}><strong>Safety first: {h.title}</strong><p>{h.why}</p><p><b>Action:</b> {h.action}</p><p className={s.dim}>{h.type}</p></div>)}
    {plan.telemetry_verdict && <div className={s.callout}><strong>What the recording cannot do.</strong> {plan.telemetry_verdict}</div>}
    <h3 className={s.h3}>Recommended, in order</h3>
    <ol className={s.plan}>{plan.recommended.map((r: Any) => <PlanItem key={r.id} r={r} />)}</ol>
    {plan.handoff.length > 0 && <><h3 className={s.h3}>Needs qualified personnel (hand-off, not for the requester)</h3><ol className={s.plan}>{plan.handoff.map((r: Any) => <PlanItem key={r.id} r={r} handoff />)}</ol></>}
    <p className={s.dim}>Ranking rule: {plan.ranking_rule}.</p>
  </>;
}

function PlanItem({ r, handoff }: { r: Any; handoff?: boolean }) {
  return <li className={handoff ? s.planHandoff : s.planItem}>
    <div className={s.planHead}><b>{r.title}</b><span className={s.chips}>
      <span className={s.chip}>{r.kind}</span><span className={s.chip}>effort {r.effort}</span>
      <span className={r.safety.hv_exposure ? s.chipHv : s.chip}>{r.safety.hv_exposure ? "HV exposure" : "no HV"} · {r.safety.qualification.replace("_", " ")}</span></span></div>
    <p className={s.dim}>{r.who}{r.distinguishes_labels?.length ? ` · separates ${r.distinguishes_labels.join("; ")}` : r.enables?.length ? ` · enables ${r.enables.join("; ")}` : ""}</p>
    {handoff && <p className={s.handoffNote}>{r.handoff}</p>}
    {r.prior?.length > 0 && <p className={s.prior}>Ranked first: it settled the same finding in {r.prior.map((p: Any) => `${p.case_id} (${p.hypothesis_id} ${p.result} by ${p.verified_by}, ${p.applicability})`).join("; ")}. That result is not assumed here.</p>}
    {Object.keys(r.predictions).length > 0 && <details><summary>What each explanation predicts</summary>
      <table className={s.table}><tbody>{Object.entries(r.predictions).map(([h, p]) => <tr key={h}><td className={s.hid}>{h}</td><td>{String(p)}</td>
        <td className={s.dim}>{r.confirms?.[h] ? `confirms if: ${r.confirms[h]}` : ""}{r.weakens?.[h] ? ` · weakens if: ${r.weakens[h]}` : ""}</td></tr>)}</tbody></table>
      <p className={s.dim}>Requires: {r.requires.join("; ")}{r.preconditions.length ? ` · Preconditions: ${r.preconditions.join("; ")}` : ""}</p></details>}
  </li>;
}

type ScenarioProps = { sec: Any; caseId: string; actor: string; setActor: (v: string) => void; reload: () => Promise<void>; hypotheses: Any[]; plan: Any };

function useAction(actor: string, reload: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (label: string, fn: () => Promise<Any>) => {
    if (!actor.trim()) { setMsg({ ok: false, text: "Enter your name as the engineer first." }); return; }
    setBusy(true); setMsg(null);
    try { await fn(); await reload(); setMsg({ ok: true, text: `${label}: recorded.` }); }
    catch (e) { setMsg({ ok: false, text: `${label} refused: ${e instanceof Error ? e.message : String(e)}` }); }
    finally { setBusy(false); }
  };
  return { busy, msg, run };
}

function Msg({ m }: { m: { ok: boolean; text: string } | null }) {
  return m ? <p className={m.ok ? s.msgOk : s.msgBad} role="status">{m.text}</p> : null;
}

function Scenario({ sec, caseId, actor, setActor, reload, hypotheses, plan }: ScenarioProps) {
  const sc = sec.scenario;
  const { busy, msg, run } = useAction(actor, reload);
  const [title, setTitle] = useState<string>(sc.proposal?.title ?? "");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [scan, setScan] = useState<Any>(null);
  const approved = sc.registered.some((r: Any) => r.status === "approved");
  return <>
    <SectionHead n={5} title={sec.title} lead="Investigation finding → reviewed reusable check → run on other recordings. A scenario runs in the fleet scanner only after a second engineer approves it." />
    <div className={s.actorBar}>
      <label>Engineer <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="your name" maxLength={80} /></label>
      <span className={s.dim}>Recorded with every entry below. A local name, not a login: use this page only on this machine with the authorised team.</span>
    </div>
    {sc.proposal && !sc.registered.length && <div className={s.card}><header><h4>{sc.proposal.title}</h4><span className={`${s.status} ${s.st_open}`}>proposal</span></header>
      <p className={s.dim}>Check {sc.proposal.check_id} ({sc.proposal.check_version}) · from finding {sc.proposal.from_finding} · applies to {sc.proposal.applicability.machine_class} with roles {sc.proposal.applicability.required_roles.join(", ")}</p>
      <div className={s.form}>
        <label>Title <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></label>
        <button disabled={busy || title.trim().length < 3} onClick={() => run("Create scenario", () => post("/engineering/scenarios", { case_id: caseId, title, finding_kind: sc.proposal.from_finding, check_id: sc.proposal.check_id, applicability: sc.proposal.applicability }, actor))}>Create scenario from this finding</button>
      </div></div>}
    {sc.registered.map((r: Any) => <div key={r.id} className={s.card}><header><h4>{r.title}</h4><span className={`${s.status} ${r.status === "approved" ? s.st_confirmed : r.status === "rejected" ? s.st_weakened : s.st_open}`}>{r.status} v{r.version}</span></header>
      <p className={s.dim}>{r.id} · author {r.author}{r.reviews.length ? ` · reviews: ${r.reviews.map((x: Any) => `${x.by} ${x.decision} v${x.version}${x.notes ? ` ("${x.notes}")` : ""}`).join("; ")}` : " · awaiting review by a second engineer"}</p>
      {r.status === "draft" && <div className={s.form}>
        <label>Review notes (what you checked) <textarea rows={2} value={notes[r.id] ?? ""} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} /></label>
        <div className={s.btnRow}>
          <button disabled={busy} onClick={() => run("Approve", () => post(`/engineering/scenarios/${r.id}/review`, { decision: "approve", notes: notes[r.id] ?? "" }, actor))}>Approve</button>
          <button disabled={busy} onClick={() => run("Reject", () => post(`/engineering/scenarios/${r.id}/review`, { decision: "reject", notes: notes[r.id] ?? "" }, actor))}>Reject</button>
          <span className={s.dim}>The reviewer must be a different engineer from the author.</span>
        </div></div>}
    </div>)}
    <h3 className={s.h3}>{approved ? "Runs on other recordings" : "Preview on other recordings"}</h3>
    <table className={s.table}><thead><tr><th>Recording</th><th>Outcome</th><th>Applicability</th><th>Summary</th></tr></thead><tbody>
      {sc.preview_runs.map((r: Any, i: number) => <tr key={i}><td>{r.recording}{r.recording_synthetic ? <span className={s.synth}>SYNTHETIC</span> : null}</td>
        <td><span className={`${s.outcome} ${OUTCOME_CLASS[r.outcome]}`}>{r.outcome}</span></td><td>{r.applicability}{r.reviewed ? "" : " · unreviewed preview"}</td><td className={s.small}>{r.summary}</td></tr>)}
    </tbody></table>
    {approved && <div className={s.form}><div className={s.btnRow}>
      <button disabled={busy} onClick={() => run("Scan", async () => setScan(await post("/engineering/scan", {}, actor)))}>Run approved scenarios on every available recording and store the results</button></div></div>}
    {scan && <><p className={s.dim}>Scan: {scan.scenarios.length} approved scenario(s) × {scan.recordings.length} recording(s) · {Object.entries(scan.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}. Stored in the engineering ledger.</p>
      <table className={s.table}><thead><tr><th>Recording</th><th>Scenario</th><th>Outcome</th><th>Matches the original finding</th><th>Summary</th></tr></thead><tbody>
        {scan.results.map((r: Any, i: number) => <tr key={i}><td>{r.recording}{r.recording_synthetic ? <span className={s.synth}>SYNTHETIC</span> : null}{r.recording === caseId ? <span className={s.synth}>ORIGIN CASE</span> : null}</td>
          <td className={s.small}>{r.scenario_id} v{r.scenario_version}</td><td><span className={`${s.outcome} ${OUTCOME_CLASS[r.outcome]}`}>{r.outcome}</span></td>
          <td>{r.matched_original == null ? "—" : r.matched_original ? "yes" : "no"}</td><td className={s.small}>{r.summary}</td></tr>)}</tbody></table></>}
    <Msg m={msg} />
    <Outcomes caseId={caseId} actor={actor} reload={reload} state={sec.outcomes} hypotheses={hypotheses} plan={plan} />
  </>;
}

const OUTCOME_ACTIONS: Record<string, string> = {
  review: "Review a hypothesis", performed: "Record a performed check", observe: "Record an observation", verify: "Verify a hypothesis",
  correct: "Correct or withdraw a record", close: "Close the case",
};

function Outcomes({ caseId, actor, reload, state, hypotheses, plan }: { caseId: string; actor: string; reload: () => Promise<void>; state: Any; hypotheses: Any[]; plan: Any }) {
  const { busy, msg, run } = useAction(actor, reload);
  const [kind, setKind] = useState("review");
  const [f, setF] = useState<Record<string, Any>>({});
  const set = (k: string, v: Any) => setF({ ...f, [k]: v });
  const hist: Any[] = state?.history ?? [];
  const tracked = Object.keys(state?.hypotheses ?? {});
  const candidates = hypotheses.filter((h: Any) => h.role === "upstream_cause" || h.role === "contributing_factor");
  const performedRecs = hist.filter((r) => r.kind === "performed_check" && r.current);
  const obsRecs = hist.filter((r) => r.kind === "observation" && r.current);
  const templates = [...(plan?.recommended ?? []), ...(plan?.handoff ?? [])];
  const toggle = (k: string, v: string) => { const cur: string[] = f[k] ?? []; set(k, cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]); };
  const out = (k: string, data: Any) => post(`/engineering/cases/${encodeURIComponent(caseId)}/outcomes`, { kind: k, data }, actor);
  const opened = hist.some((r) => r.kind === "case_opened");

  const start = () => run("Open case and track hypotheses", async () => {
    if (!opened) await out("open", {});
    for (const h of candidates) if (!tracked.includes(h.id)) await out("propose", { hypothesis_id: h.id, statement: h.statement, origin: "deterministic" });
  });
  const submit = () => {
    if (kind === "review") return run(OUTCOME_ACTIONS.review, () => out("review", { hypothesis_id: f.hid, decision: f.decision ?? "accept_for_testing", notes: f.notes ?? "" }));
    if (kind === "performed") return run(OUTCOME_ACTIONS.performed, () => out("performed", { template_id: f.template, method: f.method ?? "", performed_by: f.performed_by ?? actor, qualification: f.qualification ?? "none" }));
    if (kind === "observe") {
      const measurements: Record<string, string> = {};
      for (const line of String(f.measurements ?? "").split("\n")) { const i = line.indexOf("="); if (i > 0) measurements[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
      return run(OUTCOME_ACTIONS.observe, () => out("observe", { performed_id: f.performed_id, summary: f.summary ?? "", measurements, supports: f.supports ?? [], contradicts: f.contradicts ?? [] }));
    }
    if (kind === "verify") return run(OUTCOME_ACTIONS.verify, () => out("verify", { hypothesis_id: f.hid, result: f.result ?? "not_settled", observation_ids: f.obs ?? [], basis: f.basis ?? "" }));
    if (kind === "correct") {
      const old = hist.find((r) => r.id === f.record);
      let replacement: Any = null;
      if (f.replace && old) { try { replacement = JSON.parse(f.replacement ?? ""); } catch { return run(OUTCOME_ACTIONS.correct, async () => { throw new Error("the replacement is not valid JSON"); }); } }
      return run(OUTCOME_ACTIONS.correct, () => out("correct", { record_id: f.record, reason: f.reason ?? "", replacement }));
    }
    return run(OUTCOME_ACTIONS.close, () => out("close", { summary: f.summary ?? "" }));
  };
  const hidSelect = <label>Hypothesis <select value={f.hid ?? ""} onChange={(e) => set("hid", e.target.value)}><option value="">choose…</option>
    {tracked.map((h) => <option key={h} value={h}>{h}{hypotheses.find((x: Any) => x.id === h) ? ` · ${hypotheses.find((x: Any) => x.id === h).label}` : ""}</option>)}</select></label>;
  const hidChecks = (k: string, label: string) => <fieldset className={s.checks}><legend>{label}</legend>{tracked.map((h) =>
    <label key={h}><input type="checkbox" checked={(f[k] ?? []).includes(h)} onChange={() => toggle(k, h)} /> {h}</label>)}</fieldset>;

  return <>
    <h3 className={s.h3}>Verified outcome</h3>
    {state && Object.keys(state.hypotheses).length ? <table className={s.table}><thead><tr><th>Hypothesis</th><th>Stage</th><th>Observations</th><th>Verification</th></tr></thead><tbody>
      {Object.values(state.hypotheses).map((h: Any) => <tr key={h.hypothesis_id}><td className={s.hid}>{h.hypothesis_id}</td><td>{h.stage.replaceAll("_", " ")}{h.conflict ? <span className={s.small}> · conflicting evidence: {h.conflict}</span> : null}</td>
        <td className={s.small}>{h.observations.map((o: Any) => `${o.id} ${o.bears}`).join("; ") || "—"}</td>
        <td className={s.small}>{h.verification ? `${h.verification.result} by ${h.verification.by}: ${h.verification.basis}` : "—"}</td></tr>)}</tbody></table>
      : <p className={s.note}>No outcome recorded yet. The case stays <b>unresolved</b> until an engineer records a performed check, its observation and a verification.</p>}
    {state && <p className={s.dim}>Case stage: <b>{String(state.case_stage).replaceAll("_", " ")}</b>{state.closure ? ` · closed: ${state.closure.summary}` : ""}</p>}
    <div className={s.workbench}>
      <p className={s.dim}>Engineer entries. Append-only: a correction supersedes a record and keeps it. Only an engineer can review or verify; confirming or excluding needs an observation, and is blocked while observations conflict.</p>
      {(!opened || candidates.some((h: Any) => !tracked.includes(h.id))) &&
        <div className={s.btnRow}><button disabled={busy} onClick={start}>{opened ? "Track the remaining candidate causes" : "Open this case in the outcome ledger and track its candidate causes"}</button>
          <span className={s.dim}>Records {candidates.map((h: Any) => h.id).join(", ")} as proposed diagnoses (origin: deterministic assessment).</span></div>}
      {opened && <div className={s.form}>
        <label>Entry <select value={kind} onChange={(e) => { setKind(e.target.value); setF({}); }}>{Object.entries(OUTCOME_ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        {kind === "review" && <>{hidSelect}<label>Decision <select value={f.decision ?? "accept_for_testing"} onChange={(e) => set("decision", e.target.value)}>
          <option value="accept_for_testing">accept for testing</option><option value="needs_more_evidence">needs more evidence</option><option value="reject">reject</option></select></label>
          <label>Notes <textarea rows={2} value={f.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></label></>}
        {kind === "performed" && <><label>Check <select value={f.template ?? ""} onChange={(e) => set("template", e.target.value)}><option value="">choose…</option>
          {templates.map((t: Any) => <option key={t.id} value={t.id}>{t.id} · {t.title}</option>)}</select></label>
          <label>Method (what was done, with what instrument) <textarea rows={2} value={f.method ?? ""} onChange={(e) => set("method", e.target.value)} /></label>
          <label>Performed by <input value={f.performed_by ?? ""} placeholder={actor} onChange={(e) => set("performed_by", e.target.value)} /></label>
          <label>Their qualification <select value={f.qualification ?? "none"} onChange={(e) => set("qualification", e.target.value)}>
            <option value="none">none</option><option value="trained_technician">trained technician</option><option value="hv_qualified">HV-qualified</option></select></label></>}
        {kind === "observe" && <><label>From performed check <select value={f.performed_id ?? ""} onChange={(e) => set("performed_id", e.target.value)}><option value="">choose…</option>
          {performedRecs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.payload.template_id} · {r.by}</option>)}</select></label>
          <label>What was observed <textarea rows={2} value={f.summary ?? ""} onChange={(e) => set("summary", e.target.value)} /></label>
          <label>Measurements (one per line, name = value with unit) <textarea rows={3} value={f.measurements ?? ""} onChange={(e) => set("measurements", e.target.value)} /></label>
          {hidChecks("supports", "Supports")}{hidChecks("contradicts", "Contradicts")}</>}
        {kind === "verify" && <>{hidSelect}<label>Result <select value={f.result ?? "not_settled"} onChange={(e) => set("result", e.target.value)}>
          <option value="not_settled">not settled</option><option value="confirmed">confirmed</option><option value="excluded">excluded</option></select></label>
          <fieldset className={s.checks}><legend>Observations relied on</legend>{obsRecs.map((r) => <label key={r.id}><input type="checkbox" checked={(f.obs ?? []).includes(r.id)} onChange={() => toggle("obs", r.id)} /> {r.id}: {r.payload.summary}</label>)}</fieldset>
          <label>Basis <textarea rows={2} value={f.basis ?? ""} onChange={(e) => set("basis", e.target.value)} /></label></>}
        {kind === "correct" && <><label>Record <select value={f.record ?? ""} onChange={(e) => { const r = hist.find((x) => x.id === e.target.value); setF({ ...f, record: e.target.value, replacement: r ? JSON.stringify(Object.fromEntries(Object.entries(r.payload).filter(([k]) => k !== "correction_reason")), null, 1) : "" }); }}>
          <option value="">choose…</option>{hist.filter((r) => r.current && r.kind !== "case_opened").map((r) => <option key={r.id} value={r.id}>{r.id} · {r.kind} · {r.by}</option>)}</select></label>
          <label>Reason <textarea rows={2} value={f.reason ?? ""} onChange={(e) => set("reason", e.target.value)} /></label>
          <label className={s.inline}><input type="checkbox" checked={!!f.replace} onChange={(e) => set("replace", e.target.checked)} /> Replace it with a corrected record (otherwise it is withdrawn)</label>
          {f.replace && <label>Corrected record <textarea rows={6} className={s.mono} value={f.replacement ?? ""} onChange={(e) => set("replacement", e.target.value)} /></label>}</>}
        {kind === "close" && <label>Summary (closing without a confirmed cause leaves the case unresolved) <textarea rows={2} value={f.summary ?? ""} onChange={(e) => set("summary", e.target.value)} /></label>}
        <div className={s.btnRow}><button disabled={busy} onClick={submit}>Record</button></div>
      </div>}
      <Msg m={msg} />
      {hist.length > 0 && <details className={s.traceItem}><summary>Ledger history for this case ({hist.length} records)</summary>
        <table className={s.table}><thead><tr><th>Record</th><th>Kind</th><th>By</th><th>When</th><th>Status</th></tr></thead><tbody>
          {hist.map((r) => <tr key={r.id}><td className={s.small}>{r.id}</td><td>{r.kind.replaceAll("_", " ")}</td><td>{r.by} <span className={s.dim}>({r.actor_kind})</span></td><td className={s.small}>{r.at}</td>
            <td className={s.small}>{r.current ? "current" : "superseded"}{r.supersedes ? ` · supersedes ${r.supersedes}` : ""}</td></tr>)}</tbody></table></details>}
    </div>
  </>;
}

function History({ h }: { h: Any }) {
  return <>
    <SectionHead n="H" title="Earlier report, reconciled" lead="An earlier analysis of this case is history, not a result of this run. Its numbers are recomputed here from the recorded rows." />
    {h.reports.length === 0 ? <p className={s.note}>No earlier report for this case.</p> : h.reports.map((r: Any) => <div key={r.id} className={s.card}><header><Basis b="historical" /><h4>{r.file}</h4></header><p className={s.dim}>{r.kind} · {r.date}</p>{r.note && <p>{r.note}</p>}</div>)}
    {h.reconciliation.length > 0 && <table className={s.table}><thead><tr><th>Earlier statement</th><th>Earlier</th><th>Recomputed</th><th>Status</th></tr></thead><tbody>
      {h.reconciliation.map((r: Any) => <tr key={r.id}><td>{r.historical}</td><td className={s.num}>{r.historical_value} {r.unit}</td><td className={s.num}>{r.recomputed ?? "—"} {r.recomputed != null ? r.unit : ""}</td>
        <td><span className={`${s.outcome} ${r.status === "agrees" ? s.ok : r.status === "differs" ? s.warn : s.muted}`}>{r.status}</span>{r.status === "differs" ? <span className={s.small}> Δ {r.difference} (tolerance {r.tolerance})</span> : null}</td></tr>)}
    </tbody></table>}
  </>;
}

function Trace({ pack }: { pack: Any }) {
  return <>
    <SectionHead n="T" title="Full trace" lead="Every check result as computed, with versions, parameters, input digests, assumptions and all findings. Deterministic: the same inputs give the same output." />
    <p className={s.note}>Assumptions in force: {pack.trace.assumptions.join("; ") || "none"}.</p>
    {Object.values(pack.trace.checks).map((c: Any) => <details key={c.check_id} className={s.traceItem}><summary><span className={`${s.outcome} ${OUTCOME_CLASS[c.outcome]}`}>{c.outcome}</span> {c.check_id} · {c.check_version} · digest {c.input_digest.slice(0, 12)}…</summary>
      <pre className={s.pre}>{JSON.stringify(c, null, 1)}</pre></details>)}
    <details className={s.traceItem}><summary>Facts</summary><pre className={s.pre}>{JSON.stringify(pack.trace.facts, null, 1)}</pre></details>
    <details className={s.traceItem}><summary>Check specifications</summary><pre className={s.pre}>{JSON.stringify(pack.trace.specs, null, 1)}</pre></details>
    <details className={s.traceItem}><summary>AI investigator status</summary><pre className={s.pre}>{JSON.stringify(pack.agent, null, 1)}</pre></details>
  </>;
}

function EvidenceDrawer({ caseId, r, onClose }: { caseId: string; r: Ref; onClose: () => void }) {
  const [rows, setRows] = useState<Any>(null);
  const role = rows?.role ?? "";
  const load = useCallback(() => {
    // the API resolves the field to its role through the case's signal profile; no vendor names live in the page
    get(`/engineering/cases/${encodeURIComponent(caseId)}/series?window=${encodeURIComponent(r.window)}&field=${encodeURIComponent(r.field)}&t0=${r.t - 3}&t1=${r.t + 3}&limit=80`)
      .then(setRows).catch((e) => setRows({ samples: [], note: String(e) }));
  }, [caseId, r]);
  useEffect(load, [load]);
  const rowsSorted = useMemo(() => rows?.samples ?? [], [rows]);
  return <div className={s.drawerBack} onClick={onClose}><aside className={s.drawer} onClick={(e) => e.stopPropagation()}>
    <header><b>Rows around {fmtT(r.t)}</b><button onClick={onClose} aria-label="Close">×</button></header>
    <p className={s.dim}>{r.recording} · {r.window} · {r.file} · {r.source} · {r.field}{role ? ` (role ${role})` : ""}</p>
    {rows?.note && <p className={s.dim}>{rows.note}</p>}
    <table className={s.table}><thead><tr><th>Row</th><th>t</th><th>Value</th></tr></thead><tbody>
      {rowsSorted.map((x: Any) => <tr key={x.row + "-" + x.t} className={x.row === r.row ? s.rowHit : ""}><td className={s.num}>{x.row}</td><td className={s.num}>{x.t}</td><td className={s.num}>{x.value}</td></tr>)}
    </tbody></table>
  </aside></div>;
}

function ViewTabs({ view, setView }: { view: string; setView: (v: "case" | "recordings") => void }) {
  return <nav className={s.viewTabs} aria-label="Engineering views">
    <button className={view === "case" ? s.tabOn : s.tab} onClick={() => setView("case")}>Investigation</button>
    <button className={view === "recordings" ? s.tabOn : s.tab} onClick={() => setView("recordings")}>Recordings &amp; automatic scans</button>
  </nav>;
}

const SCAN_STATE: Record<string, string> = {
  scanned: "Scanned", no_approved_scenarios: "No approved scenario yet", no_compatible_profile: "No signal profile can read it", failed: "Scan failed",
};

function Recordings({ actor, setActor, onOpen }: { actor: string; setActor: (v: string) => void; onOpen: (id: string) => void }) {
  const [d, setD] = useState<Any>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => get("/engineering/recordings").then((x) => { setD(x); setErr(""); return x; }).catch((e) => { setErr(String(e)); return null; }), []);
  useEffect(() => { load(); }, [load]);
  const pending = !!d?.recordings?.some((r: Any) => r.investigation?.running);
  useEffect(() => { if (!pending) return; const t = setInterval(load, 2500); return () => clearInterval(t); }, [pending, load]);
  const waitFor = async (rid: string) => {
    for (let i = 0; i < 90; i++) {
      const x = await load();
      const row = x?.recordings?.find((r: Any) => r.id === rid);
      if (row?.auto_scan) return row;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return null;
  };
  const upload = async (file: File) => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${API}/engine/recordings?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file, headers: { "x-veyra-actor": actor || "Engineer (upload)" } });
      const text = await r.text();
      if (!r.ok) { let m = text; try { m = JSON.parse(text).detail ?? text; } catch { /* keep */ } throw new Error(m); }
      const meta = JSON.parse(text);
      setMsg({ ok: true, text: `Stored ${meta.filename} as ${meta.id} (${meta.inspection.format}). Automatic scan: ${meta.engineering_auto_scan}…` });
      const row = await waitFor(meta.id);
      setMsg({ ok: true, text: row ? `${meta.filename}: ${SCAN_STATE[row.auto_scan.state] ?? row.auto_scan.state}.` : `${meta.filename}: stored; the scan has not reported yet.` });
    } catch (e) { setMsg({ ok: false, text: `Upload refused: ${e instanceof Error ? e.message : String(e)}` }); }
    finally { setBusy(false); }
  };
  const rescan = async (rid: string) => {
    setBusy(true); setMsg(null);
    try { await post(`/engineering/recordings/${rid}/scan`, {}, actor || "Engineer"); await load(); setMsg({ ok: true, text: `${rid}: scanned again.` }); }
    catch (e) { setMsg({ ok: false, text: `Scan refused: ${e instanceof Error ? e.message : String(e)}` }); }
    finally { setBusy(false); }
  };
  const investigate = async (rid: string) => {
    setMsg(null);
    try { await post(`/engineering/cases/${rid}/investigate`, {}, actor || "Engineer"); await load(); setMsg({ ok: true, text: `${rid}: investigation started.` }); }
    catch (e) { setMsg({ ok: false, text: `Investigation refused: ${e instanceof Error ? e.message : String(e)}` }); }
  };
  if (err) return <div className={s.errorBox}><h1>Recordings unavailable</h1><p>{err}</p></div>;
  if (!d) return <p className={s.dim}>Loading recordings…</p>;
  return <section className={s.main}>
    <SectionHead n="R" title="Recordings and automatic scans" lead="Recordings uploaded through Veyra's recording ingestion (ROS 1 bag, ROS 2 bag as .zip, MCAP, PX4 ULog). When one arrives, every approved scenario runs on it if a signal profile can read it; approving a scenario runs it on the recordings already stored." />
    <div className={s.actorBar}>
      <label>Engineer <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="your name" maxLength={80} /></label>
      {d.upload ? <label className={s.uploadBtn}>{busy ? "Working…" : "Upload a recording"}<input type="file" accept=".bag,.mcap,.zip,.ulg" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} /></label>
        : <span className={s.dim}>Upload is off on this server (it needs VEYRA_ENABLE_RUNTIME and VEYRA_ALLOW_RECORDINGS); recordings already stored are listed.</span>}
      <span className={s.dim}>Automatic scan on arrival: {d.auto_scan ? "on" : "off"}. Uploads stay on this machine.</span>
    </div>
    <Msg m={msg} />
    {d.recordings.length === 0 ? <p className={s.note}>No recordings stored yet.</p> :
      <table className={s.table}><thead><tr><th>Recording</th><th>Signal profile</th><th>Automatic scan</th><th>Scenario results</th><th>Investigation</th><th></th></tr></thead><tbody>
        {d.recordings.map((r: Any) => <tr key={r.id}>
          <td><b>{r.filename}</b>{r.profile?.synthetic ? <span className={s.synth}>SYNTHETIC</span> : null}<div className={s.small}>{r.id} · {r.format} · {r.uploaded_at ? new Date(r.uploaded_at * 1000).toLocaleString() : ""}</div></td>
          <td className={s.small}>{r.profile ? <>{r.profile.title}<div>machine class: {r.profile.machine_class ?? "not stated"}</div></> :
            <details><summary>none can read it</summary><ul>{r.profiles.map((p: Any) => <li key={p.profile}>{p.profile}: {p.status}{p.reasons.length ? ` (${p.reasons.join("; ")})` : ""}</li>)}</ul></details>}</td>
          <td className={s.small}>{r.auto_scan ? <>{SCAN_STATE[r.auto_scan.state] ?? r.auto_scan.state}{r.auto_scan.error ? `: ${r.auto_scan.error}` : ""}<div>{new Date(r.auto_scan.at).toLocaleString()}</div></> : d.auto_scan ? "pending…" : "not run (automatic scan off)"}</td>
          <td>{r.results.length === 0 ? <span className={s.small}>—</span> : r.results.map((x: Any) => <div key={x.scenario_id} className={s.small}>
            <span className={`${s.outcome} ${OUTCOME_CLASS[x.outcome]}`}>{x.outcome}</span> {x.scenario_title} v{x.scenario_version} <span className={s.dim}>({x.trigger === "ingest" ? "on arrival" : x.trigger === "approval-backfill" ? "when approved" : "manual scan"})</span>
            {x.applicability !== "applies" ? <div className={s.dim}>{x.summary}</div> : null}</div>)}</td>
          <td className={s.small}>{!r.profile ? "—" : r.investigation?.running ? "Investigating…" : !r.investigation?.mode ? "Not investigated yet" : <>
            <b>{AGENT_MODE[r.investigation.mode] ?? r.investigation.mode}</b>{r.investigation.model ? ` · ${r.investigation.model}` : ""}
            {r.investigation.mode === "agent" && <div>{r.investigation.steps} tool calls · {Object.entries(r.investigation.verdicts).filter(([, n]) => n).map(([v, n]) => `${n} ${v}`).join(", ")}</div>}
            {r.investigation.next_check && <div>next check: {r.investigation.next_check.id}{r.investigation.next_check.prior ? " (earlier verified case)" : ""}</div>}
            {r.investigation.knowledge_used > 0 && <div>{r.investigation.knowledge_used} earlier verified result(s) considered</div>}</>}</td>
          <td><div className={s.btnCol}>{r.profile && <button onClick={() => onOpen(r.id)}>Open investigation</button>}
            {r.profile && <button disabled={busy} onClick={() => rescan(r.id)}>Scan again</button>}
            {r.profile && <button disabled={busy || r.investigation?.running} onClick={() => investigate(r.id)}>{r.investigation?.mode ? "Investigate again" : "Investigate"}</button>}</div></td>
        </tr>)}</tbody></table>}
    <details className={s.traceItem}><summary>Signal profiles ({d.profiles.length})</summary>
      <table className={s.table}><thead><tr><th>Profile</th><th>Origin</th><th>Formats</th><th>Machine class</th></tr></thead><tbody>
        {d.profiles.map((p: Any) => <tr key={p.id}><td>{p.title}{p.synthetic ? <span className={s.synth}>SYNTHETIC</span> : null}<div className={s.small}>{p.id}</div></td><td>{p.origin}</td><td className={s.small}>{p.formats.join(", ")}</td><td className={s.small}>{p.machine_class ?? "not stated"}</td></tr>)}</tbody></table>
      <p className={s.dim}>A customer's own topics, machine class and conditions go in a private profile (private root/profiles/*.json), tried before the built-in ones. Two candidate topics are never guessed between.</p></details>
  </section>;
}
