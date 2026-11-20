"use client";
/* Expected vs observed, layer by layer (app/physics/expected.py, deterministic). What the machine should have done
   given what it was asked, against what it did: the first point where it left the expected path, the protections that
   fired as designed, the symptom, and per layer what the data confirms and what it cannot determine. */
import React, { useEffect, useState } from "react";
import st from "./investigation.module.css";
import { useZh } from "./lang";

type Any = any; // engine output is versioned JSON; read defensively
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const EXP_ZH: Record<string, string> = {
  intent_request: "自动驾驶要求车辆起步",
  vcu_drive: "VCU 在 1.5 秒内进入行驶状态",
  epb_release_cmd: "1.5 秒内报告驻车制动已松开",
  cmd_within_limit: "扭矩指令不超过 BMS 给的扭矩上限",
  torque_follows: "每个轮的实际扭矩跟得上指令（误差在 15% 或 8 N·m 以内）",
  current_within_dcl: "电池电流不超过 BMS 的放电上限",
  bus_above_uv: "每个逆变器的母线电压保持在 55 V 欠压线以上",
  wheels_turn: "3 秒内每个轮的电机都转起来",
  vehicle_moves: "3 秒内车辆开始移动",
  epb_caliper_released: "驻车制动卡钳真的松开了",
  uv_trip: "母线电压低于 55 V 超过 50 毫秒时，逆变器切断扭矩（设计好的保护）",
  fault_stop: "VCU 报电机故障后，自动驾驶停车（设计好的保护）",
  symptom: "VCU 三级故障：切断扭矩、紧急停车",
};
const NEEDS_ZH: Record<string, string> = { epb_caliper_released: "需要 CAN 上有驻车制动卡钳的位置，或驻车电机的电流" };
const LAYER: Record<string, [string, string]> = {
  intent: ["Intent", "意图：操作员或自动驾驶要求做什么"], controller: ["Controller", "控制器：控制器决定并下发了什么"],
  physical: ["Physical", "物理：电机、电源、刹车实际做了什么"], environment: ["Environment", "环境：坡度、温度、电量、软件"],
};
const CTX_ZH: Record<string, string> = { Slope: "坡度", "Coldest cell": "最冷电芯", Charge: "电量", "VCU software": "VCU 软件", "Hill-start assist active": "坡起辅助" };
const KIND: Record<string, [string, string]> = { divergence: ["Left the expected path", "偏离预期"], protection: ["Protection fired as designed", "保护按设计动作"], symptom: ["Symptom", "故障现象"] };

export function ExpectedBehavior({ caseId, call }: { caseId: string; call: Call }) {
  const zh = useZh();
  const T = (en: string, cn: string) => (zh ? cn : en);
  const [d, setD] = useState<Any>(null);
  useEffect(() => { let live = true; call(`/engine/cases/${caseId}/expected`).then((x) => live && setD(x)).catch(() => live && setD(null)); return () => { live = false; }; }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d || d.applies === false || !d.snapshots) return null;
  const X = (id: string, en: string) => (zh ? EXP_ZH[id] || en : en);
  const ctxVal = (k: string, v: Any) => (k === "Hill-start assist active" ? (v ? T("on", "开") : T("off", "关")) : `${v}`);
  const eps = d.snapshots.filter((s: Any) => s.window);
  return (
    <div className={st.block} data-no-zh>
      <h2 className={st.h2}>{T("Expected vs observed", "预期 vs 实际")}</h2>
      <p className={st.fine}>{T("What the machine should have done given what it was asked, against what it did, from the request through the controller to motors, power and brakes. Finds the first point where it left the expected path. Computed by code, not by the AI.",
        "先写下机器本来应该怎么做（从指令、控制器，到电机、电源、刹车），再看它实际做了什么，找出最早偏离的地方。由程序计算，不是 AI 猜的。")}</p>
      <p className={st.fine}>{T(`Where the rules come from: ${d.spec.source}`, "规则来源：根据日志说明和线控底盘的惯例写的示例规则，还需要机器的工程师确认。")}</p>

      {d.status !== "ok" && <p className={st.fine}>{T(d.reason || "Not enough data.", "这份日志里没有要求车辆起步的片段，无法比较。")}</p>}

      {eps.map((s: Any) => {
        const i = d.snapshots.indexOf(s);
        return (
          <div key={i} className={st.expEp}>
            <b>{T(`Episode ${i + 1}: ${s.label.replace(/^snapshot \d+ of \d+:\s*/, "")}`, `第 ${i + 1} 次：${labelZh(s.label.replace(/^snapshot \d+ of \d+:\s*/, ""))}`)}</b>
            <div className={st.signals}>{Object.entries(s.context || {}).map(([k, v]: [string, Any]) => <code key={k}>{`${zh ? CTX_ZH[k] || k : k} ${ctxVal(k, v.value)}${v.unit ? ` ${v.unit}` : ""}`}</code>)}</div>
            {!s.first_divergence && <p className={st.fine}>{T("Behaved as expected throughout.", "全程符合预期。")}</p>}
            {s.first_divergence && <>
              <p className={st.expFirst}>{T("First left the expected path: ", "最早偏离预期：")}<b>{X(s.first_divergence.id, s.first_divergence.text)}</b>
                {` (+${s.first_divergence.first.rel_s} s${zh ? "，" : ", "}${T("line", "第")} ${s.first_divergence.first.line}${zh ? " 行" : ""})`}</p>
              <ol className={st.expChain}>
                {s.chain.map((c: Any, j: number) => (
                  <li key={j} className={st["exp_" + c.kind]}>
                    <span className={st.expT}>{`+${c.t_rel.toFixed(2)} s`}</span>
                    <span className={st.expKind}>{KIND[c.kind][zh ? 1 : 0]}</span>
                    <span>{X(c.id, c.text)}</span>
                    {c.line && <code className={st.ln}>{c.line}</code>}
                  </li>))}
              </ol>
            </>}
          </div>);
      })}

      {(d.contrast || []).length > 0 && (
        <div className={st.dataReq}>
          <span className={st.blindHead}>{T("Broke in some episodes and held in others", "有几次没做到、另几次做到了")}</span>
          <ul>{d.contrast.map((c: Any) => <li key={c.id}><b>{X(c.id, c.text)}</b><span>{T(`Broke in episode ${c.violated_in.join(", ")}; held in ${c.met_in.join(", ")}`, `第 ${c.violated_in.join("、")} 次没做到；第 ${c.met_in.join("、")} 次做到了`)}</span></li>)}</ul>
        </div>)}

      {d.layers && (
        <div className={st.expLayers}>
          {Object.keys(LAYER).filter((k) => d.layers[k]).map((k) => {
            const L = d.layers[k];
            return (
              <div key={k}>
                <b>{LAYER[k][zh ? 1 : 0]}</b>
                {L.confirmed.length > 0 && <p><span className={st.expOk}>{T("Confirmed: ", "能确认：")}</span>{L.confirmed.map((x: Any) => X(x.id, x.text)).join(T("; ", "；"))}</p>}
                {L.diverged.length > 0 && <p><span className={st.expBad}>{T("Did not happen as expected: ", "没按预期发生：")}</span>{L.diverged.map((x: Any) => `${X(x.id, x.text)}${T(` (episode ${x.episodes.join(", ")})`, `（第 ${x.episodes.join("、")} 次）`)}`).join(T("; ", "；"))}</p>}
                {L.cannot_determine.length > 0 && <p><span className={st.expBlind}>{T("Cannot determine: ", "无法确定：")}</span>{L.cannot_determine.map((x: Any) => `${X(x.id, x.text)}${zh ? `（${NEEDS_ZH[x.id] || `需要：${x.needs}`}）` : ` (needs ${x.needs})`}`).join(T("; ", "；"))}</p>}
                {k === "environment" && <p className={st.fine}>{T("Recorded per episode above; conditions are compared, not judged.", "每次的条件见上面；条件只做对比，不判断对错。")}</p>}
              </div>);
          })}
        </div>)}
    </div>
  );
}

/** Chinese wording for the episode labels in a decoded log's snapshot headers (plain words only; values stay as they are). */
function labelZh(t: string): string {
  return t.replace(/\bmorning launch on ([A-Z])-ramp\b/i, "早班在 $1 坡道起步").replace(/\bafternoon launch on ([A-Z])-ramp\b/i, "下午在 $1 坡道起步")
    .replace(/\bbefore the (.+?) OTA\b/i, "$1 OTA 升级之前").replace(/field report: emergency stop, remote code (\d+)/i, "现场报告：急停，遥控器报码 $1")
    .replace(/same vehicle, after the restart/i, "同一辆车，重启之后").replace(/\s*\(/g, "（").replace(/\)/g, "）");
}
