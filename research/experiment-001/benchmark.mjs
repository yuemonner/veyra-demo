// Reproducible benchmark. Test seeds were not used while developing or tuning (tuning used 10001-10200).
// node --experimental-strip-types research/experiment-001/benchmark.mjs  ->  results.json, results.md
import { writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import * as S from "../../frontend/lib/experiment-sim.ts";
const t0 = Date.now();
const model = S.buildModel();
const closed = S.benchmark(model, "closed", Array.from({ length: 300 }, (_, i) => 100001 + i));
const open = S.benchmark(model, "open", Array.from({ length: 100 }, (_, i) => 100301 + i));
// paired comparison on the same hidden faults: cost difference, active minus best fixed, with a bootstrap 95% CI
function paired(a, b, seed = 7) {
  const d = a.map((o, i) => o.cost - b[i].cost), n = d.length, r = S.rng(seed), boots = [];
  for (let k = 0; k < 2000; k++) { let s = 0; for (let i = 0; i < n; i++) s += d[Math.floor(r.next() * n)]; boots.push(s / n); }
  boots.sort((x, y) => x - y);
  const both = a.filter((o, i) => o.correct && b[i].correct).length;
  return { meanDiff: d.reduce((s, x) => s + x, 0) / n, ci: [boots[50], boots[1949]], bothCorrect: both };
}
const fp = createHash("sha256").update(JSON.stringify({ c: closed.outcomes, o: open.outcomes }, (k, v) => (k === "trace" ? undefined : v))).digest("hex").slice(0, 16);
const out = {
  experiment: "001 active physical diagnosis, two-wheeled robot (simulation)", generated: new Date().toISOString(), seconds: (Date.now() - t0) / 1000,
  fingerprint: fp, policy: S.POLICY, fixedOrder: S.FIXED_ORDER, experiments: S.EXPERIMENTS.map(({ id, name, cost, duration }) => ({ id, name, cost, duration })),
  closed: { seeds: closed.seeds, summaries: closed.summaries,
            classicalVsFixed: paired(closed.outcomes.classical, closed.outcomes.fixed), veyraVsFixed: paired(closed.outcomes.veyra, closed.outcomes.fixed) },
  open: { seeds: open.seeds, summaries: open.summaries },
};
writeFileSync(new URL("./results.json", import.meta.url), JSON.stringify(out, null, 1));
const pct = (x) => (100 * x).toFixed(1) + "%";
const row = (s) => `| ${S.METHODS[s.method].name} | ${pct(s.accuracy)} (${pct(s.ci.accuracy[0])}–${pct(s.ci.accuracy[1])}) | ${pct(s.falseCertainty)} | ${pct(s.undetermined)} | ${s.meanExperiments.toFixed(2)} | ${s.meanCost.toFixed(2)} (${s.ci.meanCost[0].toFixed(2)}–${s.ci.meanCost[1].toFixed(2)}) | ${pct(s.widenedRate)} |`;
const head = "| Method | Correct (95% CI) | Confidently wrong | Undetermined | Tests | Test cost (95% CI) | Widened hypotheses |\n|---|---|---|---|---|---|---|";
const md = `# Experiment 001 results (simulation)\n\nFingerprint \`${fp}\` · ${out.closed.summaries[0].n} known-fault cases (seeds ${closed.seeds.join("–")}), ${out.open.summaries[0].n} unknown-fault cases (seeds ${open.seeds.join("–")}).\n\n## Known faults (in the hypothesis list)\n\n${head}\n${closed.summaries.map(row).join("\n")}\n\nPaired, same hidden faults, test cost minus the best fixed checklist: classical ${out.closed.classicalVsFixed.meanDiff.toFixed(2)} (95% CI ${out.closed.classicalVsFixed.ci.map((x) => x.toFixed(2)).join(" to ")}), Veyra ${out.closed.veyraVsFixed.meanDiff.toFixed(2)} (95% CI ${out.closed.veyraVsFixed.ci.map((x) => x.toFixed(2)).join(" to ")}).\n\n## Unknown fault (a motor command cutting out; not in the initial list)\n\n${head}\n${open.summaries.map(row).join("\n")}\n`;
writeFileSync(new URL("./results.md", import.meta.url), md);
console.log(md, "\nseconds", out.seconds);
