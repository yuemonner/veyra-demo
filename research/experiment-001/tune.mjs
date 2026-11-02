// Tunes the two free choices that favour the BASELINES or the method, on calibration faults only (seeds 10001-10200).
// The benchmark test seeds (100001+) are never touched here.
//   1. The fixed checklist: every ordering of the five tests is tried; the best (accuracy first, then cost) is kept.
//   2. Veyra's misfit threshold: the loosest p-value whose false-alarm rate on known faults stays at or below 5%.
// node --experimental-strip-types research/experiment-001/tune.mjs
import * as S from "../../frontend/lib/experiment-sim.ts";
const model = S.buildModel();
const cal = Array.from({ length: 200 }, (_, i) => 10001 + i);
const perms = (a) => a.length <= 1 ? [a] : a.flatMap((x, i) => perms([...a.slice(0, i), ...a.slice(i + 1)]).map((p) => [x, ...p]));
const ids = S.EXPERIMENTS.map((e) => e.id);
let best = null;
for (const p of perms(ids)) {
  S.setFixedOrder([...p, ...p]);
  const outs = cal.map((s) => { const f = S.drawFault(s, S.CLOSED_WORLD); return S.runEpisode(model, "fixed", f.params, f.family, s); });
  const sm = S.summarise("fixed", outs);
  if (!best || sm.accuracy > best.accuracy + 1e-9 || (Math.abs(sm.accuracy - best.accuracy) < 1e-9 && sm.meanCost < best.meanCost)) best = { order: p, accuracy: sm.accuracy, meanCost: sm.meanCost };
}
console.log("best fixed order", JSON.stringify(best));
const thresholds = [1e-3, 1e-4, 1e-5, 1e-6, 1e-8];
const rows = [];
for (const t of thresholds) {
  S.POLICY.misfitP = t;
  const closed = cal.slice(0, 120).map((s) => { const f = S.drawFault(s, S.CLOSED_WORLD); return S.runEpisode(model, "veyra", f.params, f.family, s); });
  const open = cal.slice(120).map((s) => { const f = S.drawFault(s, S.OPEN_WORLD); return S.runEpisode(model, "veyra", f.params, f.family, s); });
  rows.push({ p: t, falseAlarm: closed.filter((o) => o.widened).length / closed.length, closedAccuracy: closed.filter((o) => o.correct).length / closed.length,
              openDetected: open.filter((o) => o.widened).length / open.length, openAccuracy: open.filter((o) => o.correct).length / open.length });
}
console.table(rows);
const chosen = rows.find((r) => r.falseAlarm <= 0.05) || rows[rows.length - 1];
console.log(JSON.stringify({ fixedOrder: best.order, misfitP: chosen.p, calibration: { fixed: best, misfit: rows } }));
