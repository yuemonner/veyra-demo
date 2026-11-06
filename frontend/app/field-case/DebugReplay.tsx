"use client";
/* How engineers debugged the case, replayed against the data (app/agent/replay.py, deterministic). Each step the
   engineers took is asked two questions: could the data recorded before it have settled it, and does the data after it
   confirm what they concluded. Steps that were not the issue and that the data could have answered, or that a cheaper
   check could have preceded, are counted in time and money. Engineers add a walkthrough by pasting a chat or service
   note (drafted into steps they correct) or by entering the steps. */
import React, { useEffect, useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // replay output is versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const BEFORE: Record<string, [string, string]> = {
  not_separable: ["The data saw it but could not tell the candidates apart", "数据已看到异常，但分不清是哪一种"],
  answerable: ["Answerable from signals already in the log", "日志里已有能回答它的信号"],
  weakened: ["The data already argued against it", "数据已经削弱了这个可能"],
  no_anomaly: ["The data showed this branch normal", "数据显示这条支路正常"],
  physical_only: ["No signal shows it: needs a person at the vehicle", "没有信号能看到：需要人到车上"],
  later_data: ["Only data recorded later shows it", "只有之后记录的数据能看到"],
  not_in_data: ["Nothing in this data answers it", "这份数据回答不了"],
};
const AFTER: Record<string, [string, string]> = {
  unchanged: ["After it, the relation that did not add up is unchanged", "之后，对不上的物理关系没有变化"],
  resolved: ["After it, the relation adds up again", "之后，物理关系恢复正常"],
};
const HINDSIGHT: Record<string, [string, string]> = {
  avoidable: ["Could have been answered from the data", "本可以直接从数据回答"],
  cheaper_first: ["A cheaper discriminating check could have come first", "本可以先做更便宜、能区分的检查"],
  needed: ["Needed", "必要的一步"],
  not_decidable: ["", ""],
};
const RESULT: Record<string, [string, string]> = { fault_found: ["Fault found", "找到故障"], not_the_issue: ["Not the issue", "不是问题所在"], inconclusive: ["Inconclusive", "无结论"], partly: ["Partly", "部分相关"] };
const KIND: Record<string, [string, string]> = { check: ["Check", "检查"], swap: ["Swap", "更换"], repair: ["Repair", "维修"] };
const MEAS_ZH: Record<string, string> = {
  segment_r: "高压隔离后、拔插之前，四线法测该支路每一段电阻，并与正常轮对比", branch_i: "起步时用直流电流钳测该支路和正常轮的输入电流",
  segment_dv_loaded: "起步时测该支路每一段的压降，并与正常轮对比", meter_v: "用标准电压表核对逆变器上报的 DC_V", t_inv_trend: "调取一整班日志比较逆变器温度",
};
const blankStep = () => ({ check: "", kind: "check", result: "not_the_issue", when: "", minutes: "", cost: "" });

export function DebugReplay({ caseId, call }: { caseId: string; call: Call }) {
  const zh = useZh();
  const T = (en: string, cn: string) => (zh ? cn : en);
  const L = (m: Record<string, [string, string]>, k: string) => (m[k] ? m[k][zh ? 1 : 0] : k);
  const [d, setD] = useState<Any>(null);
  const [edit, setEdit] = useState<Any>(null);
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { let live = true; call(`/engine/cases/${caseId}/walkthrough`).then((x) => live && setD(x)).catch(() => live && setD(null)); return () => { live = false; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return null;
  const w = d.walkthrough, r = d.replay;

  async function draft() {
    setBusy(true); setErr(null);
    try {
      const p = await call(`/engine/walkthrough/parse`, { method: "POST", body: JSON.stringify({ text: paste }) });
      setEdit({ ...edit, steps: (p.steps || []).map((s: Any) => ({ ...blankStep(), ...s, when: s.when || "", minutes: s.minutes ?? "", cost: s.cost ?? "" })) });
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function save() {
    setBusy(true); setErr(null);
    try {
      const steps = edit.steps.filter((s: Any) => s.check.trim()).map((s: Any) => ({ check: s.check.trim(), kind: s.kind, result: s.result,
        ...(s.when ? { when: s.when } : {}), ...(s.minutes !== "" ? { minutes: Number(s.minutes) } : {}), ...(s.cost !== "" ? { cost: Number(s.cost) } : {}) }));
      const body = { steps, architecture: edit.architecture, source: edit.source, ...(edit.system ? { system: edit.system } : {}),
        ...(edit.final_cause ? { final_cause: edit.final_cause } : {}), ...(edit.currency ? { currency: edit.currency } : {}) };
      setD(await call(`/engine/cases/${caseId}/walkthrough`, { method: "POST", body: JSON.stringify(body) }));
      setEdit(null); setPaste("");
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  const set = (i: number, k: string, v: Any) => setEdit({ ...edit, steps: edit.steps.map((s: Any, j: number) => (j === i ? { ...s, [k]: v } : s)) });
  const start = () => setEdit({ system: w?.system || "", architecture: w?.architecture || "unknown", source: "interview", final_cause: "", currency: w?.currency || "",
    steps: [blankStep(), blankStep()] });

  const m = r?.summary;
  const cur = w?.currency ? ` ${w.currency}` : "";
  return (
    <div className={st.block} data-no-zh>
      <div className={st.spaceHead}>
        <h2 className={st.h2}>{T("How engineers debugged it", "工程师是怎么排查的")}</h2>
        {!edit && <button className={st.inline} onClick={start}>{w ? T("Record another walkthrough", "记录新的排查过程") : T("Add the engineers' walkthrough", "添加工程师的排查过程")}</button>}
      </div>
      <p className={st.fine}>{T("Each step the engineers took, replayed against the data: could the data recorded before it have settled it, and does the data after it confirm the result? Computed by code, not by the AI.",
        "把工程师做过的每一步放回数据里重放：之前记录的数据能不能已经回答它？之后的数据能不能证实结论？由程序计算，不是 AI。")}</p>
      {w && w.source === "example" && <p className={st.fine}>{T("Example walkthrough written for this synthetic case, not a real service history.", "这是为该合成案例编写的示例排查过程，不是真实维修记录。")}</p>}

      {m && (
        <div className={st.metrics}>
          <div><b>{m.steps}</b><span>{T("Steps", "步骤")}</span></div>
          <div><b>{m.not_the_issue}</b><span>{T("Were not the issue", "不是问题所在")}</span></div>
          <div><b>{m.avoidable + m.cheaper_first}</b><span>{T("Of those, the data could have saved or redirected", "其中数据本可省掉或改道")}</span></div>
          {m.minutes_avoidable > 0 && <div><b>{`${Math.round(m.minutes_avoidable / 6) / 10} h`}</b><span>{T("Time on those steps", "这些步骤花的时间")}</span></div>}
          {m.cost_avoidable > 0 && <div><b>{`${m.cost_avoidable.toLocaleString()}${cur}`}</b><span>{T("Parts on those steps", "这些步骤的零件费用")}</span></div>}
        </div>)}

      {r && (
        <ol className={st.replaySteps}>
          {r.steps.map((s: Any, i: number) => {
            const ws = w.steps[i] || {};
            return (
              <li key={s.id} className={s.hindsight === "avoidable" || s.hindsight === "cheaper_first" ? st.replayHot : ""}>
                <div className={st.replayHead}>
                  <span className={st.fine}>{[ws.when, L(KIND, s.kind), ws.minutes ? `${ws.minutes} min` : null, ws.cost ? `${ws.cost}${cur}` : null].filter(Boolean).join(" · ")}</span>
                  <b>{s.check}</b>
                  <span className={`${st.resultChip} ${st["res_" + s.result] || ""}`}>{L(RESULT, s.result)}</span>
                </div>
                <dl className={st.replayDl}>
                  <dt>{T("Before", "之前")}</dt><dd>{zh ? L(BEFORE, s.before.verdict) : s.before.text}</dd>
                  {s.after && <><dt>{T("After", "之后")}</dt><dd>{zh ? `${L(AFTER, s.after.verdict)}（${s.after.values?.before_ratio}× → ${s.after.values?.after_ratio}×）` : s.after.text}</dd></>}
                  {s.cheaper && <><dt>{T("Cheaper first", "更便宜的先做")}</dt><dd>{zh ? `${MEAS_ZH[s.cheaper.measurement] || s.cheaper.what}（停在场站即可）` : `${s.cheaper.what} (${s.cheaper.cost_label})`}</dd></>}
                  {s.hindsight !== "not_decidable" && <><dt>{T("In hindsight", "回头看")}</dt><dd>{L(HINDSIGHT, s.hindsight)}</dd></>}
                  {s.evidence.length > 0 && <><dt>{T("Lines", "日志行")}</dt><dd className={st.replayLines}>{s.evidence.slice(0, 2).map((e: Any) => <code key={e.line}>{`${e.line}  ${e.quote}`}</code>)}</dd></>}
                </dl>
              </li>);
          })}
        </ol>)}
      {m && m.signal_additions.length > 0 && (
        <div className={st.dataReq}>
          <span className={st.blindHead}>{T("Logging this would let the data settle more of these steps", "记录下面这些数据，能让数据回答更多步骤")}</span>
          <ul>{m.signal_additions.map((a: Any) => <li key={a.what}><b>{zh && a.what.startsWith("per-branch") ? "在 CAN 上记录每条支路的直流电流（能区分支路电阻和取电过多）" : a.what}</b><span>{T(`Steps ${a.steps.join(", ")}`, `涉及步骤 ${a.steps.join("、")}`)}</span></li>)}</ul>
        </div>)}
      {w?.final_cause && <p className={st.fine}>{T(`Engineers' conclusion: ${w.final_cause}`, `工程师结论：${w.final_cause}`)}</p>}

      {edit && (
        <div className={st.reproForm}>
          <label className={st.fieldLabel}>{T("Paste a chat thread or service note (optional)", "粘贴聊天记录或维修记录（可选）")}
            <textarea className={st.text} rows={4} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder={T("- 2026-10-21 replaced FR inverter, 4h, €1800, fault came back", "- 2026-10-21 更换 FR 逆变器，4 小时，故障又出现")} /></label>
          <div className={st.row}><button className={st.ghost} disabled={busy || !paste.trim()} onClick={draft}>{T("Draft steps from the text", "从文字生成步骤草稿")}</button>
            <span className={st.fine}>{T("A first guess: check every step before recording it.", "只是初步猜测：记录前请逐条核对。")}</span></div>
          <div className={st.grid}>
            <label>{T("System", "系统")}<input className={st.input} value={edit.system} onChange={(e) => setEdit({ ...edit, system: e.target.value })} placeholder={T("HV traction", "高压驱动")} /></label>
            <label>{T("How the system is built", "系统结构")}<select value={edit.architecture} onChange={(e) => setEdit({ ...edit, architecture: e.target.value })}>
              <option value="modular">{T("In blocks (a failure points to few parts)", "模块化（故障指向少数部件）")}</option>
              <option value="integrated">{T("Highly integrated", "高度集成")}</option><option value="unknown">{T("Not sure", "不确定")}</option></select></label>
            <label>{T("Source", "来源")}<select value={edit.source} onChange={(e) => setEdit({ ...edit, source: e.target.value })}>
              <option value="interview">{T("Interview", "访谈")}</option><option value="chat">{T("Chat thread", "聊天记录")}</option><option value="service_record">{T("Service record", "维修记录")}</option></select></label>
            <label>{T("Currency", "币种")}<input className={st.input} value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })} placeholder="EUR" /></label>
          </div>
          {edit.steps.map((s: Any, i: number) => (
            <div key={i} className={st.stepEdit}>
              <input className={st.input} value={s.check} onChange={(e) => set(i, "check", e.target.value)} placeholder={T(`Step ${i + 1}: what was checked, swapped or repaired`, `第 ${i + 1} 步：检查、更换或维修了什么`)} aria-label={T(`Step ${i + 1}`, `第 ${i + 1} 步`)} />
              <div className={st.row}>
                <select className={st.input} value={s.kind} onChange={(e) => set(i, "kind", e.target.value)} aria-label={T("Kind", "类型")}>{Object.keys(KIND).map((k) => <option key={k} value={k}>{L(KIND, k)}</option>)}</select>
                <select className={st.input} value={s.result} onChange={(e) => set(i, "result", e.target.value)} aria-label={T("Result", "结果")}>{Object.keys(RESULT).map((k) => <option key={k} value={k}>{L(RESULT, k)}</option>)}</select>
                <input className={st.input} type="date" value={s.when} onChange={(e) => set(i, "when", e.target.value)} aria-label={T("Date", "日期")} />
                <input className={st.input} type="number" min={0} value={s.minutes} onChange={(e) => set(i, "minutes", e.target.value)} placeholder={T("min", "分钟")} aria-label={T("Minutes", "分钟")} style={{ width: 90 }} />
                <input className={st.input} type="number" min={0} value={s.cost} onChange={(e) => set(i, "cost", e.target.value)} placeholder={T("parts", "零件费")} aria-label={T("Parts cost", "零件费")} style={{ width: 100 }} />
              </div>
            </div>))}
          <div className={st.row}>
            <button className={st.ghost} onClick={() => setEdit({ ...edit, steps: [...edit.steps, blankStep()] })}>{T("Add a step", "加一步")}</button>
          </div>
          <label className={st.fieldLabel}>{T("What it turned out to be (optional)", "最后查出的原因（可选）")}<input className={st.input} value={edit.final_cause} onChange={(e) => setEdit({ ...edit, final_cause: e.target.value })} /></label>
          <div className={st.row}>
            <button className={st.primary} disabled={busy || !edit.steps.some((s: Any) => s.check.trim())} onClick={save}>{busy ? T("Replaying…", "重放中…") : T("Replay against the data", "放回数据里重放")}</button>
            <button className={st.ghost} onClick={() => setEdit(null)}>{T("Cancel", "取消")}</button>
          </div>
          {err && <p className={st.err}>{err}</p>}
        </div>)}
    </div>
  );
}
