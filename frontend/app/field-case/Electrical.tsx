"use client";
/* Electrical relations: where the physics of the recording does not add up, the hidden parameters estimated from it
   (each branch's and the pack's resistance, with uncertainty), and which mechanisms the recorded signals cannot tell
   apart, with the measurement that would. Computed by code from the file (app/physics/electrical.py), not by the AI. */
import React, { useEffect, useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // engine output is versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const MECH: Record<string, [string, string]> = {
  terminal: ["connector or terminal contact resistance", "接插件或端子接触电阻"],
  cable: ["cable, busbar or fuse resistance", "线缆、母排或保险丝电阻"],
  share: ["drawing more than its share of current", "该支路电流高于平均分配"],
  offset: ["DC voltage sensor offset", "DC 电压采样零偏"],
  gain: ["DC voltage sensor gain error", "DC 电压采样增益误差"],
  pack: ["pack internal resistance (cold or aged cells)", "电池包内阻偏大（低温或老化）"],
  limit: ["torque command not held to the BMS limit", "扭矩指令没有被 BMS 限值卡住"],
};
const MEAS: Record<string, [string, string]> = {
  terminal_dv: ["Voltage across the {w} connector (both sides of the contact) under load", "带载时测 {w} 接插件两侧的电压（跨接触点）"],
  branch_i: ["DC current into the {w} inverter during the launch", "起步时测 {w} 逆变器的直流输入电流"],
  meter_v: ["A reference voltmeter at the {w} inverter input, at rest and under load", "静止和带载时，用标准电压表测 {w} 逆变器输入端"],
  cable_r: ["Four-wire resistance of the {w} cable and fuse, pack disconnected", "断开电池包后，四线法测 {w} 线缆和保险丝电阻"],
};
const mo = (x?: number | null) => (x === null || x === undefined ? "—" : (x * 1000).toFixed(1));

export function Electrical({ caseId, call }: { caseId: string; call: Call }) {
  const zh = useZh();
  const [d, setD] = useState<Any>(null);
  useEffect(() => { let live = true; call(`/engine/cases/${caseId}/electrical`).then((x) => live && setD(x)).catch(() => live && setD(null)); return () => { live = false; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d || d.applies === false || !d.snapshots) return null;
  const T = (en: string, cn: string) => (zh ? cn : en);
  const wheels: string[] = Object.keys(d.snapshots[0]?.branches || {});
  const odd = d.wheel;
  const finding = (f: Any) => {
    const v = f.values || {};
    if (f.relation.startsWith("B_")) return T(`${v.wheel} branch: ${v.r_mohm} mΩ against ${v.others_mohm} mΩ for the others, in ${v.snapshots} of ${v.of} launches`,
      `${v.wheel} 支路：${v.r_mohm} mΩ，其他支路 ${v.others_mohm} mΩ；${v.of} 次起步里 ${v.snapshots} 次都是这样`);
    if (f.relation === "P") return T(`Pack: ${v.cold_mohm} mΩ at ${v.cold_c} °C against ${v.warm_mohm} mΩ at ${v.warm_c} °C`, `电池包：${v.cold_c} °C 时 ${v.cold_mohm} mΩ，${v.warm_c} °C 时 ${v.warm_mohm} mΩ`);
    if (f.relation === "L") return T(`Torque command above the BMS limit by up to ${v.over} N.m (line ${v.line})`, `扭矩指令最多超过 BMS 限值 ${v.over} N.m（第 ${v.line} 行）`);
    if (f.relation.startsWith("O_")) return T(`${v.wheel} reads ${v.v} V away from the pack at rest`, `${v.wheel} 静止时与电池包电压相差 ${v.v} V`);
    return f.text;
  };
  const iso = d.isolability || {};
  return (
    <div className={st.block} data-no-zh>
      <h2 className={st.h2}>{T("Electrical relations", "电气关系检查")}</h2>
      <p className={st.fine}>{T("Computed by code from the recorded voltages and currents, not by the AI. A relation that doesn't add up is evidence even when no alarm fired.",
        "由程序根据记录的电压和电流计算，不是 AI 推出来的。即使没有任何报警，物理关系对不上也是证据。")}</p>
      {(d.findings || []).length > 0 && (
        <ul className={st.elecFindings}>{d.findings.map((f: Any, i: number) => <li key={i}>{finding(f)}</li>)}</ul>
      )}
      <div className={st.tableScroll}>
        <table className={st.elecTable}>
          <thead><tr><th>{T("Launch", "起步")}</th><th>{T("Cell temp", "电芯温度")}</th><th>{T("Pack R (mΩ)", "电池包内阻 (mΩ)")}</th>
            {wheels.map((w) => <th key={w} className={w === odd ? st.oddCol : ""}>{`${w} ${T("branch", "支路")} (mΩ)`}</th>)}</tr></thead>
          <tbody>{d.snapshots.map((s: Any, i: number) => (
            <tr key={i}><td>{(String(s.label).match(/\d{4}-\d{2}-\d{2}[^(]*/) || [s.label])[0].replace(/launch on.*$/, "").trim()}</td>
              <td>{s.cell_temp_c ?? "—"}{s.cell_temp_c !== undefined && s.cell_temp_c !== null ? " °C" : ""}</td>
              <td>{s.pack ? `${mo(s.pack.r_ohm)} ± ${mo(s.pack.r_se_ohm)}` : "—"}</td>
              {wheels.map((w) => { const b = s.branches[w] || {}; return <td key={w} className={w === odd ? st.oddCol : ""}>{b.r_ohm !== null && b.r_ohm !== undefined ? `${mo(b.r_ohm)} ± ${mo(b.r_se_ohm)}` : "—"}</td>; })}</tr>))}
          </tbody>
        </table>
      </div>
      {(iso.not_separable || []).map((g: Any, i: number) => (
        <div key={i} className={st.isoBox}>
          <b>{T(`The recorded signals can't tell these apart for ${odd}:`, `现有信号还分不清 ${odd} 是下面哪一种：`)}</b>
          <ul>{(g.ids || []).map((id: string) => <li key={id}>{`${odd} ${MECH[id] ? MECH[id][zh ? 1 : 0] : id}`}</li>)}</ul>
          {(g.measurements || []).length > 0 && <>
            <span className={st.isoHead}>{T("One measurement each separates them:", "下面每一项测量能把其中一种区分出来：")}</span>
            <ul>{g.measurements.map((m: Any) => <li key={m.id}>{(MEAS[m.id] ? MEAS[m.id][zh ? 1 : 0] : m.measure).replace("{w}", odd)}</li>)}</ul>
          </>}
          {iso.torque_share && <p className={st.fine}>{T(`${odd}'s torque under load is ${iso.torque_share.nm} N.m against ${iso.torque_share.others_nm} for the others (${iso.torque_share.diff_pct > 0 ? "+" : ""}${iso.torque_share.diff_pct}%), so a higher current share would have to come from losses, not torque.`,
            `${odd} 带载扭矩 ${iso.torque_share.nm} N.m，其他轮 ${iso.torque_share.others_nm} N.m（${iso.torque_share.diff_pct > 0 ? "+" : ""}${iso.torque_share.diff_pct}%），所以如果它电流更大，只能来自损耗，不是扭矩。`)}</p>}
        </div>))}
      <p className={st.fine}>{T(`Assumes each branch carries Pack_I / ${wheels.length} (no per-wheel current is recorded). Method: least-squares estimates with standard errors; fault-signature isolability.`,
        `假设每条支路电流为 Pack_I / ${wheels.length}（没有记录单轮电流）。方法：最小二乘估计（含标准误差）+ 故障特征矩阵可隔离性分析。`)}</p>
    </div>
  );
}
