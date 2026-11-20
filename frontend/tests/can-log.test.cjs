// Decoded CAN log (synthetic shuttle case): snapshots, the comparison table and the chart come straight from the lines.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const CL = require('../lib/can-log.ts');
const S = require('../lib/svg-chart.ts');

const lines = fs.readFileSync(path.join(__dirname, '../../fixtures/synthetic/demo/shuttle-hill-start-stop/log.txt'), 'utf8').split('\n');
const micro = fs.readFileSync(path.join(__dirname, 'fixtures/microros-log.txt'), 'utf8').split('\n');

test('recognises a decoded CAN log, and not a micro-ROS agent log', () => {
  assert.equal(CL.looksLikeCanLog(lines), true);
  assert.equal(CL.looksLikeCanLog(micro), false);
});

test('three launches: only the cold launch on VCU 2.4.1 stops, and its chain is in order', () => {
  const log = CL.parseCanLog(lines);
  assert.equal(log.snapshots.length, 3);
  const f = log.snapshots.map(CL.facts);
  assert.deepEqual(f.map((x) => x.stopped), [false, true, false]);
  const a = f[1];
  assert.equal(a.vcu, '2.4.1'); assert.equal(a.hsa, true); assert.equal(a.dcl, 150);
  assert.ok(a.peakI > a.dcl, 'current above DCL'); assert.ok(a.minFrV < 55, 'FR below the 55 V threshold'); assert.ok(a.overLimit > 30, 'TqCmd_Out above TqLim_BMS');
  for (const ok of [f[0], f[2]]) { assert.ok(ok.minFrV > 55); assert.ok(ok.overLimit <= 0.5); assert.equal(ok.dtc, false); }
  assert.equal(f[0].hsa, false); assert.equal(f[0].vcu, '2.3.8'); // cold, old software: fine
  assert.ok(f[2].tempC > 10); // new software, warm: fine
  for (const x of f) assert.ok(x.frMinusFl > 0.5, 'FR reads lower than FL under load in every launch');
  const ev = log.snapshots[1].events.map((e) => e.t);
  assert.deepEqual([...ev].sort((p, q) => p - q), ev);
});

test('the stacked chart draws every panel and numbers every event', () => {
  const s = CL.parseCanLog(lines).snapshots[1];
  const rel = (p) => (p || []).map((q) => ({ t: q.t - s.t0, v: q.v }));
  const [x0, x1] = CL.focus(s);
  assert.ok(x1 - x0 >= 3 && x1 - x0 < 6);
  const svg = S.stackedChart([{ title: 'I', unit: 'A', lines: [{ label: 'Pack_I', color: '#000', pts: rel(s.series['BMS_Status.Pack_I']) }] },
    { title: 'V', unit: 'V', lines: [{ label: 'MCU_FR DC_V', color: '#000', pts: rel(s.series['MCU_FR.DC_V']) }], hlines: [{ y: 55, label: '55 V', color: '#f00' }] }],
    { x0, x1, markers: s.events.map((e) => ({ t: e.t - s.t0, label: e.msg, color: '#f00' })) });
  assert.match(svg, /^<svg /);
  assert.equal((svg.match(/<polyline/g) || []).length, 2);
  assert.equal((svg.match(/<circle/g) || []).length, 2 * s.events.length);
});
