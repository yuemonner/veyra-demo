"use client";
/* A decoded CAN log, drawn: the failing launch on one time axis (torque, current, voltage) with the events marked,
   and every snapshot side by side, so the healthy launches are the baseline for the one that failed. */
import React, { useMemo, useState } from "react";
import t from "./tools.module.css";
import * as CL from "../../lib/can-log";
import { stackedChart, C } from "../../lib/svg-chart";
import { useZh } from "./lang";
import { DataTable, Tag } from "./CaseTools";

const f1 = (v: number) => (Number.isFinite(v) ? v.toFixed(1) : "—");

export function CanPanel({ lines, onAsk }: { lines: string[]; onAsk?: () => void }) {
  const zh = useZh();
  const log = useMemo(() => CL.parseCanLog(lines), [lines]);
  const all = useMemo(() => log.snapshots.map(CL.facts), [log]);
  const failing = Math.max(0, all.findIndex((f) => f.stopped));
  const [pick, setPick] = useState(failing);
  const s = log.snapshots[pick];
  if (!s) return null;
  const F = all[pick];
  const rel = (pts?: { t: number; v: number }[]) => (pts || []).map((p) => ({ t: p.t - s.t0, v: p.v }));
  const [x0, x1] = CL.focus(s);
  const evColor = (lv: string) => (lv === "ERROR" ? C.fault : lv === "WARN" ? C.amber : C.ink3);
  const evName = (e: CL.CanEvent) => (/DTC/.test(e.text) ? "MCU_FR DTC 0x2A11" : /EMERGENCY/.test(e.text) ? (zh ? "自驾急停" : "AD emergency stop")
    : /FaultLevel/.test(e.text) ? (zh ? "VCU 故障降级" : "VCU fault reaction") : /DCL/.test(e.text) ? (zh ? "电流超 DCL" : "current > DCL") : zh ? "起步" : "launch");
  const markers = s.events.map((e) => ({ t: e.t - s.t0, label: evName(e), color: evColor(e.level) }));
  const svg = stackedChart([
    { title: zh ? "扭矩（每轮）" : "Torque (per wheel)", unit: "N.m", lines: [
      { label: "TqReq_Base", color: C.ink3, dash: "4 3", pts: rel(s.series["VCU_TqDebug.TqReq_Base"]) },
      { label: "TqLim_BMS", color: C.ok, pts: rel(s.series["VCU_TqDebug.TqLim_BMS"]), width: 2 },
      { label: "TqCmd_Out", color: C.thread, pts: rel(s.series["VCU_TqDebug.TqCmd_Out"]), width: 2.2 },
      { label: "MCU_FR Tq_Act", color: C.fault, dash: "2 2", pts: rel(s.series["MCU_FR.Tq_Act"]) }] },
    { title: zh ? "电池电流" : "Pack current", unit: "A", lines: [
      { label: "Pack_I", color: C.thread, pts: rel(s.series["BMS_Status.Pack_I"]), width: 2.2 },
      { label: "DCL_A", color: C.ok, pts: rel(s.series["BMS_Status.DCL_A"]), width: 2 }] },
    { title: zh ? "母线电压" : "DC voltage", unit: "V", lines: [
      { label: "Pack_V", color: C.ink2, pts: rel(s.series["BMS_Status.Pack_V"]) },
      { label: "MCU_FL DC_V", color: C.thread, dash: "4 3", pts: rel(s.series["MCU_FL.DC_V"]) },
      { label: "MCU_FR DC_V", color: C.fault, pts: rel(s.series["MCU_FR.DC_V"]), width: 2.2 }],
      hlines: [{ y: 55, label: zh ? "MCU 欠压阈值 55 V" : "MCU UV threshold 55 V", color: C.fault }] },
  ], { x0, x1, markers, xLabel: zh ? "距快照开始" : "from snapshot start" });
  const rows = all.map((f, i) => [
    `${i + 1}. ${f.date}`, f.vcu, `${f.tempC} °C / ${f.soc} %`, `${f.dcl} A`, f.hsa ? (zh ? "开" : "on") : (zh ? "无" : "none"),
    `${f1(f.peakI)} A`, `${f1(f.minFrV)} V`, f.overLimit > 0.5 ? `✕ +${f1(f.overLimit)} N.m` : "✓ 0",
    f.stopped ? (zh ? "✕ 急停，码 7" : "✕ stop, code 7") : (zh ? "✓ 正常起步" : "✓ normal")]);
  const first = all[failing];
  return (
    <div className={t.panel}>
      <div className={t.panelHead}>
        <h2 className={t.h2}>{zh ? "数据里看到了什么" : "What the data shows"}</h2>
        <span className={t.dim}>{zh ? `CAN 日志 · ${log.snapshots.length} 次同一坡道起步 · 由 DBC 解码` : `CAN log · ${log.snapshots.length} launches on the same ramp · DBC-decoded`}</span>
      </div>
      <div className={t.row} role="tablist" aria-label={zh ? "选择快照" : "Pick a snapshot"}>
        {all.map((f, i) => <button key={i} role="tab" aria-selected={pick === i} className={pick === i ? t.chipOn : t.chip} onClick={() => setPick(i)}>
          {f.date.slice(5)} · VCU {f.vcu}{f.stopped ? (zh ? " · 急停" : " · stop") : ""}</button>)}
      </div>
      <figure className={t.figure}>
        <div className={t.svg} role="img" aria-label={zh ? "扭矩、电流、电压随时间变化" : "Torque, current and voltage over time"} dangerouslySetInnerHTML={{ __html: svg }} />
        <figcaption><Tag k="observed" />{F.stopped
          ? (zh ? `TqCmd_Out 从起步后 ${f1(F.overLimitAt ?? 0)} s 起超过 TqLim_BMS（最多 +${f1(F.overLimit)} N.m）；Pack_I 升到 ${f1(F.peakI)} A，超过 DCL ${F.dcl} A；MCU_FR 母线电压跌到 ${f1(F.minFrV)} V，低于 55 V，随后报 DTC 0x2A11，VCU 降级，自驾急停。`
            : `TqCmd_Out exceeds TqLim_BMS from ${f1(F.overLimitAt ?? 0)} s (up to +${f1(F.overLimit)} N.m); Pack_I reaches ${f1(F.peakI)} A, above DCL ${F.dcl} A; MCU_FR DC voltage falls to ${f1(F.minFrV)} V, below 55 V; then DTC 0x2A11, VCU fault reaction, AD emergency stop.`)
          : (zh ? `正常起步：峰值电流 ${f1(F.peakI)} A（DCL ${F.dcl} A），MCU_FR 最低 ${f1(F.minFrV)} V，TqCmd_Out 没有超过 TqLim_BMS。` : `Normal launch: peak ${f1(F.peakI)} A (DCL ${F.dcl} A), MCU_FR min ${f1(F.minFrV)} V, TqCmd_Out never above TqLim_BMS.`)}</figcaption>
      </figure>
      <div className={t.subhead}>
        <h3 className={t.h3}>{zh ? "同一辆车、同一坡道的三次起步：正常基线 vs 出问题的那次" : "Three launches, same vehicle, same ramp: the healthy baseline against the failure"}</h3>
      </div>
      <DataTable table={{ cols: zh ? ["起步", "VCU_SW", "电芯温度 / SOC", "DCL", "HSA", "峰值 Pack_I", "MCU_FR 最低电压", "TqCmd_Out 超 TqLim_BMS", "结果"]
        : ["Launch", "VCU_SW", "Cell temp / SOC", "DCL", "HSA", "Peak Pack_I", "MCU_FR min V", "TqCmd_Out above TqLim_BMS", "Outcome"], rows }} />
      {first && <p className={t.note}><Tag k="observed" />{zh
        ? `MCU_FR 的电压在三次起步里都比 MCU_FL 低约 ${f1(all.reduce((a, f) => a + f.frMinusFl, 0) / all.length)} V（峰值电流时），静止时两者相同。`
        : `In all three launches MCU_FR reads about ${f1(all.reduce((a, f) => a + f.frMinusFl, 0) / all.length)} V below MCU_FL at peak current, and the same at standstill.`}</p>}
      {onAsk && <p className={t.invite}>{zh ? "有不同看法？" : "Disagree?"} <button className={t.link} onClick={onAsk}>{zh ? "和 Veyra 一起排查" : "Debug it with Veyra"}</button>{zh ? "，它会用这份数据验证你的判断。" : "."}</p>}
    </div>
  );
}
