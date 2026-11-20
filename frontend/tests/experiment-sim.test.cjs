// Research prototype 001: the simulator, the likelihood and the methods behave as described, and runs are reproducible.
const test = require('node:test');
const assert = require('node:assert/strict');
const S = require('../lib/experiment-sim.ts');

const model = S.buildModel();
const with_ = (patch) => ({ ...S.NOMINAL, ...patch });
const end = (p, id, seed = 0) => { const t = S.simulate(p, S.experimentById(id), seed ? S.rng(seed) : null); const k = t.t.length - 1; return { x: t.x[k], th: t.th[k], t }; };

test('same seed, same run: traces and outcomes are reproducible', () => {
  const f = S.drawFault(4242, S.CLOSED_WORLD);
  assert.deepEqual(S.simulate(f.params, S.experimentById('launch'), S.rng(1)), S.simulate(f.params, S.experimentById('launch'), S.rng(1)));
  const a = S.runEpisode(model, 'veyra', f.params, f.family, 4242), b = S.runEpisode(model, 'veyra', f.params, f.family, 4242);
  assert.deepEqual(a, b);
});

test('physics: faults only show in the conditions that excite them', () => {
  const nom = end(S.NOMINAL, 'cruise');
  assert.ok(Math.abs(nom.x - (0.4 * 0.75 + 0.4 * 1.5)) < 0.05, `cruise distance ${nom.x}`);   // ramp 1.5 s then 1.5 s at 0.4 m/s, minus latency
  const weak = with_({ wR: 12 }), grip = with_({ aL: 1.0 }), gyro = with_({ gyroBias: 0.03 });
  assert.ok(Math.abs(end(weak, 'cruise').th - nom.th) < 1e-6);                                 // 8 rad/s needed: below the weak motor's limit
  assert.ok(Math.abs(end(weak, 'topspeed').th) > 0.05);                                        // 18 rad/s needed: it saturates and the robot turns
  assert.ok(Math.abs(end(grip, 'cruise').x - nom.x) < 1e-6);                                   // gentle ramp: within traction
  assert.ok(Math.abs(end(grip, 'launch').th) > 0.01);                                          // hard launch: left wheel spins, robot yaws
  const g = S.simulate(gyro, S.experimentById('hold'), null);
  assert.ok(Math.abs(g.gyro[5] - 0.03) < 1e-9 && g.x.every((v) => v === 0));                   // standing still: only the gyro moves
});

test('likelihood is calibrated: no confident mistakes on known faults, and the truth wins with enough data', () => {
  const seeds = Array.from({ length: 40 }, (_, i) => 50001 + i);
  const outs = seeds.map((s) => { const f = S.drawFault(s, S.CLOSED_WORLD); return S.runEpisode(model, 'classical', f.params, f.family, s); });
  assert.ok(outs.filter((o) => o.confident && !o.correct).length <= 1);
  assert.ok(outs.filter((o) => o.correct).length >= 32);
  const obs = S.EXPERIMENTS.map((e) => { const tr = S.simulate(S.NOMINAL, e, S.rng(77)); return { experiment: e.id, y: S.features(tr), trace: tr }; });
  const b = S.belief(model, obs, false);
  assert.equal(Object.entries(b.families).sort((x, y) => y[1] - x[1])[0][0], 'nominal');
});

test('fair comparison: same belief and stopping rule; only the choice of test differs', () => {
  const f = S.drawFault(60001, S.CLOSED_WORLD);
  for (const m of ['fixed', 'random', 'classical', 'veyra']) {
    const o = S.runEpisode(model, m, f.params, f.family, 60001);
    assert.ok(o.cost <= S.POLICY.budget, `${m} over budget`);
    if (o.confident) assert.ok(Math.max(...Object.values(o.steps.at(-1).families)) >= S.POLICY.confident);
    if (m === 'fixed') assert.deepEqual(o.steps.map((s) => s.experiment), S.FIXED_ORDER.slice(0, o.steps.length));
  }
});

test('open world: list-only methods cannot name an unlisted fault; Veyra widens when nothing fits', () => {
  const f = S.drawFault(70001, S.OPEN_WORLD);
  const c = S.runEpisode(model, 'classical', f.params, f.family, 70001), v = S.runEpisode(model, 'veyra', f.params, f.family, 70001);
  assert.ok(!c.correct && !S.OPEN_WORLD.includes(c.declared));
  assert.ok(v.widened && v.correct);
});

test('expected information gain is non-negative and zero once the answer is certain', () => {
  const b = S.belief(model, [], false);
  for (const e of S.EXPERIMENTS) assert.ok(S.expectedInfoGain(model, b, e.id, S.rng(3), 32) >= 0);
  const sure = { ...b, families: { nominal: 1 }, active: [b.active[0]] };
  assert.equal(S.expectedInfoGain(model, sure, 'launch', S.rng(3), 16), 0);
});

test('equal knowledge: the prototype is exactly classical + detector, and knowledge never changes the first choice', () => {
  for (const s of [80001, 80002, 80003]) {
    const f = S.drawFault(s, S.CLOSED_WORLD);
    const v = S.runEpisode(model, 'veyra', f.params, f.family, s), c = S.runEpisode(model, 'classical_widen', f.params, f.family, s);
    assert.deepEqual({ ...v, method: 0 }, { ...c, method: 0 });
    for (const sel of ['fixed', 'random', 'classical']) {
      const a = S.runEpisode(model, sel, f.params, f.family, s), b = S.runEpisode(model, `${sel}_widen`, f.params, f.family, s);
      assert.equal(a.steps[0].experiment, b.steps[0].experiment);
    }
  }
});

test('robot B in the library matches the preregistered robot-b.mjs exactly', async () => {
  const B = await import('../../research/experiment-001/robot-b.mjs');
  assert.deepEqual(S.SPEC_B_WORLD, B.B_WORLD);
  assert.deepEqual(S.SPEC_B_MODEL, B.B_MODEL);
  assert.deepEqual(JSON.parse(JSON.stringify(S.FAULTS_B)), JSON.parse(JSON.stringify(B.B_FAULTS)));
});
