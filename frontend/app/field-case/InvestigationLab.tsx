"use client";

import { useEffect, useState } from "react";
import s from "./field-case.module.css";
import l from "./investigation-lab.module.css";

type RecordValue = any;
const API = process.env.NEXT_PUBLIC_API_URL ?? "/local-api";
const title: Record<string, string> = {
  inventory: "Inspect available evidence", discover_windows: "Find candidate windows", read_signal: "Read source samples",
  read_events: "Read recorded events", revise_hypotheses: "Update competing explanations",
  propose_calculation: "Choose the next calculation", run_calculation: "Execute the calculation", finish: "Stop & report",
};

export default function InvestigationLab({ caseId }: { caseId: string }) {
  const [info, setInfo] = useState<RecordValue>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [selected, setSelected] = useState("");
  const base = `${API}/engine/cases/${encodeURIComponent(caseId)}/investigation-lab`;
  useEffect(() => {
    let disposed = false;
    setInfo(null); setError(""); setSelected("");
    const load = async () => {
      try {
        const response = await fetch(base, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) throw new Error(response.status === 404 ? "This experiment is available in the local preview only." : `Cannot load investigation (${response.status}).`);
        const data = await response.json();
        if (!disposed) { setInfo(data); setError(""); }
      } catch (e) { if (!disposed) setError(e instanceof Error ? e.message : "Connection unavailable"); }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => { disposed = true; clearInterval(timer); };
  }, [base]);
  const runs: RecordValue[] = info?.runs || [];
  const run = runs.find((r) => r.id === selected) || runs[0];
  const active = runs.some((r) => r.state === "running");
  const start = async (suffix = "") => {
    setStarting(true); setError("");
    try {
      const response = await fetch(base + suffix, { method: "POST", credentials: "same-origin", headers: { "x-veyra-request": "1" } });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not start investigation");
      setSelected(data.id);
      setInfo((old: RecordValue) => ({ ...old, runs: [data, ...(old?.runs || [])] }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start investigation"); }
    finally { setStarting(false); }
  };
  const download = () => {
    const blob = new Blob([JSON.stringify(run, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `${run.id}.json`; a.click(); URL.revokeObjectURL(url);
  };
  const latestRevision = run?.revisions?.at(-1);
  return <section className={s.panel}>
    <div className={l.header}>
      <div><p className={l.eyebrow}>LOCAL EXPERIMENT · READ ONLY</p><h2>Watch the investigation</h2>
        <p className={s.lede}>Evidence → competing explanations → calculation → revised judgment.</p></div>
      <button className={s.button} disabled={!info || starting || active} onClick={() => start()}>
        {starting ? "Freezing evidence…" : active ? "Agent investigating…" : info?.configured ? "Freeze evidence & run Agent" : "Check Agent readiness"}
      </button>
    </div>
    <p className={s.note}>This path receives imported evidence only. It does not receive the existing engine’s findings, an incident time, a healthy baseline or the evaluator’s answers.</p>
    {error && <p role="alert" className={l.warning}>{error}</p>}
    {info && !info.configured && <div className={l.warning}><b>Agent blocked · no model configured</b><br />
      Tools and evidence freezing are available. No autonomous investigation has run. Configure a provider locally to enable a real run.</div>}
    {info && <button className={s.quiet} disabled={starting || active} onClick={() => start("/tool-validation")}>Validate local tools · no Agent</button>}
    {runs.length > 1 && <label className={l.selector}>Recorded run <select value={run?.id} onChange={(e) => setSelected(e.target.value)}>
      {runs.map((r) => <option key={r.id} value={r.id}>{r.created_at.slice(0, 19).replace("T", " ")} · {r.state}</option>)}
    </select></label>}
    {!run && <div className={l.empty}><b>Start with a source recording.</b><p>Freeze the current case’s imports, then inspect every chosen calculation and its result here.</p>
      <p className={s.note}>Independent evaluation remains separate from the Agent’s own report.</p></div>}
    {run && <>
      <div className={l.metrics}>
        <div><small>RUN STATE</small><b>{run.state.replace(/_/g, " ")}</b></div>
        <div><small>MODEL CALLS</small><b>{run.model_calls}</b></div>
        <div><small>EXECUTED ANALYSES</small><b>{run.completed_analyses}</b></div>
        <div><small>HYPOTHESIS REVISIONS</small><b>{run.revisions.length}</b></div>
      </div>
      {run.mode !== "live_agent" && <p className={l.warning}>Operator-triggered tool validation. Real computations, fixed selection, zero model calls. This record does not demonstrate Agent autonomy.</p>}
      {run.error && <p className={l.warning}>{run.error}</p>}
      {latestRevision && <div className={l.hypotheses}><h3>Current explanations · revision {latestRevision.version}</h3>
        <p className={s.note}>{latestRevision.reason}</p>
        {latestRevision.hypotheses.map((h: RecordValue) => <article key={h.id}>
          <span className={l.status}>{h.status}</span><b>{h.statement}</b>
          <p className={s.note}>Supporting calls: {h.support.join(", ") || "none"} · Opposing calls: {h.against.join(", ") || "none"}</p>
          <p className={s.note}>Still missing: {h.missing}</p>
        </article>)}
      </div>}
      <ol className={l.trace}>
        {run.calls.map((c: RecordValue, i: number) => <li key={c.id} className={c.status === "failed" ? l.failed : ""}>
          <div className={l.step}>{String(i + 1).padStart(2, "0")}</div><div className={l.receipt}>
            <div className={l.header}><b>{title[c.tool] || c.tool}</b><span className={s.note}>{c.status} · {c.duration_ms} ms</span></div>
            {(c.input.why || c.input.reason) && <p>{c.input.why || c.input.reason}</p>}
            {c.tool === "propose_calculation" && <p className={s.note}>Distinguishes {c.input.distinguishes.join(" vs ")} · {c.input.method}</p>}
            {c.status === "failed" && <p role="status">{c.output.error}</p>}
            {c.tool === "run_calculation" && c.status === "completed" && <p className={s.note}>Real calculation completed. Result and exact input provenance below; not a confirmed diagnosis.</p>}
            <details><summary>Inspect inputs, result & provenance · {c.id}</summary><pre>{JSON.stringify({ input: c.input, output: c.output, output_sha256: c.output_sha256, snapshot: c.snapshot_sha256 }, null, 2)}</pre></details>
          </div>
        </li>)}
      </ol>
      {run.report && <div className={l.report}><p className={l.eyebrow}>STOPPED · {run.report.disposition.replace(/_/g, " ")}</p>
        <h3>{run.report.summary}</h3><p>Why stop: {run.report.reason_to_stop}</p>
        <b>Minimum evidence needed next</b><ul>{run.report.minimum_missing_information.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
        <p className={s.note}>{run.mode === "live_agent" ? "Agent-generated report" : "Operator-triggered tool-validation record"} · not independently validated · root cause unconfirmed.</p></div>}
      <details className={l.seal}><summary>Frozen boundaries & reproducibility</summary>
        <p className={s.note}>Evidence snapshot {run.snapshot_sha256}</p><p className={s.note}>Tool contract {run.tool_contract_sha256}</p>
        <p className={s.note}>{run.provider} · {run.model} · {run.version} · integrity {run.integrity ? "intact" : "FAILED"}</p>
        <p className={s.note}>Execution does not equal independent acceptance. Evaluator results are produced separately; autonomy is not claimed by this screen.</p>
      </details>
      <button className={s.quiet} onClick={download}>Download execution record</button>
    </>}
  </section>;
}
