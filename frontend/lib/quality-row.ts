// The case as one row of a quality-control table (CSV), with the evidence behind each cell. Headers follow the case's language.
type Any = any; // case records are versioned JSON; read defensively

const LEVEL_ZH: Record<string, string> = { open: "未确定", probable: "可能原因", reproduced: "已复现", verified: "已验证" };
const LEVEL_EN: Record<string, string> = { open: "Open", probable: "Probable cause", reproduced: "Reproduced", verified: "Verified" };

/** The case as one row of a quality-control table; the headers follow the case's language. */
export function qualityRow(ladder: Any, result: Any, zh: boolean, url: string): { cols: string[]; row: string[] } {
  const c = ladder?.case || {};
  const hyps: Any[] = (result?.hypotheses || []).filter((h: Any) => h.status !== "ruled_out");
  const role = (r: string) => hyps.filter((h) => h.role === r).map((h) => `${h.area}: ${h.claim}`);
  const main = role("main_cause");
  const rest = [...role("trigger").map((x) => `${zh ? "诱因" : "Trigger"} · ${x}`), ...role("amplifier").map((x) => `${zh ? "放大" : "Amplifier"} · ${x}`)];
  const evidence = (result?.evidence || []).map((e: Any) => `L${e.line}: ${e.shows || ""}`).join(" | ");
  const cols = zh ? ["发生时间", "车辆", "故障现象", "主因", "诱因 / 放大因素", "下一步 / 处理措施", "验证等级", "证据（日志行）", "案例链接"]
    : ["Reported", "Vehicle", "Symptom", "Main cause", "Triggers / amplifiers", "Next action / measures", "Verification", "Evidence (log lines)", "Case link"];
  const row = [String(c.created_at || "").slice(0, 10), c.machine_id || "", c.reported || c.title || "",
    main.join("; ") || result?.explanation || "", rest.join("; "), result?.next_action?.action || "",
    (zh ? LEVEL_ZH : LEVEL_EN)[ladder?.level || "open"], evidence, url];
  return { cols, row };
}

export function csv(cols: string[], rows: string[][]): string {
  const q = (s: string) => `"${String(s ?? "").replace(/"/g, '""')}"`;
  return "﻿" + [cols, ...rows].map((r) => r.map(q).join(",")).join("\r\n");
}

