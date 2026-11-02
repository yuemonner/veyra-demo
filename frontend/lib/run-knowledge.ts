// What is normal for this machine, and what is not: read from the engine's own run (its baselines, its routine and
// explained events, its departures). A joint whose tracking error is noisy but inside its healthy range is normal;
// one that leaves that range is not. Each answer to a question names the baseline it was judged against.

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
export type Table = { cols: string[]; rows: (string | number)[][] };
export type Verdict = "supports" | "against" | "cannot_tell" | "facts";
export type Answer = { check: string; title: string; claim: string; verdict: Verdict; text: string; points: string[]; table?: Table;
  lines: number[]; about: RegExp | null; settle?: string; plots?: { rule: string; text: string }[] };

export type AxisRow = { axis: string; pair: string; unit: string; status: "normal" | "unusual"; source: string; statistic: string; limit: number;
  normal: number; now: number; ratio: number; bin: string; noise: number; peak?: number; since?: string; atStart?: number; peakRatio?: number };

const n1 = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 10 ? x.toFixed(1) : x.toFixed(2));
const PAIR: Record<string, string> = { joint_position: "Joint position", body_velocity: "Body velocity", turn_rate: "Turn rate" };

/** Every tracked axis: its normal error (from healthy data of this machine) against this case's window. */
export function axes(run: Any): AxisRow[] {
  const tr = run?.findings?.tracking;
  if (!tr || tr.status !== "computed") return [];
  const rule = String(tr.rule || "");
  const limitFor = (pair: string | undefined): { stat: string; limit: number } => {
    const own = pair ? rule.match(new RegExp(`${pair}:[^;]*?÷ baseline (p95|median)[^;]*?≥ (\\d+(?:\\.\\d+)?)`)) : null;
    const any = own || rule.match(/÷ baseline (p95|median)[^;]*?≥ (\d+(?:\.\d+)?)/);
    return any ? { stat: any[1], limit: Number(any[2]) } : { stat: "p95", limit: 1 };
  };
  return (tr.axes || []).map((a: Any) => {
    const { stat: ruleStat, limit } = limitFor(a.pair);
    const stat = a.baseline_statistic || ruleStat;
    let best = { ratio: -1, bin: "", normal: NaN, now: NaN, noise: NaN };
    for (const [bin, sw] of Object.entries(a.symptom_window || {}) as [string, Any][]) {
      const b = (a.baseline || {})[bin];
      if (!b || !sw) continue;
      const ratio = (sw[stat] ?? 0) / Math.max(1e-9, b[stat] ?? 0);
      if (ratio > best.ratio) best = { ratio, bin, normal: b[stat], now: sw[stat], noise: stat === "p95" ? b.median : b.p95 };
    }
    return { axis: a.axis, pair: PAIR[a.pair] || (a.unit === "°" || !a.unit ? "Attitude" : a.pair || ""), unit: a.unit || "°", status: a.status === "departed" ? "unusual" : "normal",
      source: a.baseline_source || "", statistic: stat, limit, normal: best.normal, now: best.now, ratio: best.ratio, bin: best.bin, noise: best.noise,
      peak: a.departure ? a.departure.peak_error ?? a.departure.peak_error_deg : undefined, since: a.departure?.start,
      atStart: a.departure?.ratio_at_start, peakRatio: a.departure?.peak_ratio };
  }).filter((r: AxisRow) => r.ratio >= 0);
}

export type Grouped = { reason: string; count: number; example: string; kind: string; domain: string };

/** Events the engine judged routine or explained, grouped by the reason it gave. */
export function treatedAsNormal(run: Any): Grouped[] {
  const out = new Map<string, Grouped>();
  for (const [domain, d] of Object.entries(run?.domains || {}) as [string, Any][]) {
    for (const o of d.observations || []) {
      if (o.kind !== "routine" && o.kind !== "explained") continue;
      const m = String(o.text).match(/\((routine: [\s\S]*)\)\s*$/);
      const reason = m ? m[1].replace(/^routine:\s*/, "") : o.kind === "explained" ? String(o.text).split(/,\s*but\s*/).slice(1).join(", but ") || o.text : o.text;
      const key = `${o.kind}|${reason}`;
      const g = out.get(key) || { reason, count: 0, example: String(o.text).replace(/\s*\(routine:[\s\S]*$/, ""), kind: o.kind, domain };
      g.count += 1; out.set(key, g);
    }
  }
  return [...out.values()].sort((a, b) => b.count - a.count);
}

export function unusual(run: Any): { rule: string; text: string; domain: string }[] {
  const out: { rule: string; text: string; domain: string }[] = [];
  for (const [domain, d] of Object.entries(run?.domains || {}) as [string, Any][])
    for (const o of d.observations || []) if (o.kind === "anomaly" || o.kind === "delay") out.push({ rule: o.rule, text: o.text, domain });
  return out;
}

export function outputRanges(run: Any): Table | null {
  const ch = run?.findings?.actuator_outputs?.channels || [];
  if (!ch.length) return null;
  const f = run.findings.actuator_outputs.findings || [];
  return { cols: ["Output", "Normal range", "Samples", "Status"],
    rows: ch.map((c: Any) => { const hit = f.find((x: Any) => x.channel === c.channel);
      return [c.channel, c.reference_range ? `${c.reference_range[0]}–${c.reference_range[1]}` : "—", c.samples, hit ? `${hit.rule.toLowerCase()}: held ${hit.held_value} for ${(Date.parse(hit.end) / 1000 - hit.t_start).toFixed(1)} s` : "within normal"]; }) };
}

export function axisTable(rows: AxisRow[]): Table {
  return { cols: ["Axis", "Error in healthy data", "Error in this case", "This ÷ healthy", "Rule flags at", "Verdict"],
    rows: rows.map((r) => [r.axis, `${n1(r.normal)} ${r.unit} (${r.statistic})`, `${n1(r.now)} ${r.unit} (${r.statistic})${r.peak != null ? ` · peak ${n1(r.peak)}` : ""}`,
      `${r.ratio.toFixed(2)}×`, `${r.limit}× for ≥ 2 s`, r.status === "unusual" ? `outside normal${r.atStart != null ? ` (${r.atStart}× → ${r.peakRatio}×)` : ""}` : "within normal"]) };
}

// ---------------------------------------------------------------------------------------------------------------
// Questions about a recording, matched to the engine's own results.

const ALIAS: [RegExp, string][] = [
  [/\bleft front\b/, "lf"], [/\bright front\b/, "rf"], [/\bleft (hind|rear|back)\b/, "lh"], [/\bright (hind|rear|back)\b/, "rh"],
  [/\bknee\b/, "kfe"], [/\bhip (flex\w*|pitch)\b/, "hfe"], [/\bhip (abd\w*|roll)\b/, "haa"], [/\bhip\b/, "hfe haa"],
  [/\bspeed\b/, "velocity"], [/\bservos?\b/, "output"], [/\bmotor\b/, "output joint"], [/\bheading\b/, "yaw"],
];
const STOP = new Set(["the", "and", "for", "this", "that", "what", "was", "were", "are", "is", "it", "its", "any", "all", "just", "really", "machine", "robot",
  "data", "case", "does", "did", "can", "could", "why", "how", "veyra", "there", "with", "from", "out", "not", "ok", "okay", "be", "of", "to", "in", "on", "or", "an", "as", "at"]);
const LEGS = new Set(["lf", "rf", "lh", "rh"]), JOINTS = new Set(["kfe", "hfe", "haa"]);
const NOISE = /\b(noise|noisy|spikes?|spiky|jitter|contact|touch ?down|impact|bump|encoder|glitch|transient)\b/;

export function answerRun(run: Any, question: string, labels: Record<string, string> = {}): Answer[] {
  let q = ` ${question.toLowerCase()} `;
  for (const [re, add] of ALIAS) if (re.test(q)) q += ` ${add} `;
  const words = q.replace(/[^a-z0-9_ ]/g, " ").split(/\s+/).filter((w) => w.length >= 2 && !STOP.has(w));
  const has = (s: string) => { const l = s.toLowerCase(); return words.some((w) => w.length >= 4 && l.includes(w)) || words.some((w) => l.split(/[^a-z0-9]+/).includes(w)); };
  const out: Answer[] = [];
  const A = axes(run);
  const legs = words.filter((w) => LEGS.has(w)), joints = words.filter((w) => JOINTS.has(w));
  const ax = A.filter((r) => {
    const parts = r.axis.toLowerCase().split(/[_ ]/);
    if ((legs.length || joints.length) && parts.some((p) => LEGS.has(p) || JOINTS.has(p)))
      return (!legs.length || legs.some((l) => parts.includes(l))) && (!joints.length || joints.some((j) => parts.includes(j)));
    return parts.length > 1 ? parts.every((p) => words.includes(p)) || words.includes(r.axis.toLowerCase()) : words.includes(parts[0]);
  });
  if (ax.length) {
    const bad = ax.filter((r) => r.status === "unusual");
    out.push({ check: "axes", title: ax.length === 1 ? `${ax[0].axis}: against its normal behaviour` : `${ax.length} axes against their normal behaviour`, claim: "", verdict: bad.length ? "supports" : "against",
      text: bad.length ? bad.map((r) => `${r.axis} left its normal range${r.since ? ` at ${r.since.replace("T", " ").replace(/\.\d+Z$/, " UTC")}` : ""}: its rolling tracking error reached ${r.atStart ?? "?"}× the healthy limit and rose to ${r.peakRatio ?? "?"}×${r.peak != null ? ` (peak error ${n1(r.peak)}${r.unit})` : ""}.`).join(" ")
        : `Within normal. ${ax.map((r) => r.axis).join(", ")} ${ax.length > 1 ? "stay" : "stays"} inside the error range ${ax.length > 1 ? "they show" : "it shows"} in healthy data.`,
      points: [`Normal is judged per command size against ${ax[0].source}, using the ${ax[0].statistic} of the error. A finding needs the rolling 3 s median of the error above ${ax[0].limit}× that value for at least 2 s, so noise and short spikes (contact, touchdown) are not flagged.${ax[0].statistic === "p95" ? " 5% of healthy samples are above a 95th percentile by definition." : ""}`],
      table: axisTable(ax), lines: [], about: null,
      plots: unusual(run).filter((o) => o.rule === "R-TRACKING" && bad.some((r) => o.text.startsWith(r.axis))).map((o) => ({ rule: o.rule, text: o.text })) });
  }
  if (NOISE.test(q)) {
    const worst = [...A].filter((r) => r.status === "normal").sort((a, b) => b.ratio / b.limit - a.ratio / a.limit)[0];
    out.push({ check: "noise", title: "Noise and spikes against what is normal", claim: "Spikes in the data mean a fault", verdict: A.some((r) => r.status === "unusual") ? "cannot_tell" : "against",
      text: "A spike is only a finding if it is unusual for this machine. Veyra compares each axis with its own healthy data, at the same command size.",
      points: [
        `${A.filter((r) => r.status === "normal").length} of ${A.length} axes stay within their normal error.`,
        ...(worst ? [worst.statistic === "p95"
          ? `Closest to its limit: ${worst.axis}. Its 95th-percentile error in this window is ${worst.ratio.toFixed(2)}× the healthy one, so the spikes are there; but its rolling 3 s median never stays above the healthy limit for 2 s, so they are not a departure.`
          : `Closest to its limit: ${worst.axis}, with a median error ${worst.ratio.toFixed(2)}× the healthy median; the rule needs ${worst.limit}× sustained for 2 s.`] : []),
        ...treatedAsNormal(run).slice(0, 2).map((g) => `${g.count} event${g.count > 1 ? "s" : ""} treated as routine: ${g.reason}.`),
        A.some((r) => r.status === "unusual") ? `Outside normal: ${A.filter((r) => r.status === "unusual").map((r) => `${r.axis} (rolling error ${r.atStart ?? "?"}× → ${r.peakRatio ?? "?"}× the healthy ${r.statistic})`).join(", ")}. That is a sustained departure (the rule needs ≥ 2 s), not a single spike.` : "Nothing departs from normal for longer than the rule's 2 s, so no spike is reported as a fault.",
      ], table: axisTable(A), lines: [], about: null });
  }
  if (/\b(routine|ignored?|dismiss\w*|suppress\w*|treated as normal|not flagged)\b/.test(q)) {
    const g = treatedAsNormal(run);
    out.push({ check: "routine", title: "What Veyra treated as normal, and why", claim: "", verdict: "facts",
      text: g.length ? `${g.reduce((n, x) => n + x.count, 0)} event${g.reduce((n, x) => n + x.count, 0) > 1 ? "s" : ""} in ${g.length} group${g.length > 1 ? "s" : ""}. Each is still in the record; none is hidden.` : "Nothing in this case was set aside as routine.",
      points: [], table: g.length ? { cols: ["Events", "Example", "Why it is normal here"], rows: g.map((x) => [x.count, x.example, x.reason]) } : undefined, lines: [], about: null });
  }
  if (/\b(normal|healthy|baseline|expected|usual|unusual|summary|overview|what happened)\b/.test(q) && !out.length) {
    out.push({ check: "normal", title: "Normal against unusual, for this machine", claim: "", verdict: "facts",
      text: `${A.filter((r) => r.status === "unusual").length} of ${A.length} tracked axes are outside normal; ${unusual(run).length} finding${unusual(run).length === 1 ? "" : "s"} in all; ${treatedAsNormal(run).reduce((n, g) => n + g.count, 0)} event${treatedAsNormal(run).reduce((n, g) => n + g.count, 0) === 1 ? "" : "s"} treated as routine.`,
      points: unusual(run).slice(0, 4).map((o) => `${labels[o.domain] || o.domain}: ${o.text}`), table: A.length ? axisTable(A) : undefined, lines: [], about: null });
  }
  const obs = unusual(run).filter((o) => has(o.text));
  const routine = treatedAsNormal(run).filter((g) => has(g.example) || has(g.reason));
  if (!out.length && (obs.length || routine.length)) {
    out.push({ check: "observations", title: "What the engine found about that", claim: "", verdict: obs.length ? "supports" : "against",
      text: obs.length ? `Unusual: ${obs.length} finding${obs.length > 1 ? "s" : ""}.` : `Normal for this machine: ${routine.reduce((n, g) => n + g.count, 0)} matching event${routine.length > 1 ? "s" : ""}, each with the reason it is routine.`,
      points: [...obs.slice(0, 4).map((o) => `${labels[o.domain] || o.domain}: ${o.text}`), ...routine.slice(0, 3).map((g) => `Routine (${g.count}×): ${g.example}. Why: ${g.reason}.`)],
      lines: [], about: null, plots: obs.slice(0, 2).map((o) => ({ rule: o.rule, text: o.text })) });
  }
  const outT = outputRanges(run);
  if (outT && /\b(output|rudder|aileron|elevator|throttle|servo|pwm|stuck|held)\b/.test(q))
    out.push({ check: "outputs", title: "Actuator outputs against their normal range", claim: "", verdict: "facts", text: "Each output's normal range comes from the reference part of this flight; a hold is compared with the longest hold of that value in the machine's known-good runs.",
      points: [], table: outT, lines: [], about: null });
  return out;
}

export const RUN_SUGGESTIONS = (run: Any): string[] => {
  const A = axes(run), bad = A.filter((r) => r.status === "unusual");
  const s: string[] = [];
  if (bad[0]) s.push(`Is ${bad[0].axis} really outside normal?`);
  if (A.length) s.push("Are the spikes just noise or contact?");
  const ok = A.find((r) => r.status === "normal" && /KFE|knee/i.test(r.axis)) || A.find((r) => r.status === "normal");
  if (ok) s.push(`Is ${ok.axis} normal?`);
  if (outputRanges(run)) s.push("Was any servo output stuck?");
  if (treatedAsNormal(run).length) s.push("What did Veyra treat as routine?");
  s.push("What is normal for this machine?");
  return s.slice(0, 5);
};
