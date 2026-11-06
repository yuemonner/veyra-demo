"use client";
/* How sure the case is, and what it cost. The verification ladder (probable -> reproduced -> verified) is read from the
   case record: Veyra can reach "probable" on its own; the next rungs need records an engineer writes. Below it, the case
   counted: how many checks Veyra ran, how many questions it answered from the data instead of asking someone. And the
   case as one row of a quality-control table (CSV), with the evidence behind each cell. */
import React, { useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";
import { csv, qualityRow } from "../../lib/quality-row";

type Any = any; // case records are versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

export const ROLE: Record<string, string> = { main_cause: "Main cause", trigger: "Trigger", amplifier: "Amplifier", effect: "Effect" };
const ROLE_ZH: Record<string, string> = { main_cause: "主因", trigger: "诱因", amplifier: "放大因素", effect: "结果" };
export function CaseStatus({ ladder, metrics, result, caseId, call, actor, onChanged }:
  { ladder: Any; metrics: Any; result: Any; caseId: string; call: Call; actor: string; onChanged: () => void }) {
  const zh = useZh();
  const [form, setForm] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!ladder) return null;
  const rungs: Any[] = ladder.rungs || [];
  const m = metrics || {};
  function download() {
    const { cols, row } = qualityRow(ladder, result, zh, typeof window !== "undefined" ? window.location.href : "");
    const blob = new Blob([csv(cols, [row])], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `quality-record-${(ladder.case?.machine_id || caseId).replace(/[^\w-]+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  async function save() {
    setBusy(true); setErr(null);
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "reproduction_attempt", actor, source: "Verification ladder",
        payload: { run_id: ladder.run_id, environment: form.environment, platform: ladder.case?.machine_id || "machine", setup: form.setup.trim(),
          result: form.result, observations: form.observations.trim(), performed_at: new Date().toISOString() } }) });
      setForm(null); onChanged();
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  const stats: [string, Any][] = [
    ["Checks Veyra ran", m.checks_run], ["Answered from the data", m.answered_from_data], ["Questions for people", m.questions_for_people],
    ["Data requests", m.data_requests], ["Discussion rounds", m.discussion_rounds], ["Investigation time", m.investigation_seconds ? `${Math.round(m.investigation_seconds)} s` : null]];
  return (
    <div className={st.block}>
      <div className={st.spaceHead}>
        <h2 className={st.h2}>How sure we are</h2>
        <button className={st.inline} onClick={download}>Download quality record (CSV)</button>
      </div>
      <ol className={st.ladder}>
        {rungs.map((r) => (
          <li key={r.level} className={r.reached ? st.rungOn : st.rung}>
            <b>{r.label}</b>
            {r.reached ? <span>{`${r.reached.by} · ${String(r.reached.at || "").slice(0, 10)}`}{r.reached.provenance === "engineer_reported" ? (zh ? " · 工程师报告，未附数据" : " · engineer reported, no data attached") : ""}</span> : <span className={st.needs}>{r.needs}</span>}
          </li>))}
      </ol>
      {ladder.engineer_reported_confirmation && <p className={st.fine}>{zh ? `工程师记录了原因已确认（${ladder.engineer_reported_confirmation.method || ""}）。没有带数据的记录支持，所以不算“已验证”。`
        : `The engineer recorded the cause as confirmed (${ladder.engineer_reported_confirmation.method || "no method"}). No record with data backs it, so it is not counted as verified.`}</p>}
      {ladder.next && ladder.next.level !== "probable" && !form && (
        <div className={st.row}>
          <button className={st.ghost} onClick={() => setForm({ environment: "field", setup: "", result: "reproduced", observations: "" })}>Record a reproduction</button>
          <span className={st.fine}>An engineer&apos;s record moves the case up the ladder; Veyra cannot do it alone.</span>
        </div>)}
      {form && (
        <div className={st.reproForm}>
          <label className={st.fieldLabel}>Where<select className={st.text} value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
            <option value="field">Field</option><option value="bench">Bench</option><option value="simulation">Simulation</option><option value="log_replay">Log replay</option></select></label>
          <label className={st.fieldLabel}>Conditions and setup<input className={st.text} value={form.setup} onChange={(e) => setForm({ ...form, setup: e.target.value })} placeholder="cold morning, SOC 38 %, B ramp, HSA on" /></label>
          <label className={st.fieldLabel}>Result<select className={st.text} value={form.result} onChange={(e) => setForm({ ...form, result: e.target.value })}>
            <option value="reproduced">Reproduced</option><option value="partially_reproduced">Partly reproduced</option><option value="not_reproduced">Not reproduced</option><option value="inconclusive">Inconclusive</option></select></label>
          <label className={st.fieldLabel}>What you saw<input className={st.text} value={form.observations} onChange={(e) => setForm({ ...form, observations: e.target.value })} /></label>
          <div className={st.row}><button className={st.primary} disabled={busy || !form.setup.trim() || !form.observations.trim()} onClick={save}>{busy ? "Saving…" : "Save to the case"}</button>
            <button className={st.ghost} onClick={() => setForm(null)}>Cancel</button></div>
          {err && <p className={st.err}>{err}</p>}
        </div>)}
      <div className={st.metrics} aria-label="Measured on this case">
        {stats.filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => <div key={k}><b>{v}</b><span>{k}</span></div>)}
      </div>
    </div>
  );
}

/** A hypothesis' role, shown next to its area. */
export function RoleTag({ role }: { role?: string }) {
  const zh = useZh();
  if (!role || !ROLE[role]) return null;
  return <span className={`${st.role} ${st["r_" + role] || ""}`} data-no-zh>{zh ? ROLE_ZH[role] : ROLE[role]}</span>;
}
