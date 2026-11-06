"use client";
/* Where the case stands now: one versioned state (GET /engine/cases/{id}/state) for the summary, the counts, the status,
   the next action and the latest discussion round, so the parts of the page cannot disagree. Earlier conclusions are
   listed as superseded. Each explanation says what its status rests on (data, an engineer's report, or Veyra's
   inference); none is a verified cause without a verified outcome. The next action carries its hazard class and the
   review it needs. And for each engineer input: did it change the next action, what changed, what remains. */
import React from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any

const STATUS: Record<string, [string, string]> = {
  cause_verified: ["Cause verified by an independent record", "原因已由独立记录验证"],
  cause_probable: ["Probable cause, not verified", "可能的原因，未验证"],
  cause_unknown: ["Cause not established", "原因未确定"],
  no_failure_found: ["No failure found in the data", "数据里没有发现故障"],
};
const BASIS: Record<string, [string, string]> = {
  telemetry_observation: ["rests on the data", "依据：数据"],
  engineer_reported: ["rests on an engineer's report, not on the data", "依据：工程师的报告，数据里看不到"],
  model_inference: ["Veyra's inference", "依据：Veyra 的推断"],
  verified_outcome: ["verified by an independent record", "依据：独立记录验证"],
};
const STANDING: Record<string, [string, string]> = {
  leading_not_verified: ["Leading, not verified", "最可能，未验证"], open: ["Open", "待确认"], weakened: ["Weakened", "被削弱"], ruled_out: ["Ruled out", "已排除"],
  verified_cause: ["Verified cause", "已验证的原因"], mechanism_reproduced: ["Mechanism reproduced with data", "机理已用数据复现"],
};
const HS: Record<string, [string, string]> = { leading: ["leading", "最可能"], open: ["open", "待确认"], weakened: ["weakened", "被削弱"], ruled_out: ["ruled out", "已排除"] };
const hs = (zh: boolean, k: string) => (HS[k] ? HS[k][zh ? 1 : 0] : k);
const SRC = (zh: boolean, s: Any) => s ? (s.source === "discussion" ? (zh ? `讨论第 ${s.n} 轮` : `discussion round ${s.n}`) : (zh ? `排查第 ${s.n} 轮` : `investigation round ${s.n}`)) : "";
const VERDICT: Record<string, [string, string]> = {
  action_changed_with_evidence: ["Changed the next action: the explanations moved", "改变了下一步：可能的原因有变化"],
  action_changed_without_evidence: ["Changed the next action without moving any explanation: the new check tests the engineer's claim", "下一步变了，但没有任何原因的判断变化：新检查是在检验工程师的说法"],
  explanations_moved_action_unchanged: ["Moved the explanations; the next action stayed the same", "原因的判断有变化，但下一步没变"],
  no_change: ["Did not change anything", "没有改变任何判断"],
  not_yet_assessed: ["Not assessed yet", "还没有评估"],
};

export function SafetyNote({ safety, compact = false }: { safety: Any; compact?: boolean }) {
  const zh = useZh();
  if (!safety) return null;
  if (!safety.hazardous) return compact ? null : <p className={st.safeOk}>{zh ? safety.label_zh : safety.label}</p>;
  return (
    <div className={st.safety} role="note">
      <b>{zh ? `有危险：${safety.label_zh}` : `Hazardous: ${safety.label}`}</b>
      <span>{zh ? `运行前必须由以下人员审核：${(safety.review_by_zh || []).join("；")}。Veyra 不会批准有危险的检查。`
        : `Before anyone runs it, it needs review by: ${(safety.review_by || []).join("; ")}. Veyra never approves a hazardous check.`}</span>
      {safety.safer_alternative && <span>{zh ? `更安全的做法：${safety.safer_alternative_zh}` : `Safer first: ${safety.safer_alternative}`}</span>}
    </div>);
}

export default function CaseStateBar({ state, superseded }: { state: Any; superseded?: boolean }) {
  const zh = useZh();
  const T = (en: string, cn: string) => (zh ? cn : en);
  if (!state || !state.current) return null;
  const c = state.current, n = c.counts || {};
  const lead = (c.hypotheses || []).filter((h: Any) => h.status === "leading");
  const open = (c.hypotheses || []).filter((h: Any) => h.status === "open");
  const flags = state.provenance?.flags || [];
  const inputs = state.engineer_inputs || [];
  return (
    <section className={st.stateBar} aria-label={T("Where the case stands now", "案例当前状态")} data-no-zh>
      <div className={st.stateHead}>
        <span className={st.stateTag}>{T("Where the case stands now", "案例当前状态")}</span>
        <span className={st.fine}>{T(`State ${state.state_version?.id} · from ${SRC(false, c.from)} of ${c.from?.of}${c.from?.recorded ? " (recorded)" : ""}`,
          `状态 ${state.state_version?.id} · 来自${SRC(true, c.from)}（共 ${c.from?.of} 轮${c.from?.recorded ? "，录制" : ""}）`)}</span>
      </div>
      <p className={st.stateStatus}><b>{(STATUS[c.status] || STATUS.cause_unknown)[zh ? 1 : 0]}</b>
        {T(` · ${n.possible} of ${n.total} explanations still possible: ${n.leading} leading, ${n.open} open, ${n.weakened} weakened (not excluded), ${n.ruled_out} ruled out`,
          ` · ${n.total} 个可能里还有 ${n.possible} 个：${n.leading} 个最可能，${n.open} 个待确认，${n.weakened} 个被削弱（未排除），${n.ruled_out} 个已排除`)}</p>
      {[...lead, ...open].length > 0 && (
        <ul className={st.stateHyps}>
          {[...lead, ...open].map((h: Any) => (
            <li key={h.id}><b>{h.area}</b><span>{(STANDING[h.standing] || [h.standing, h.standing])[zh ? 1 : 0]}</span>
              <span className={h.status_basis === "engineer_reported" ? st.provReported : st.provOther}>{(BASIS[h.status_basis] || BASIS.model_inference)[zh ? 1 : 0]}</span></li>))}
        </ul>)}
      {c.next_action && (
        <div className={st.stateNext}>
          <span className={st.fine}>{T(`Current next check (${SRC(false, c.from)})`, `当前的下一步（${SRC(true, c.from)}）`)}</span>
          <b>{c.next_action.action}</b>
          <SafetyNote safety={c.next_action.safety} />
        </div>)}
      {superseded && <p className={st.fine}>{T("The investigation summary below is an earlier result; it is kept for the record and superseded by the state above.",
        "下面的排查结论是较早的结果，保留作记录，以上面的当前状态为准。")}</p>}
      {flags.length > 0 && (
        <ul className={st.stateFlags}>{flags.map((f: Any, i: number) => (
          <li key={i}>{T(`Shown as reported, not as an established cause: “${f.text}” (${f.source.replace(/_/g, " ")}). No verified outcome backs it.`,
            `以下是工程师报告的结论，不作为已确定的原因显示：“${f.text}”。没有已验证的结果支持它。`)}</li>))}</ul>)}
      {inputs.length > 0 && (
        <details className={st.fold}>
          <summary>{T(`What each engineer input changed (${inputs.length})`, `工程师每次输入改变了什么（${inputs.length} 次）`)}</summary>
          <ol className={st.impactList}>
            {inputs.map((x: Any, i: number) => (
              <li key={i}>
                <span className={st.fine}>{`${x.kind === "observation" ? T("Observation (engineer reported)", "观察结果（工程师报告）") : T("Claim or question", "判断或问题")}${x.round ? T(` · discussion round ${x.round}`, ` · 讨论第 ${x.round} 轮`) : ""}`}</span>
                <span className={st.impactText}>{x.text}</span>
                <b className={x.genuine_change ? st.impactYes : st.impactNo}>{(VERDICT[x.verdict] || [x.verdict, x.verdict])[zh ? 1 : 0]}</b>
                {x.before && <span className={st.fine}>{T(`Before: ${x.before.next_action || "—"} → After: ${x.after.next_action || "—"}`, `之前：${x.before.next_action || "—"} → 之后：${x.after.next_action || "—"}`)}</span>}
                {(x.moved || []).filter((m: Any) => m.from !== m.to).length > 0 && <span className={st.fine}>{T("Moved: ", "变化：")}{x.moved.filter((m: Any) => m.from !== m.to).map((m: Any) => `${m.area} ${hs(zh, m.from)} → ${hs(zh, m.to)}`).join(zh ? "；" : "; ")}</span>}
                {(x.new || []).length > 0 && <span className={st.fine}>{T("New: ", "新增：")}{x.new.map((m: Any) => m.area).join("; ")}</span>}
                {(x.rests_on_engineer_report || []).length > 0 && <span className={st.provReported}>{T("Rests on the engineer's report: ", "依据工程师的报告：")}{x.rests_on_engineer_report.join("; ")}</span>}
                {(x.remaining || []).length > 0 && <span className={st.fine}>{T("Still competing: ", "仍在竞争的解释：")}{x.remaining.map((m: Any) => m.area).join("; ")}</span>}
              </li>))}
          </ol>
        </details>)}
      {(state.history || []).length > 0 && (
        <details className={st.fold}>
          <summary>{T(`Earlier conclusions (${state.history.length}, superseded)`, `较早的结论（${state.history.length} 条，已被取代）`)}</summary>
          <ol className={st.impactList}>
            {state.history.map((h: Any) => (
              <li key={h.entry}>
                <span className={st.fine}>{`${SRC(zh, h)} · ${(STATUS[h.status] || STATUS.cause_unknown)[zh ? 1 : 0]} · ${T("superseded by", "被取代于")} ${SRC(zh, h.superseded_by)}`}</span>
                {h.leading.length > 0 && <span>{T("Leading then: ", "当时最可能：")}{h.leading.join("; ")}</span>}
                {h.next_action && <span className={st.fine}>{T("Next check then: ", "当时的下一步：")}{h.next_action}</span>}
              </li>))}
          </ol>
        </details>)}
    </section>
  );
}
