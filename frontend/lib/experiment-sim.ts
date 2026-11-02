// Research prototype 001: diagnose an unfamiliar two-wheeled robot by choosing experiments.
//
// Everything here is a real computation, deterministic for a given seed, with no dependencies, so the
// same code runs the browser demo and the benchmark script.
//
//   Simulator     differential-drive robot with motors, wheel slip, encoders, gyro, pose tracker, command delay.
//   Faults        a hidden fault family and parameter: slip, weak motor, encoder scale, gyro bias, delay;
//                 plus "library" families (intermittent motor command, e.g. a loose cable) that are NOT in the
//                 initial hypothesis set, for the open-world test.
//   Likelihood    synthetic likelihood (Wood 2010): for each hypothesis (family x parameter grid point) and each
//                 experiment, K seeded noisy simulations give the mean and spread of window-averaged sensor
//                 features; an observation is scored as independent Gaussians around that mean.
//   Posterior     Bayesian over grid points, marginalised to families.
//   Selection     the same posterior and stopping rule for every method; only the choice of the next experiment
//                 (and, for Veyra, the misfit check) differs. See METHODS.

export type Command = { v: number; w: number };
export type Experiment = { id: string; name: string; cost: number; duration: number; command: (t: number) => Command };
// aL/aR: traction limit (m/s^2) - a wheel loses grip when asked to accelerate harder than this.
// wL/wR: top wheel speed (rad/s) - a weak motor saturates below the commanded speed.
// eL/eR: encoder scale. gyroBias: rad/s. delay: command latency (s). dropL/dropR: share of 0.2 s blocks a command cuts out.
export type Params = {
  aL: number; aR: number; wL: number; wR: number; eL: number; eR: number;
  gyroBias: number; delay: number; dropL: number; dropR: number;
};
export type Family = { id: string; name: string; library: boolean; grid: number[]; apply: (p: Params, x: number) => void };
export type Hypothesis = { family: string; value: number; params: Params };
export type Trace = { t: number[]; x: number[]; y: number[]; th: number[]; encL: number[]; encR: number[]; gyro: number[] };

// ---------- robot and sensors ----------
export const ROBOT = { wheelRadius: 0.05, track: 0.3, motorTau: 0.08, dt: 0.01, sampleEvery: 10, dropBlock: 0.2 };
// Field conditions: no motion-capture. Encoders and gyro stream at 10 Hz; position and heading are measured once,
// at the end of each test (tape or marker), with centimetre-level error.
export const NOISE = { pose: 0.03, heading: 0.03, encoder: 0.6, gyro: 0.03 };
export const NOMINAL: Params = { aL: 20, aR: 20, wL: 30, wR: 30, eL: 1, eR: 1, gyroBias: 0, delay: 0.03, dropL: 0, dropR: 0 };

const lin = (a: number, b: number, n: number) => Array.from({ length: n }, (_, i) => +(a + ((b - a) * i) / (n - 1)).toFixed(4));

export const FAMILIES: Family[] = [
  { id: "nominal", name: "No fault", library: false, grid: [0], apply: () => {} },
  { id: "slip_left", name: "Left wheel losing grip", library: false, grid: lin(0.6, 3.0, 13), apply: (p, x) => { p.aL = x; } },
  { id: "slip_right", name: "Right wheel losing grip", library: false, grid: lin(0.6, 3.0, 13), apply: (p, x) => { p.aR = x; } },
  { id: "weak_left", name: "Left motor can't reach speed", library: false, grid: lin(9, 17, 11), apply: (p, x) => { p.wL = x; } },
  { id: "weak_right", name: "Right motor can't reach speed", library: false, grid: lin(9, 17, 11), apply: (p, x) => { p.wR = x; } },
  { id: "encoder_left", name: "Left encoder mis-scaled", library: false, grid: [...lin(0.9, 0.98, 9), ...lin(1.02, 1.1, 9)], apply: (p, x) => { p.eL = x; } },
  { id: "encoder_right", name: "Right encoder mis-scaled", library: false, grid: [...lin(0.9, 0.98, 9), ...lin(1.02, 1.1, 9)], apply: (p, x) => { p.eR = x; } },
  { id: "gyro_bias", name: "Gyro drifting", library: false, grid: [...lin(-0.06, -0.008, 9), ...lin(0.008, 0.06, 9)], apply: (p, x) => { p.gyroBias = x; } },
  { id: "delay", name: "Control delay", library: false, grid: lin(0.06, 0.3, 13), apply: (p, x) => { p.delay = x; } },
  { id: "intermittent_left", name: "Left motor command cutting out", library: true, grid: lin(0.05, 0.3, 8), apply: (p, x) => { p.dropL = x; } },
  { id: "intermittent_right", name: "Right motor command cutting out", library: true, grid: lin(0.05, 0.3, 8), apply: (p, x) => { p.dropR = x; } },
];
export const familyById = (id: string) => FAMILIES.find((f) => f.id === id)!;

// Ranges the hidden fault is drawn from (continuous, so it never sits exactly on a grid point).
export const TRUE_RANGES: Record<string, [number, number][]> = {
  nominal: [[0, 0]], slip_left: [[0.8, 2.6]], slip_right: [[0.8, 2.6]], weak_left: [[10, 16]], weak_right: [[10, 16]],
  encoder_left: [[0.91, 0.97], [1.03, 1.09]], encoder_right: [[0.91, 0.97], [1.03, 1.09]], gyro_bias: [[-0.05, -0.012], [0.012, 0.05]],
  delay: [[0.08, 0.25]], intermittent_left: [[0.08, 0.25]], intermittent_right: [[0.08, 0.25]],
};

const ramp = (t: number, t0: number, t1: number, v: number) => (t <= t0 ? 0 : t >= t1 ? v : (v * (t - t0)) / (t1 - t0));
// Costs: rough minutes of engineer time, space and hardware stress. Gentle tests are cheap; launches and top speed are not.
export const EXPERIMENTS: Experiment[] = [
  { id: "hold", name: "Hold still", cost: 1, duration: 2, command: () => ({ v: 0, w: 0 }) },
  { id: "cruise", name: "Gentle straight run", cost: 2, duration: 3, command: (t) => ({ v: ramp(t, 0, 1.5, 0.4), w: 0 }) },
  { id: "spin", name: "Slow spin in place", cost: 2, duration: 3, command: (t) => ({ v: 0, w: ramp(t, 0, 1, 1.2) }) },
  { id: "launch", name: "Hard launch", cost: 3, duration: 2, command: (t) => ({ v: t < 0.3 ? 0 : 0.4, w: 0 }) },
  { id: "topspeed", name: "Top-speed run", cost: 4, duration: 3.5, command: (t) => ({ v: ramp(t, 0, 2, 0.9), w: 0 }) },
];
export const experimentById = (id: string) => EXPERIMENTS.find((e) => e.id === id)!;

// ---------- seeded randomness ----------
export function rng(seed: number) {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  let spare: number | null = null;
  const normal = () => {
    if (spare !== null) { const s = spare; spare = null; return s; }
    let u = 0; while (u === 0) u = next();
    const v = next(), r = Math.sqrt(-2 * Math.log(u));
    spare = r * Math.sin(2 * Math.PI * v);
    return r * Math.cos(2 * Math.PI * v);
  };
  return { next, normal };
}
export type Rng = ReturnType<typeof rng>;

// ---------- simulator ----------
export function simulate(p: Params, exp: Experiment, r: Rng | null): Trace {
  const { wheelRadius: R, track: B, motorTau: tau, dt, sampleEvery, dropBlock } = ROBOT;
  const steps = Math.round(exp.duration / dt), delaySteps = Math.round(p.delay / dt);
  const blocks = Math.ceil(exp.duration / dropBlock) + 1;
  const cutL = Array.from({ length: blocks }, () => (r && p.dropL > 0 ? r.next() < p.dropL : false));
  const cutR = Array.from({ length: blocks }, () => (r && p.dropR > 0 ? r.next() < p.dropR : false));
  let x = 0, y = 0, th = 0, oL = 0, oR = 0, uL = 0, uR = 0;
  const out: Trace = { t: [], x: [], y: [], th: [], encL: [], encR: [], gyro: [] };
  const n = (s: number) => (r ? r.normal() * s : 0);
  for (let i = 1; i <= steps; i++) {
    const tc = (i - 1 - delaySteps) * dt;
    const c = tc >= 0 ? exp.command(tc) : { v: 0, w: 0 };
    const blk = Math.floor(((i - 1) * dt) / dropBlock);
    // without a random source the expected effect of a cutting-out command is a proportional loss
    const gL = r ? (cutL[blk] ? 0 : 1) : 1 - p.dropL, gR = r ? (cutR[blk] ? 0 : 1) : 1 - p.dropR;
    const tL = ((c.v - (c.w * B) / 2) / R) * gL, tR = ((c.v + (c.w * B) / 2) / R) * gR;
    const clip = (v: number, m: number) => Math.max(-m, Math.min(m, v));
    oL += ((clip(tL, p.wL) - oL) * dt) / tau;
    oR += ((clip(tR, p.wR) - oR) * dt) / tau;
    // the ground under each wheel follows the wheel, but no faster than traction allows; the encoder sees the wheel
    uL += clip(R * oL - uL, p.aL * dt);
    uR += clip(R * oR - uR, p.aR * dt);
    const v = (uL + uR) / 2, w = (uR - uL) / B;
    x += v * Math.cos(th) * dt; y += v * Math.sin(th) * dt; th += w * dt;
    if (i % sampleEvery === 0) {
      out.t.push(+(i * dt).toFixed(3));
      out.x.push(x + n(NOISE.pose)); out.y.push(y + n(NOISE.pose)); out.th.push(th + n(NOISE.heading));
      out.encL.push(p.eL * oL + n(NOISE.encoder)); out.encR.push(p.eR * oR + n(NOISE.encoder));
      out.gyro.push(w + p.gyroBias + n(NOISE.gyro));
    }
  }
  return out;
}

// ---------- features: 0.5 s window means (and encoder spread) ----------
export const WINDOW = 5;
export const FLOOR = { pose: 0.01, heading: 0.01, enc: 0.08, encStd: 0.08, gyro: 0.004 };
// window features: encL, encR mean; encL, encR spread; gyro mean. Then the end-of-test pose: x, y, heading.
const WIN_FLOORS = [FLOOR.enc, FLOOR.enc, FLOOR.encStd, FLOOR.encStd, FLOOR.gyro];
export const floorOf = (j: number, d: number) => (j >= d - 3 ? [FLOOR.pose, FLOOR.pose, FLOOR.heading][j - (d - 3)] : WIN_FLOORS[j % 5]);

export function features(tr: Trace): number[] {
  const f: number[] = [];
  const mean = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
  const sd = (a: number[]) => { const m = mean(a); return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1)); };
  for (let s = 0; s + WINDOW <= tr.t.length; s += WINDOW) {
    const w = (a: number[]) => a.slice(s, s + WINDOW);
    f.push(mean(w(tr.encL)), mean(w(tr.encR)), sd(w(tr.encL)), sd(w(tr.encR)), mean(w(tr.gyro)));
  }
  const k = tr.t.length - 1;
  f.push(tr.x[k], tr.y[k], tr.th[k]);
  return f;
}

// ---------- synthetic likelihood model ----------
export const MC_RUNS = 48;
export type Prediction = { mean: Float64Array; sd: Float64Array };
export type Model = { hyps: Hypothesis[]; pred: Record<string, Prediction[]> };

export function hypotheses(includeLibrary: boolean): Hypothesis[] {
  const out: Hypothesis[] = [];
  for (const f of FAMILIES) {
    if (f.library && !includeLibrary) continue;
    for (const v of f.grid) { const p = { ...NOMINAL }; f.apply(p, v); out.push({ family: f.id, value: v, params: p }); }
  }
  return out;
}

export function buildModel(): Model {
  const hyps = hypotheses(true), pred: Record<string, Prediction[]> = {};
  for (const e of EXPERIMENTS) {
    const ei = EXPERIMENTS.indexOf(e);
    const stats = hyps.map((h, hi) => {
      const runs = Array.from({ length: MC_RUNS }, (_, k) => features(simulate(h.params, e, rng(9_000_000 + ei * 1_000_003 + hi * 1009 + k * 7))));
      const d = runs[0].length, mean = new Float64Array(d), vr = new Float64Array(d);
      for (let j = 0; j < d; j++) {
        let m = 0; for (const r of runs) m += r[j]; m /= runs.length;
        let v = 0; for (const r of runs) v += (r[j] - m) ** 2; v /= runs.length - 1;
        mean[j] = m; vr[j] = v;
      }
      return { mean, vr };
    });
    // Sensor noise is the same whatever the fault, so it is pooled across the fixed-parameter hypotheses: estimating it
    // per hypothesis from a few runs lets sampling error masquerade as evidence. Faults that are themselves random
    // (a command cutting out) keep their own, larger spread.
    const d = stats[0].mean.length, pooled = new Float64Array(d);
    const fixed = hyps.map((h, i) => [h, i] as const).filter(([h]) => !familyById(h.family).library);
    for (let j = 0; j < d; j++) { let v = 0; for (const [, i] of fixed) v += stats[i].vr[j]; pooled[j] = v / fixed.length; }
    pred[e.id] = hyps.map((h, i) => {
      const own = familyById(h.family).library;
      const sd = new Float64Array(d);
      for (let j = 0; j < d; j++) sd[j] = Math.sqrt((own ? Math.max(stats[i].vr[j], pooled[j]) : pooled[j]) + floorOf(j, d) ** 2);
      return { mean: stats[i].mean, sd };
    });
  }
  return { hyps, pred };
}

export function logLik(p: Prediction, y: number[]): number {
  let s = 0;
  for (let j = 0; j < y.length; j++) { const z = (y[j] - p.mean[j]) / p.sd[j]; s += -0.5 * z * z - Math.log(p.sd[j]); }
  return s;
}
export function chi2(p: Prediction, y: number[]): number {
  let s = 0; for (let j = 0; j < y.length; j++) { const z = (y[j] - p.mean[j]) / p.sd[j]; s += z * z; } return s;
}
// upper tail of chi-square (Wilson–Hilferty)
export function chi2Tail(x: number, k: number): number {
  const z = (Math.cbrt(x / k) - (1 - 2 / (9 * k))) / Math.sqrt(2 / (9 * k));
  return 0.5 * erfc(z / Math.SQRT2);
}
function erfc(x: number): number {
  const t = 1 / (1 + 0.5 * Math.abs(x));
  const r = t * Math.exp(-x * x - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
  return x >= 0 ? r : 2 - r;
}

// ---------- belief ----------
export type Observation = { experiment: string; y: number[]; trace: Trace };
export type Belief = { active: number[]; logPost: Float64Array; families: Record<string, number> };

export function belief(model: Model, obs: Observation[], includeLibrary: boolean): Belief {
  const active = model.hyps.map((h, i) => [h, i] as const).filter(([h]) => includeLibrary || !familyById(h.family).library).map(([, i]) => i);
  const famSize: Record<string, number> = {};
  for (const i of active) famSize[model.hyps[i].family] = (famSize[model.hyps[i].family] || 0) + 1;
  const nFam = Object.keys(famSize).length;
  const lp = new Float64Array(model.hyps.length).fill(-Infinity);
  for (const i of active) {
    let s = Math.log(1 / nFam / famSize[model.hyps[i].family]); // uniform over families, then over each family's grid
    for (const o of obs) s += logLik(model.pred[o.experiment][i], o.y);
    lp[i] = s;
  }
  let mx = -Infinity; for (const i of active) mx = Math.max(mx, lp[i]);
  let z = 0; for (const i of active) z += Math.exp(lp[i] - mx);
  const families: Record<string, number> = {};
  for (const i of active) { lp[i] = lp[i] - mx - Math.log(z); families[model.hyps[i].family] = (families[model.hyps[i].family] || 0) + Math.exp(lp[i]); }
  return { active, logPost: lp, families };
}

export function entropy(fam: Record<string, number>): number {
  let h = 0; for (const p of Object.values(fam)) if (p > 1e-12) h -= p * Math.log2(p); return h;
}

// Expected information gain about the fault FAMILY from running experiment e, by Monte Carlo over the current belief.
export function expectedInfoGain(model: Model, b: Belief, e: string, r: Rng, samples = 32): number {
  const w = b.active.map((i) => Math.exp(b.logPost[i]));
  const cum: number[] = []; let c = 0; for (const x of w) cum.push((c += x));
  const h0 = entropy(b.families);
  let hSum = 0;
  for (let s = 0; s < samples; s++) {
    const u = r.next() * c; let k = 0; while (cum[k] < u) k++;
    const p = model.pred[e][b.active[k]];
    const y = Array.from(p.mean, (m, j) => m + r.normal() * p.sd[j]);
    let mx = -Infinity; const l = b.active.map((i) => { const v = b.logPost[i] + logLik(model.pred[e][i], y); mx = Math.max(mx, v); return v; });
    const fam: Record<string, number> = {}; let z = 0;
    l.forEach((v, j) => { const q = Math.exp(v - mx); z += q; const f = model.hyps[b.active[j]].family; fam[f] = (fam[f] || 0) + q; });
    for (const f in fam) fam[f] /= z;
    hSum += entropy(fam);
  }
  return Math.max(0, h0 - hSum / samples);
}

// ---------- methods ----------
// budget is in cost units, the same quantity the active methods trade information against
// misfitP: loosest threshold whose false-alarm rate on known faults was <= 5% on the calibration seeds (tune.mjs).
export const POLICY = { confident: 0.95, budget: 14, maxRepeats: 2, misfitP: 1e-8, eigSamples: 64 };
// Best fixed order, tuned on calibration seeds that the benchmark never tests on (see scripts/research/tune-fixed.ts).
// Tuned by research/experiment-001/tune.mjs on calibration seeds 10001-10200 (all 120 orders tried).
export let FIXED_ORDER = ["topspeed", "launch", "spin", "hold", "cruise", "topspeed", "launch", "spin", "hold", "cruise"];
export const setFixedOrder = (o: string[]) => { FIXED_ORDER = o; };

export type MethodId = "fixed" | "random" | "classical" | "veyra";
export const METHODS: Record<MethodId, { name: string; detail: string }> = {
  fixed: { name: "Fixed checklist", detail: "The best fixed order of tests, tuned on separate calibration faults" },
  random: { name: "Random order", detail: "Tests in a random order (seeded)" },
  classical: { name: "Classical active diagnosis", detail: "Next test by expected information gain per unit cost, over the initial fault list" },
  veyra: { name: "Veyra (prototype)", detail: "Same as classical, plus a misfit check: when no hypothesis explains the data, widen the hypothesis space" },
};

export type Step = { experiment: string; families: Record<string, number>; misfitP: number; widened: boolean; scores?: Record<string, number> };
export type Outcome = { method: MethodId; truth: string; declared: string | null; confident: boolean; correct: boolean; experiments: number; cost: number; widened: boolean; steps: Step[] };

export function chooseNext(method: MethodId, model: Model, b: Belief, used: Record<string, number>, n: number, r: Rng, left: number): { id: string; scores?: Record<string, number> } {
  const allowed = EXPERIMENTS.filter((e) => (used[e.id] || 0) < POLICY.maxRepeats && e.cost <= left);
  if (method === "fixed") {
    const id = FIXED_ORDER.find((x, i) => i >= n && allowed.some((e) => e.id === x));
    return { id: id || "" };
  }
  if (!allowed.length) return { id: "" };
  if (method === "random") return { id: allowed[Math.floor(r.next() * allowed.length)].id };
  const scores: Record<string, number> = {};
  for (const e of allowed) scores[e.id] = expectedInfoGain(model, b, e.id, r, POLICY.eigSamples) / e.cost;
  const id = allowed.reduce((a, e) => (scores[e.id] > scores[a.id] ? e : a), allowed[0]).id;
  return { id, scores };
}

export function runEpisode(model: Model, method: MethodId, truth: Params, truthFamily: string, seed: number): Outcome {
  const world = rng(seed * 7919 + 17), choice = rng(seed * 104729 + method.length);
  const obs: Observation[] = [], used: Record<string, number> = {}, steps: Step[] = [];
  let widened = false, cost = 0;
  let b = belief(model, obs, false);
  while (true) {
    const top = Math.max(...Object.values(b.families));
    if (top >= POLICY.confident) break;
    const { id, scores } = chooseNext(method, model, b, used, obs.length, choice, POLICY.budget - cost);
    if (!id) break;
    used[id] = (used[id] || 0) + 1; cost += experimentById(id).cost;
    const trace = simulate(truth, experimentById(id), world);
    obs.push({ experiment: id, y: features(trace), trace });
    b = belief(model, obs, widened);
    // misfit: how well does the best current hypothesis explain the latest observation?
    const last = obs[obs.length - 1];
    let best = -Infinity, bestI = b.active[0];
    for (const i of b.active) { const v = b.logPost[i]; if (v > best) { best = v; bestI = i; } }
    const p = chi2Tail(chi2(model.pred[last.experiment][bestI], last.y), last.y.length);
    let widenNow = false;
    if (method === "veyra" && !widened && p < POLICY.misfitP) { widened = widenNow = true; b = belief(model, obs, true); }
    steps.push({ experiment: id, families: { ...b.families }, misfitP: p, widened: widenNow, scores });
  }
  const [declared, top] = Object.entries(b.families).reduce((a, x) => (x[1] > a[1] ? x : a), ["", -1] as [string, number]);
  const confident = top >= POLICY.confident;
  return { method, truth: truthFamily, declared: confident ? declared : null, confident, correct: confident && declared === truthFamily,
           experiments: obs.length, cost, widened, steps };
}

// ---------- hidden faults ----------
export function drawFault(seed: number, families: string[]): { family: string; value: number; params: Params } {
  const r = rng(seed * 31337 + 5);
  const family = families[Math.floor(r.next() * families.length)];
  const ranges = TRUE_RANGES[family], [lo, hi] = ranges[Math.floor(r.next() * ranges.length)];
  const value = +(lo + (hi - lo) * r.next()).toFixed(4);
  const params = { ...NOMINAL }; familyById(family).apply(params, value);
  return { family, value, params };
}
export const CLOSED_WORLD = FAMILIES.filter((f) => !f.library).map((f) => f.id);
export const OPEN_WORLD = FAMILIES.filter((f) => f.library).map((f) => f.id);

// ---------- benchmark ----------
export type Summary = { method: MethodId; n: number; accuracy: number; falseCertainty: number; undetermined: number; meanExperiments: number; meanCost: number;
  ci: { accuracy: [number, number]; meanCost: [number, number] }; widenedRate: number };

export function summarise(method: MethodId, outs: Outcome[], seed = 1): Summary {
  const n = outs.length;
  const acc = (o: Outcome[]) => o.filter((x) => x.correct).length / o.length;
  const cost = (o: Outcome[]) => o.reduce((s, x) => s + x.cost, 0) / o.length;
  const r = rng(seed), bootA: number[] = [], bootC: number[] = [];
  for (let b = 0; b < 1000; b++) { const s = Array.from({ length: n }, () => outs[Math.floor(r.next() * n)]); bootA.push(acc(s)); bootC.push(cost(s)); }
  const q = (a: number[], p: number) => a.sort((x, y) => x - y)[Math.floor(p * (a.length - 1))];
  return { method, n, accuracy: acc(outs), falseCertainty: outs.filter((x) => x.confident && !x.correct).length / n,
           undetermined: outs.filter((x) => !x.confident).length / n, meanExperiments: outs.reduce((s, x) => s + x.experiments, 0) / n,
           meanCost: cost(outs), ci: { accuracy: [q(bootA, 0.025), q(bootA, 0.975)], meanCost: [q(bootC, 0.025), q(bootC, 0.975)] },
           widenedRate: outs.filter((x) => x.widened).length / n };
}

export function benchmark(model: Model, world: "closed" | "open", seeds: number[], methods: MethodId[] = ["fixed", "random", "classical", "veyra"]) {
  const fams = world === "closed" ? CLOSED_WORLD : OPEN_WORLD;
  const outcomes: Record<string, Outcome[]> = {};
  for (const m of methods) outcomes[m] = seeds.map((s) => { const f = drawFault(s, fams); return runEpisode(model, m, f.params, f.family, s); });
  return { world, seeds: [seeds[0], seeds[seeds.length - 1]], summaries: methods.map((m) => summarise(m, outcomes[m])), outcomes };
}
