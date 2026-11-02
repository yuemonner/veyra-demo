// Preregistered analysis (PREREGISTRATION.md). Run once; results are reported whatever they show.
// node --experimental-strip-types research/experiment-001/transfer.mjs  ->  transfer-results.json, transfer-results.md
import { writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import * as S from "../../frontend/lib/experiment-sim.ts";
import { B_WORLD, B_MODEL, B_FAULTS } from "./robot-b.mjs";

const t0 = Date.now();
const range = (a, n) => Array.from({ length: n }, (_, i) => a + i);
const modelA = S.buildModel(S.SPEC_A), modelB = S.buildModel(B_MODEL);
const runs = {
  A_known: S.benchmark(modelA, "closed", range(100001, 300)),
  A_unknown: S.benchmark(modelA, "open", range(100301, 100)),
  B_known: S.benchmark(modelB, "closed", range(200001, 300), S.ALL_METHODS, B_WORLD, B_FAULTS),
  B_unknown: S.benchmark(modelB, "open", range(200301, 100), S.ALL_METHODS, B_WORLD, B_FAULTS),
};
const metrics = { accuracy: (o) => (o.correct ? 1 : 0), cost: (o) => o.cost, confidentlyWrong: (o) => (o.confident && !o.correct ? 1 : 0), abstained: (o) => (o.confident ? 0 : 1) };
function boot(vals, seed) {
  const n = vals.length, r = S.rng(seed), b = [];
  for (let k = 0; k < 2000; k++) { let s = 0; for (let i = 0; i < n; i++) s += vals[Math.floor(r.next() * n)]; b.push(s / n); }
  b.sort((x, y) => x - y);
  return { mean: vals.reduce((s, x) => s + x, 0) / n, ci: [b[49], b[1950]] };
}
const per = {}, paired = {};
const COMPARE = [["classical", "fixed"], ["classical", "random"], ["classical_widen", "fixed_widen"], ["classical_widen", "classical"], ["fixed_widen", "fixed"]];
for (const [name, run] of Object.entries(runs)) {
  per[name] = {}; paired[name] = {};
  for (const m of S.ALL_METHODS) per[name][m] = Object.fromEntries(Object.entries(metrics).map(([k, f], j) => [k, boot(run.outcomes[m].map(f), 11 + j)]));
  for (const [a, b] of COMPARE) paired[name][`${a} - ${b}`] = Object.fromEntries(Object.entries(metrics).map(([k, f], j) =>
    [k, boot(run.outcomes[a].map((o, i) => f(o) - f(run.outcomes[b][i])), 101 + j)]));
}
// every unresolved or wrong run, by true fault, per method
const failures = {};
for (const [name, run] of Object.entries(runs)) {
  failures[name] = {};
  for (const m of S.ALL_METHODS) {
    const f = {};
    for (const o of run.outcomes[m]) if (!o.correct) { const k = `${o.truth} -> ${o.declared ?? "abstained"}`; f[k] = (f[k] || 0) + 1; }
    failures[name][m] = f;
  }
}
const strip = (k, v) => (k === "trace" ? undefined : v);
const fingerprint = createHash("sha256").update(JSON.stringify(Object.fromEntries(Object.entries(runs).map(([k, r]) => [k, r.outcomes])), strip)).digest("hex").slice(0, 16);
const out = { preregistration: "PREREGISTRATION.md", generated: new Date().toISOString(), seconds: (Date.now() - t0) / 1000, fingerprint,
              policy: S.POLICY, fixedOrder: S.FIXED_ORDER, perMethod: per, paired, failures,
              outcomes: Object.fromEntries(Object.entries(runs).map(([k, r]) => [k, Object.fromEntries(Object.entries(r.outcomes).map(([m, os]) => [m, os.map((o) => ({ truth: o.truth, declared: o.declared, confident: o.confident, correct: o.correct, cost: o.cost, experiments: o.experiments, widened: o.widened, tests: o.steps.map((s) => s.experiment) }))]))])) };
writeFileSync(new URL("./transfer-results.json", import.meta.url), JSON.stringify(out, strip, 1));
const pct = (x) => (100 * x).toFixed(1);
const cell = (v, k) => k === "cost" ? `${v.mean.toFixed(2)} [${v.ci[0].toFixed(2)}, ${v.ci[1].toFixed(2)}]` : `${pct(v.mean)}% [${pct(v.ci[0])}, ${pct(v.ci[1])}]`;
const dcell = (v, k) => k === "cost" ? `${v.mean >= 0 ? "+" : ""}${v.mean.toFixed(2)} [${v.ci[0].toFixed(2)}, ${v.ci[1].toFixed(2)}]` : `${v.mean >= 0 ? "+" : ""}${pct(v.mean)} pts [${pct(v.ci[0])}, ${pct(v.ci[1])}]`;
let md = `# Experiment 001 transfer test results\n\nPreregistered in PREREGISTRATION.md. Fingerprint \`${fingerprint}\`. Every run is included; abstentions are runs that ended without a 95% answer.\n`;
for (const [name, run] of Object.entries(runs)) {
  md += `\n## ${name.replace("_", ", ").replace("A,", "Robot A (development robot),").replace("B,", "Robot B (held out),")} faults: ${run.outcomes.fixed.length} cases\n\n| Method | Correct | Test cost | Confidently wrong | Abstained |\n|---|---|---|---|---|\n`;
  for (const m of S.ALL_METHODS) md += `| ${S.METHODS[m].name} | ${["accuracy", "cost", "confidentlyWrong", "abstained"].map((k) => cell(per[name][m][k], k)).join(" | ")} |\n`;
  md += `\nPaired differences on the same cases (first minus second), 95% bootstrap CI:\n\n| Comparison | Correct | Test cost | Confidently wrong | Abstained |\n|---|---|---|---|---|\n`;
  for (const c of Object.keys(paired[name])) md += `| ${c.split(" - ").map((m) => S.METHODS[m].name).join(" vs ")} | ${["accuracy", "cost", "confidentlyWrong", "abstained"].map((k) => dcell(paired[name][c][k], k)).join(" | ")} |\n`;
}
writeFileSync(new URL("./transfer-results.md", import.meta.url), md);
console.log(md, "\nseconds", out.seconds);
