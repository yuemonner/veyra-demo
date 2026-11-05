"use client";
/* Electrical relations, computed by code from the file (app/physics/electrical.py v2), not by the AI. Three tiers kept
   apart: what the signals measure, what a model derives from them (and under which assumptions), and what no signal
   shows. For a branch that does not add up: the mechanisms still open, and the measurements that would separate them,
   with the result each one predicts, its cost and its blind spot. A relation the data cannot support says so. */
import React, { useEffect, useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // engine output is versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const pct = (x?: number | null) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`);
const mo = (x?: number | null) => (x === null || x === undefined ? "—" : (x * 1000).toFixed(1));

const CAND: Record<string, [string, string]> = {
  contact: ["connector or terminal contact resistance", "接插件或端子接触电阻"],
  conductor: ["cable, busbar or fuse resistance", "线缆、母排或保险丝电阻"],
  inverter_draw: ["inverter or motor drawing more DC current (resistance normal)", "逆变器或电机多取直流电流（支路电阻正常）"],
  uneven_load: ["carrying more load than the other wheels (healthy)", "该轮负载比其他轮大（正常现象）"],
  sensor_gain: ["DC voltage sensor gain error", "DC 电压传感器增益误差"],
};
const MEAS: Record<string, [string, string, string, string]> = {
  // what (en, zh), blind spot (en, zh)
  t_inv_trend: ["Pull a full shift of logs and compare {w}'s inverter temperature with the others at the same torque", "调取一整班的日志，在相同扭矩下比较 {w} 和其他逆变器的温度",
    "Cooling and 1 °C resolution can hide it: a hotter inverter supports, a similar one only weakens.", "散热和 1 °C 分辨率可能掩盖差异：更热算支持，差不多只能算稍微削弱。"],
  meter_v: ["Reference voltmeter at the {w} inverter DC input, key on, compared with the DC_V it reports on CAN", "上电后用标准电压表测 {w} 逆变器直流输入端，与 CAN 上报的 DC_V 对比",
    "Shows the error at one voltage; a gain error needs two.", "只能看到一个电压点的误差；判断增益误差需要两个电压点。"],
  segment_r: ["With high voltage isolated, and before anyone unplugs or reseats it: four-wire resistance of each {w} segment (connectors, cable, fuse), and the same on one healthy wheel",
    "高压隔离后、在任何人拔插或重新插紧之前：四线法测 {w} 每一段的电阻（两端接插件、线缆、保险丝），并在一个正常的轮上做同样测量",
    "A contact that only opens under load, heat or vibration can read normal at rest: a normal reading weakens a contact fault, it does not exclude it.",
    "只在带载、发热或振动时才变差的接触点，静止时可能测得正常：读数正常只能削弱接触问题，不能排除。"],
  branch_i: ["HV-rated DC clamp on the {w} inverter input and on one healthy wheel during the same ramp launch", "同一次坡道起步中，用高压直流电流钳同时测 {w} 和一个正常轮的逆变器输入电流",
    "Separates current from resistance, not connector from cable.", "能区分电流和电阻，分不清接插件和线缆。"],
  segment_dv_loaded: ["Voltage across each {w} segment during a ramp launch, and the same on one healthy wheel", "坡道起步时测 {w} 每一段的压降，并在一个正常的轮上做同样测量",
    "Live high-voltage measurement on a driven vehicle: insulated probes, an engineer's approval.", "行驶中的带电高压测量：需要绝缘探头和工程师批准。"],
};
const COST: Record<number, [string, string]> = { 0: ["data already logged", "已有数据"], 1: ["parked vehicle at the depot", "车辆停在场站"], 2: ["instrumented driven launch", "装仪器的实车起步"] };

export function Electrical({ caseId, call }: { caseId: string; call: Call }) {
  const zh = useZh();
  const [d, setD] = useState<Any>(null);
  useEffect(() => { let live = true; call(`/engine/cases/${caseId}/electrical`).then((x) => live && setD(x)).catch(() => live && setD(null)); return () => { live = false; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d || d.applies === false || !d.relations) return null;
  const T = (en: string, cn: string) => (zh ? cn : en);
  const rel = d.relations || {};
  const findings: Any[] = d.findings || [];
  const measured = findings.filter((f) => f.tier === "measured");
  const modelF = findings.filter((f) => f.tier === "model");
  const branches = (d.model || {}).branches || {};
  const wheels: string[] = d.wheels || Object.keys(branches);

  const text = (f: Any) => {
    const v = f.values || {};
    if (!zh) return f.text;
    if (f.relation.startsWith("B_")) return `${v.wheel} 的压降随电池包电流每安培增加 ${v.k_mv_per_a} mV，其他逆变器是 ${v.others_mv_per_a} mV（${v.ratio} 倍）；${v.of} 个片段里有 ${v.snapshots} 个如此。`;
    if (f.relation.startsWith("R_")) return `如果 ${v.wheel} 分到平均份额的电流，它的支路电阻是 ${v.r_equal_mohm} mΩ，其他支路 ${v.others_equal_mohm} mΩ；在${v.basis === "torque" ? "记录的扭矩允许的" : "无扭矩数据时假设的"} ${v.band_lo_pct}%–${v.band_hi_pct}% 份额范围内是 ${v.r_lo_mohm}–${v.r_hi_mohm} mΩ。只有它承担约 ${v.s_star_pct}% 的驱动电流时，电阻才会和其他支路一样。`;
    if (f.relation === "P") {
      const c = (v.confounds || []) as string[];
      const names: Record<string, string> = { soc: `SOC 也不同（${v.cold_soc}% 对 ${v.warm_soc}%）`, current: "峰值电流不同", pulse: "起步时长不同", dynamic: "电压响应有延迟（极化）" };
      return `起步过程中电池包的表观内阻：${v.cold_c} °C 时 ${v.cold_mohm} mΩ，${v.warm_c} °C 时 ${v.warm_mohm} mΩ。` + (c.length ? `其他条件也不同：${c.map((x) => names[x] || x).join("；")}。这组数据不能把差异只归因于温度。` : "SOC、电流和起步时长相近，温度是主要差别。");
    }
    if (f.relation.startsWith("O_")) return `静止时 ${v.wheel} 与其他逆变器相差 ${v.v} V（${v.of} 个片段里 ${v.snapshots} 个），是传感器零偏或增益误差。`;
    if (f.relation === "L") return `扭矩指令最多超过 BMS 限值 ${v.over} N.m（第 ${v.line} 行）。`;
    return f.text;
  };
  const reason = (r: Any) => (zh ? ({ not_identifiable: "数据不足以计算", not_applicable: "不适用" } as Any)[r.status] || r.status : r.status.replace("_", " "));

  return (
    <div className={st.block} data-no-zh>
      <h2 className={st.h2}>{T("Electrical relations", "电气关系检查")}</h2>
      <p className={st.fine}>{T("Computed by code from the recorded voltages and currents, not by the AI. A relation that doesn't add up is evidence even when no alarm fired. Nothing here is ruled out, only weakened.",
        "由程序根据记录的电压和电流计算，不是 AI 推出来的。即使没有报警，物理关系对不上也是证据。这里不排除任何可能，只会削弱。")}</p>
      {(["branch", "pack", "sensor", "limit"] as const).filter((k) => rel[k] && rel[k].status !== "ok").map((k) => (
        <p key={k} className={st.fine}>{`${({ branch: T("Branch drop", "支路压降"), pack: T("Pack resistance", "电池包内阻"), sensor: T("Sensors at rest", "静止时的传感器"), limit: T("Command limit", "指令限值") } as Any)[k]}: ${reason(rel[k])}${rel[k].reason && !zh ? ` (${rel[k].reason})` : ""}`}</p>))}

      {measured.length > 0 && <>
        <h3 className={st.elecTier}>{T("Measured", "测量值")}<span>{T("read straight from the signals", "直接来自信号")}</span></h3>
        <ul className={st.elecFindings}>{measured.map((f, i) => <li key={i}>{text(f)}</li>)}</ul>
      </>}

      {Object.keys(branches).length > 0 && (
        <div className={st.tableScroll}>
          <table className={st.elecTable}>
            <thead><tr><th>{T("Wheel", "轮")}</th><th>{T("Drop per amp of pack current (measured)", "每安培电池包电流的压降（测量）")}</th>
              <th>{T("Resistance if current is shared equally (model)", "电流均分时的电阻（模型）")}</th><th>{T("Resistance over plausible shares (model)", "合理份额范围内的电阻（模型）")}</th></tr></thead>
            <tbody>{wheels.filter((w) => branches[w]).map((w) => { const b = branches[w]; const odd = (rel.branch?.flagged || []).includes(w); return (
              <tr key={w} className={odd ? st.oddRow : ""}><td>{w}</td><td>{`${(b.k_v_per_a * 1000).toFixed(2)} ± ${(b.k_halfwidth * 1000).toFixed(2)} mV/A`}</td>
                <td>{`${mo(b.r_equal_share_ohm)} mΩ`}</td><td>{`${mo(b.r_interval_ohm[0])}–${mo(b.r_interval_ohm[1])} mΩ (${pct(b.share_band[0])}–${pct(b.share_band[1])})`}</td></tr>); })}</tbody>
          </table>
        </div>)}

      {modelF.length > 0 && <>
        <h3 className={st.elecTier}>{T("Model-derived", "模型推算")}<span>{T("holds only under the assumptions below", "只在下列假设下成立")}</span></h3>
        {modelF.map((f, i) => (
          <div key={i} className={st.elecModel}>
            <p>{text(f)}</p>
            <ul className={st.elecAssume}>{(zh ? ["没有记录每条支路的电流；电阻 = 每安培压降 ÷ 该支路分到的电流份额。", "其他支路彼此电阻相同。", "电池包电流传感器误差在 ±2% 以内。"] : f.assumptions).map((a: string, j: number) => <li key={j}>{a}</li>)}</ul>
          </div>))}
        {Object.entries((d.model || {}).sensitivity || {}).map(([w, rows]: [string, Any]) => (
          <div key={w} className={st.tableScroll}>
            <table className={st.elecTable}>
              <caption>{T(`How ${w}'s resistance depends on its share of the traction current`, `${w} 的电阻如何随它分到的驱动电流份额变化`)}</caption>
              <thead><tr><th>{T(`${w}'s share`, `${w} 的份额`)}</th><th>{T(`${w} resistance`, `${w} 电阻`)}</th><th>{T("Others' resistance", "其他支路电阻")}</th></tr></thead>
              <tbody>{rows.map((x: Any) => <tr key={x.share} className={x.in_band ? st.inBand : ""}><td>{pct(x.share)}{x.in_band ? T(" · plausible", " · 合理范围") : ""}</td><td>{`${mo(x.r_ohm)} mΩ`}</td><td>{`${mo(x.others_r_ohm)} mΩ`}</td></tr>)}</tbody>
            </table>
          </div>))}
      </>}

      {(d.assessments || []).map((a: Any) => {
        const open = a.candidates.filter((c: Any) => c.status === "consistent");
        const weak = a.candidates.filter((c: Any) => c.status === "weakened");
        const plan = [...a.measurements].sort((x: Any, y: Any) => x.cost - y.cost);
        return (
          <div key={a.wheel} className={st.isoBox}>
            <b>{T(`The recorded signals can't tell these apart for ${a.wheel}:`, `现有信号分不清 ${a.wheel} 是下面哪一种：`)}</b>
            <ul>{open.map((c: Any) => <li key={c.id}>{`${a.wheel} ${CAND[c.id] ? CAND[c.id][zh ? 1 : 0] : c.name}`}{!zh && c.why ? <span className={st.why}>{c.why}</span> : null}</li>)}</ul>
            {weak.length > 0 && <>
              <span className={st.isoHead}>{T("Weakened, not excluded:", "被削弱、但未排除：")}</span>
              <ul>{weak.map((c: Any) => <li key={c.id}>{`${a.wheel} ${CAND[c.id] ? CAND[c.id][zh ? 1 : 0] : c.name}`}{!zh && c.why ? <span className={st.why}>{c.why}</span> : null}</li>)}</ul>
            </>}
            {d.torque_note && <p className={st.fine}>{zh ? `${d.torque_note.wheel} 带载扭矩 ${d.torque_note.nm} N.m，其他轮 ${d.torque_note.others_nm} N.m（${d.torque_note.diff_pct > 0 ? "+" : ""}${d.torque_note.diff_pct}%）。扭矩相近说明机械负载相近，但不能说明直流电流相近，因为直流电流还取决于逆变器和电机的损耗，这些没有记录。` : d.torque_note.text}</p>}
            {a.t_inv && <p className={st.fine}>{zh ? `起步过程中 ${a.wheel} 逆变器比其他逆变器多升温 ${a.t_inv.rise_diff_c} °C（弱证据）。` : a.t_inv.note}</p>}
            {a.load_dependent && <p className={st.fine}>{T("Its loaded drop extrapolates below its at-rest reading, as a contact that opens under load would make it.", "带载压降外推到零电流时低于静止读数，像是一个带载才变差的接触点。")}</p>}
            <span className={st.isoHead}>{T("Measurements that would separate them, cheapest first:", "能把它们区分开的测量（按成本从低到高）：")}</span>
            <ol className={st.measList}>
              {plan.map((m: Any) => {
                const rec = m.id === a.recommended;
                const M = MEAS[m.id];
                return (
                  <li key={m.id} className={rec ? st.measRec : !m.discriminates ? st.measDim : ""}>
                    {rec && <span className={st.recTag}>{T("Recommended", "建议先做")}</span>}
                    <b>{M ? M[zh ? 1 : 0].replace("{w}", a.wheel) : m.what}</b>
                    <span className={st.fine}>{`${COST[m.cost] ? COST[m.cost][zh ? 1 : 0] : m.cost_label}${m.needs_approval ? T(" · needs an engineer's approval", " · 需要工程师批准") : ""} · ${m.strength === "strong" ? T("separates clearly", "区分清楚") : T("weak evidence", "弱证据")}${!m.discriminates ? T(" · does not separate the open candidates", " · 分不开剩下的可能") : ""}`}</span>
                    {m.discriminates && !zh && <ul className={st.expect}>{Object.entries(m.expected || {}).map(([c, e]: [string, Any]) => <li key={c}><i>{`If ${CAND[c] ? CAND[c][0] : c}:`}</i> {e}</li>)}</ul>}
                    <span className={st.blindSpot}>{M ? M[zh ? 3 : 2] : m.blind_spot}</span>
                  </li>);
              })}
            </ol>
            {a.then && <p className={st.fine}>{T(`If ${a.then.if}: ${a.then.why}.`, "如果每一段读数都正常：带载才变差的接触点静止时会显示正常，先在带载时测分段压降，再决定是否削弱接触问题。")}</p>}
          </div>);
      })}

      <h3 className={st.elecTier}>{T("Not observable in this data", "这份数据看不到的")}</h3>
      <ul className={st.elecAssume}>{(zh ? ["每条支路的直流电流", "额外电阻在支路的哪个位置（接插件、线缆、保险丝、逆变器输入端）", "逆变器和电机的效率", "接触点的状态（插合、扭矩、腐蚀）"] : d.identifiability?.unobservable || []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}
