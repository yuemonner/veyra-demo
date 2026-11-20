// Decoded CAN log exports ("<ISO time> <Message> Key=value ..."), as a CAN logger writes them after DBC decoding.
// Splits the file into its snapshots, builds a time series per signal, finds the events, and compares the snapshots.
// Deterministic: the plots and the comparison table come straight from the lines of the file.

export type CanEvent = { line: number; t: number; msg: string; level: string; text: string };
export type Snapshot = { label: string; firstLine: number; lastLine: number; t0: number; series: Record<string, { t: number; v: number; line: number }[]>;
  text: Record<string, string>; events: CanEvent[] };
export type CanLog = { header: string[]; snapshots: Snapshot[] };

const ROW = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?:(INFO|WARN|ERROR|DEBUG)\s+)?(\w+)\s+(.*)$/;
const KV = /([A-Za-z][\w]*)=([^\s()]+)/g;

export function looksLikeCanLog(lines: string[]): boolean {
  let n = 0;
  for (const l of lines.slice(0, 400)) if (ROW.test(l) && /=/.test(l)) n++;
  return n > 50 && lines.some((l) => /\b(VCU|BMS|MCU)_\w+/.test(l));
}

export function parseCanLog(lines: string[]): CanLog {
  const header: string[] = [];
  const snapshots: Snapshot[] = [];
  let cur: Snapshot | null = null;
  const start = (label: string, line: number) => { cur = { label, firstLine: line, lastLine: line, t0: NaN, series: {}, text: {}, events: [] }; snapshots.push(cur); };
  lines.forEach((raw, i) => {
    const line = i + 1;
    if (raw.startsWith("# ----")) { start(raw.replace(/^#\s*-+\s*/, ""), line); return; }
    if (raw.startsWith("#")) { if (!snapshots.length) header.push(raw.replace(/^#\s*/, "")); return; }
    const m = raw.match(ROW);
    if (!m) return;
    if (!cur) start("Log", line);
    const s = cur as unknown as Snapshot;
    const t = Date.parse(m[1] + "Z") / 1000;
    if (Number.isNaN(s.t0)) s.t0 = t;
    s.lastLine = line;
    const [, , level, msg, rest] = m;
    if (/_Event$/.test(msg) || level) {
      const lv = level || (rest.match(/^(INFO|WARN|ERROR)\b/) || [])[1] || "INFO";
      s.events.push({ line, t, msg, level: lv, text: rest.replace(/^(INFO|WARN|ERROR)\s+/, "") });
      return;
    }
    for (const kv of rest.matchAll(KV)) {
      const key = `${msg}.${kv[1]}`, num = Number(kv[2]);
      if (Number.isFinite(num)) (s.series[key] ||= []).push({ t, v: num, line });
      else s.text[key] = kv[2];
    }
  });
  return { header, snapshots };
}

const at = (pts: { t: number; v: number }[] | undefined, t: number) => {
  if (!pts?.length) return NaN;
  let best = pts[0];
  for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  return best.v;
};
const max = (pts?: { v: number }[]) => (pts?.length ? Math.max(...pts.map((p) => p.v)) : NaN);
const min = (pts?: { v: number }[]) => (pts?.length ? Math.min(...pts.map((p) => p.v)) : NaN);

export type SnapshotFacts = { label: string; date: string; vcu: string; tempC: number; soc: number; dcl: number; hsa: boolean;
  peakI: number; minPackV: number; minFrV: number; frMinusFl: number; overLimit: number; overLimitAt: number | null; dtc: boolean; stopped: boolean; lines: [number, number] };

/** What each snapshot shows, side by side: the healthy launches are the baseline for the one that failed. */
export function facts(s: Snapshot): SnapshotFacts {
  const I = s.series["BMS_Status.Pack_I"], peakT = I?.length ? I.reduce((a, b) => (b.v > a.v ? b : a)).t : s.t0;
  const out = s.series["VCU_TqDebug.TqCmd_Out"] || [], lim = s.series["VCU_TqDebug.TqLim_BMS"] || [];
  let over = 0, overAt: number | null = null;
  out.forEach((p, i) => { const d = p.v - (lim[i]?.v ?? Infinity); if (d > 0.5 && overAt === null) overAt = p.t - s.t0; if (d > over) over = d; });
  return {
    label: s.label, date: new Date(s.t0 * 1000).toISOString().slice(0, 16).replace("T", " "),
    vcu: s.text["VCU_Status.VCU_SW"] || "?", tempC: at(s.series["BMS_Status.T_cell_min"], s.t0), soc: at(s.series["BMS_Status.SOC"], s.t0),
    dcl: at(s.series["BMS_Status.DCL_A"], s.t0), hsa: (s.series["VCU_Status.HSA_Active"] || []).some((p) => p.v > 0),
    peakI: max(I), minPackV: min(s.series["BMS_Status.Pack_V"]), minFrV: min(s.series["MCU_FR.DC_V"]),
    frMinusFl: at(s.series["MCU_FL.DC_V"], peakT) - at(s.series["MCU_FR.DC_V"], peakT),
    overLimit: over, overLimitAt: overAt,
    dtc: (s.series["MCU_FR.DTC"] || []).some((p) => p.v > 0),
    stopped: s.events.some((e) => /EMERGENCY STOP/i.test(e.text)), lines: [s.firstLine, s.lastLine],
  };
}

/** The window around the launch: from 0.6 s before the first event to 0.9 s after the last one (at least 3 s wide). */
export function focus(s: Snapshot): [number, number] {
  const ev = s.events.map((e) => e.t - s.t0);
  const a = ev.length ? Math.max(0, Math.min(...ev) - 0.6) : 0;
  const b = ev.length ? Math.max(...ev) + 0.9 : 6;
  return [a, Math.max(b, a + 3)];
}
