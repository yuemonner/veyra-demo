"use client";
/* How engineers debugged the case, replayed against the data (app/agent/replay.py, deterministic). Each step the
   engineers took is asked two questions: could the data recorded before it have settled it, and does the data after it
   confirm what they concluded. A step that was not the issue is verified avoidable only when a computed relation in data
   recorded within a day before it showed nothing where it looked, and nothing later implicated it; only those count time
   and money. Potentially avoidable is an estimate with no time or money; insufficient evidence says the data cannot judge it. Engineers add a walkthrough by pasting a chat or service
   note (drafted into steps they correct) or by entering the steps. */
import React, { useEffect, useRef, useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // replay output is versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const BEFORE: Record<string, [string, string]> = {
  not_separable: ["The data saw it but could not tell the candidates apart", "数据当时已经看到问题，但分不清是哪个部件"],
  answerable: ["Signals that bear on it were already in the log (nobody has checked what they would have shown)", "日志里当时已经有相关的信号（还要有人去读，没核实过当时会读出什么）"],
  weakened: ["The data already argued against it", "数据当时已经说明，这个可能性不大"],
  no_anomaly: ["The data showed this branch normal", "数据显示这一路是正常的"],
  physical_only: ["No signal shows it: needs a person at the machine or the bench", "数据看不到，只能派人去车上查"],
  later_data: ["Only data recorded later shows it", "只有后来的数据才看得到"],
  not_in_data: ["Nothing in this data answers it", "这份数据里没有能回答它的信息"],
};
const AFTER: Record<string, [string, string]> = {
  unchanged: ["After it, the relation that did not add up is unchanged", "做完以后，出问题的那组数据没有变：说明没碰到真正的原因"],
  resolved: ["After it, the relation adds up again", "做完以后，数据恢复正常：说明修对了"],
};
const HINDSIGHT: Record<string, [string, string]> = {
  verified_avoidable: ["Verified avoidable: data from just before it showed nothing where it looked", "确认可以省掉：做之前一天内的数据已经显示这里没有问题"],
  potentially_avoidable: ["Potentially avoidable (an estimate, not a saving)", "可能可以省掉（只是估计，不算节省）"],
  insufficient_evidence: ["Insufficient evidence to say it was avoidable", "证据不够，不能说这一步可以省掉"],
  needed: ["Needed", "这一步是必要的"],
  not_assessed: ["", ""],
};
const RESULT: Record<string, [string, string]> = { fault_found: ["Fault found", "找到故障"], not_the_issue: ["Not the issue", "不是问题所在"], inconclusive: ["Inconclusive", "没查出结论"], partly: ["Partly", "有一部分关系"] };
const KIND: Record<string, [string, string]> = { check: ["Check", "检查"], swap: ["Swap", "更换"], repair: ["Repair", "维修"] };
const MEAS_ZH: Record<string, string> = {
  segment_r: "断开高压后、先别拔插头：用四线法量这一路每一段的电阻，再和一个正常的轮对比", branch_i: "起步时用直流电流钳，同时量这一路和一个正常轮的电流",
  segment_dv_loaded: "起步时量这一路每一段的压降，再和一个正常的轮对比", meter_v: "用标准电压表量一下，看逆变器报的电压准不准", t_inv_trend: "调一整班的日志，比一比各个逆变器的温度",
};
// plain Chinese for the reasons replay.py gives (English is shown as returned)
function whyZh(t: string): string {
  if (t.startsWith("a later step")) return "后面的步骤发现同一个部件或功能也有关系，它可能是原因之一";
  if (t.startsWith("the data before this step pointed toward it")) return "做之前的数据反而指向它";
  if (t.startsWith("the data before this step could not answer it")) return "做之前的数据回答不了这个问题";
  if (t.startsWith("a cheaper check")) return "当时有一个更便宜、能分清几种可能的检查";
  const m = t.match(/data before it was (\d+) days old|but the data was (\d+) days old/);
  if (m) return `当时的数据已经是 ${m[1] || m[2]} 天前的`;
  if (t.startsWith("the data argued against it")) return "数据说明可能性不大，但只是推断，没有排除";
  if (t.startsWith("the signals that answer it")) return "日志里有能回答它的信号，但没人核实过当时会读出正常";
  if (t.startsWith("a computed relation in data recorded within a day")) return "做之前一天内的数据经计算显示这里没有异常";
  if (t.startsWith("nothing in the data")) return "做之前的数据没有反对它的证据";
  return t;
}
const blankStep = () => ({ check: "", why: "", kind: "check", result: "not_the_issue", when: "", minutes: "", cost: "" });

// Played step by step: each engineer step appears, then what the data said before it, then after it and in hindsight.
const TICK_MS = 1600;      // one part of one step
const START_MS = 900;

export function DebugReplay({ caseId, call, autoplay = true }: { caseId: string; call: Call; autoplay?: boolean }) {
  const zh = useZh();
  const T = (en: string, cn: string) => (zh ? cn : en);
  const L = (m: Record<string, [string, string]>, k: string) => (m[k] ? m[k][zh ? 1 : 0] : k);
  const [d, setD] = useState<Any>(null);
  const [edit, setEdit] = useState<Any>(null);
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(autoplay ? 0 : 9999);
  const [paused, setPaused] = useState(false);
  // The replay starts when it scrolls into view, so it is never over before anyone sees it.
  const rootRef = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => { let live = true; setTick(autoplay ? 0 : 9999); setSeen(false); call(`/engine/cases/${caseId}/walkthrough`).then((x) => live && setD(x)).catch(() => live && setD(null)); return () => { live = false; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  const total = ((d?.replay?.steps || []).length) * 3;
  useEffect(() => {
    if (seen || !rootRef.current) return;
    if (typeof IntersectionObserver === "undefined") { setSeen(true); return; }
    const o = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); o.disconnect(); } }, { threshold: 0.15 });
    o.observe(rootRef.current);
    return () => o.disconnect();
  }, [d, seen]);
  useEffect(() => {
    if (!d?.replay || !seen || paused || tick >= total) return;
    const t = setTimeout(() => setTick(tick + 1), tick === 0 ? START_MS : TICK_MS);
    return () => clearTimeout(t);
  }, [d, tick, paused, total, seen]);
  if (!d) return null;
  const done = tick >= total;
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
        ...(s.why && s.why.trim() ? { why: s.why.trim() } : {}),
        ...(s.when ? { when: s.when } : {}), ...(s.minutes !== "" ? { minutes: Number(s.minutes) } : {}), ...(s.cost !== "" ? { cost: Number(s.cost) } : {}) }));
      const body = { steps, architecture: edit.architecture, source: edit.source, ...(edit.system ? { system: edit.system } : {}),
        ...(edit.final_cause ? { final_cause: edit.final_cause } : {}), ...(edit.currency ? { currency: edit.currency } : {}) };
      setD(await call(`/engine/cases/${caseId}/walkthrough`, { method: "POST", body: JSON.stringify(body) }));
      setPaused(false); setTick(0);
      setEdit(null); setPaste("");
    } catch (e: Any) { setErr(e.message); } finally { setBusy(false); }
  }
  const set = (i: number, k: string, v: Any) => setEdit({ ...edit, steps: edit.steps.map((s: Any, j: number) => (j === i ? { ...s, [k]: v } : s)) });
  const start = () => setEdit({ system: w?.system || "", architecture: w?.architecture || "unknown", source: "interview", final_cause: "", currency: w?.currency || "",
    steps: [blankStep(), blankStep()] });

  const m = r?.summary;
  const cur = w?.currency ? ` ${w.currency}` : "";
  return (
    <div className={st.block} data-no-zh ref={rootRef}>
      <div className={st.spaceHead}>
        <h2 className={st.h2}>{T("Replay of how engineers debugged it", "复盘排查过程")}</h2>
        {!edit && <button className={st.inline} onClick={start}>{w ? T("Record another walkthrough", "记录新的排查过程") : T("Add the engineers' walkthrough", "添加工程师的排查过程")}</button>}
      </div>
      <p className={st.fine}>{T("Not only what happened to the machine, but why engineers took the path they did, and which work the data shows could have been avoided. Each step, replayed against the data: could the data before it have settled it, and does the data after it confirm the result? Computed by code, not by the AI.",
        "不只看机器出了什么事，还复盘工程师为什么这样排查，以及数据能证明哪些工作本可以省掉。把每一步放回数据里看：做之前，数据是不是已经有答案？做之后，数据能不能证明结论？这些由程序计算，不是 AI 猜的。")}</p>
      {w && w.source === "example" && <p className={st.fine}>{T("Example walkthrough written for this synthetic case, not a real service history.", "这是为演示案例写的示例，不是真实的维修记录。")}</p>}
      {w && w.source === "chat" && <p className={st.fine}>{T("From a public discussion thread, as the participants posted it (summarised by the Veyra team): steps in the order posted; dates, time and costs were not recorded.",
        "来自公开讨论帖，按参与者发布的内容整理（Veyra 团队摘要）：步骤按发布顺序；没有记录日期、用时和费用。")}</p>}
      {r && r.steps.length > 0 && (
        <div className={st.row}>
          {!done && <span className={st.fine}>{T(`Replaying step ${Math.min(Math.floor(tick / 3) + 1, r.steps.length)} of ${r.steps.length}…`, `正在复盘第 ${Math.min(Math.floor(tick / 3) + 1, r.steps.length)} 步，共 ${r.steps.length} 步…`)}</span>}
          {!done && <button className={st.inline} onClick={() => setPaused(!paused)}>{paused ? T("Resume", "继续") : T("Pause", "暂停")}</button>}
          {done && <button className={st.inline} onClick={() => { setPaused(false); setTick(0); }}>{T("Replay from the first step", "从第一步重新复盘")}</button>}
        </div>)}

      {m && done && (
        <div className={st.metrics}>
          <div><b>{m.steps}</b><span>{T("Steps", "一共几步")}</span></div>
          <div><b>{m.not_the_issue}</b><span>{T("Were not the issue", "不是问题所在的步骤")}</span></div>
          <div><b>{m.verified_avoidable ?? 0}</b><span>{T("Verified avoidable", "确认可以省掉")}</span></div>
          <div><b>{m.potentially_avoidable ?? 0}</b><span>{T("Potentially avoidable (estimate)", "可能可以省掉（估计）")}</span></div>
          <div><b>{m.insufficient_evidence ?? 0}</b><span>{T("Insufficient evidence", "证据不够，判断不了")}</span></div>
          {m.minutes_verified_avoidable > 0 && <div><b>{`${Math.round(m.minutes_verified_avoidable / 6) / 10} h`}</b><span>{T("Time on verified avoidable steps", "确认可以省掉的步骤花的时间")}</span></div>}
          {m.cost_verified_avoidable > 0 && <div><b>{`${m.cost_verified_avoidable.toLocaleString()}${cur}`}</b><span>{T("Parts on verified avoidable steps", "确认可以省掉的步骤花的零件钱")}</span></div>}
        </div>)}

      {r && (
        <ol className={st.replaySteps}>
          {r.steps.map((s: Any, i: number) => {
            const ws = w.steps[i] || {};
            const phase = tick - i * 3;          // 1: the step, 2: the data before it, 3: after it and in hindsight
            if (phase < 1) return null;
            return (
              <li key={s.id} className={phase >= 3 && (s.hindsight === "verified_avoidable" || s.hindsight === "potentially_avoidable") ? st.replayHot : ""}>
                <div className={st.replayHead}>
                  <span className={st.fine}>{[ws.when, L(KIND, s.kind), ws.minutes ? `${ws.minutes} min` : null, ws.cost ? `${ws.cost}${cur}` : null].filter(Boolean).join(" · ")}</span>
                  <b>{s.check}</b>
                  <span className={`${st.resultChip} ${st["res_" + s.result] || ""}`}>{L(RESULT, s.result)}</span>
                </div>
                <dl className={st.replayDl}>
                  {ws.why && <><dt>{T("Why this step", "为什么走这一步")}</dt><dd>{ws.why}</dd></>}
                  {ws.decided_by && <><dt>{T("What decided it", "靠什么判断的")}</dt><dd>{ws.decided_by}</dd></>}
                  {phase >= 2 && <><dt>{T("Before", "做之前")}</dt><dd>{zh ? L(BEFORE, s.before.verdict) : s.before.text}</dd></>}
                  {phase >= 3 && s.after && <><dt>{T("After", "做之后")}</dt><dd>{zh ? `${L(AFTER, s.after.verdict)}（${Number(s.after.values?.before_ratio).toFixed(2)}× → ${Number(s.after.values?.after_ratio).toFixed(2)}×）` : s.after.text}</dd></>}
                  {phase >= 3 && s.cheaper && <><dt>{T("Cheaper first", "更省的做法")}</dt><dd>{zh ? `${MEAS_ZH[s.cheaper.measurement] || s.cheaper.what}（${["看已有数据就行", "车停在场站就能做", "要装仪器实车起步"][s.cheaper.cost_tier] || ""}）` : `${s.cheaper.what} (${s.cheaper.cost_label})`}</dd></>}
                  {phase >= 3 && s.hindsight && s.hindsight !== "not_assessed" && s.hindsight !== "not_decidable" && <><dt>{T("In hindsight", "回头看")}</dt><dd>{L(HINDSIGHT, s.hindsight)}
                    {!zh && (s.hindsight_why || []).length > 0 && <span className={st.fine}>{` ${s.hindsight_why.join("; ")}.`}</span>}
                    {zh && (s.hindsight_why || []).length > 0 && <span className={st.fine}>{`（${s.hindsight_why.map(whyZh).join("；")}）`}</span>}</dd></>}
                  {phase >= 2 && s.evidence.length > 0 && <><dt>{T("Lines", "相关日志")}</dt><dd className={st.replayLines}>{s.evidence.slice(0, 2).map((e: Any) => <code key={e.line}>{`${e.line}  ${e.quote}`}</code>)}</dd></>}
                </dl>
              </li>);
          })}
        </ol>)}
      {done && m && m.signal_additions.length > 0 && (
        <div className={st.dataReq}>
          <span className={st.blindHead}>{T("Logging this would let the data settle more of these steps", "以后多记录下面这些数据，很多步骤看数据就能定")}</span>
          <ul>{m.signal_additions.map((a: Any) => <li key={a.what}><b>{zh && a.what.startsWith("per-branch") ? "在 CAN 上记录每一路的直流电流（这样就能分清是线路电阻大，还是逆变器用电多）" : a.what}</b><span>{T(`Steps ${a.steps.join(", ")}`, `涉及步骤 ${a.steps.join("、")}`)}</span></li>)}</ul>
        </div>)}
      {done && w?.final_cause && <p className={st.fine}>{T(`Engineers' reported conclusion (not verified: no record with data confirms it): ${w.final_cause}`, `工程师报告的结论（未验证：没有带数据的记录证实它）：${w.final_cause}`)}</p>}

      {edit && (
        <div className={st.reproForm}>
          <label className={st.fieldLabel}>{T("Paste a chat thread or service note (optional)", "粘贴聊天记录或维修记录（可选）")}
            <textarea className={st.text} rows={4} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder={T("- 2026-10-21 replaced FR inverter, 4h, €1800, fault came back", "- 2026-10-21 更换 FR 逆变器，4 小时，故障又出现")} /></label>
          <div className={st.row}><button className={st.ghost} disabled={busy || !paste.trim()} onClick={draft}>{T("Draft steps from the text", "从文字自动整理成步骤")}</button>
            <span className={st.fine}>{T("A first guess: check every step before recording it.", "这是自动猜的，保存前请一条条核对。")}</span></div>
          <div className={st.grid}>
            <label>{T("System", "系统")}<input className={st.input} value={edit.system} onChange={(e) => setEdit({ ...edit, system: e.target.value })} placeholder={T("HV traction", "高压驱动")} /></label>
            <label>{T("How the system is built", "系统结构")}<select value={edit.architecture} onChange={(e) => setEdit({ ...edit, architecture: e.target.value })}>
              <option value="modular">{T("In blocks (a failure points to few parts)", "分模块（出了问题，能指向少数几个部件）")}</option>
              <option value="integrated">{T("Highly integrated", "高度集成（一个现象可能来自很多地方）")}</option><option value="unknown">{T("Not sure", "不确定")}</option></select></label>
            <label>{T("Source", "来源")}<select value={edit.source} onChange={(e) => setEdit({ ...edit, source: e.target.value })}>
              <option value="interview">{T("Interview", "访谈")}</option><option value="chat">{T("Chat thread", "聊天记录")}</option><option value="service_record">{T("Service record", "维修记录")}</option></select></label>
            <label>{T("Currency", "币种")}<input className={st.input} value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })} placeholder="EUR" /></label>
          </div>
          {edit.steps.map((s: Any, i: number) => (
            <div key={i} className={st.stepEdit}>
              <input className={st.input} value={s.check} onChange={(e) => set(i, "check", e.target.value)} placeholder={T(`Step ${i + 1}: what was checked, swapped or repaired`, `第 ${i + 1} 步：检查、更换或维修了什么`)} aria-label={T(`Step ${i + 1}`, `第 ${i + 1} 步`)} />
              <input className={st.input} value={s.why || ""} onChange={(e) => set(i, "why", e.target.value)} placeholder={T("Why this step, and why then (optional)", "为什么走这一步、为什么那时候做（可选）")} aria-label={T("Why", "原因")} />
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
            <button className={st.primary} disabled={busy || !edit.steps.some((s: Any) => s.check.trim())} onClick={save}>{busy ? T("Replaying…", "复盘中…") : T("Replay against the data", "放回数据里复盘")}</button>
            <button className={st.ghost} onClick={() => setEdit(null)}>{T("Cancel", "取消")}</button>
          </div>
          {err && <p className={st.err}>{err}</p>}
        </div>)}
    </div>
  );
}
