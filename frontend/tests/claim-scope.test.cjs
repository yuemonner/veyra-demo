// Claim scope: nothing shown as observed may say what the board transmitted; an agent-side log cannot see it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const G = require('../lib/claim-scope.ts');
const X = require('../lib/xrce-log.ts');
const S = require('../lib/svg-chart.ts');

const dir = path.join(__dirname, '../../fixtures/real/public/microros-stm32-session-drops');
const recorded = fs.readdirSync(dir).filter((f) => /^recorded_investigation.*\.json$/.test(f))
  .flatMap((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')).entries.filter((e) => e.payload?.result && typeof e.payload.result === 'object').map((e) => [f, e.payload]));
const lines = fs.readFileSync(path.join(__dirname, 'fixtures/microros-log.txt'), 'utf8').split('\n').filter(Boolean);
const log = X.parseAgentLog(lines);

test('every recorded investigation, guarded: no observed statement claims the board transmitted', () => {
  assert.ok(recorded.length >= 6);
  for (const [f, inv] of recorded) {
    const g = G.guardInvestigation(inv);
    const r = g.result;
    const observed = [r.headline_kind === 'observed' ? r.headline : '', ...(r.evidence || []).filter((e) => e.kind === 'observed').map((e) => e.shows),
      ...(r.checked || []).filter((c) => c.kind === 'observed').map((c) => c.result), ...(r.events || []).map((e) => e.what)];
    for (const s of observed) assert.ok(!G.OUT_OF_SCOPE.test(s || ''), `${f}: ${s}`);
    assert.deepEqual(g.scope.residual, [], f);
    for (const w of g.scope.rewrites) assert.notEqual(w.before, w.after);
  }
});

test('the live production headline is rewritten to what the agent observed', () => {
  const v3 = recorded.filter(([f]) => f.endsWith('v3.json')).map(([, inv]) => inv);
  const g = G.guardInvestigation(v3[0]);
  assert.match(g.result.headline, /^Session opened correctly, then the agent received a 36-byte all-zero packet/);
  assert.equal(g.result.headline_kind, 'observed');
  assert.ok(g.scope.rewrites.some((w) => w.field === 'headline' && /board sent/.test(w.before)));
  // hypotheses keep their claims (they are hypotheses) and are labelled so
  assert.ok(g.result.hypotheses.every((h) => h.kind === 'hypothesis'));
  // the next action is an instruction, not a claim, and recorded outcome labels are untouched
  assert.deepEqual(g.result.next_action, v3[0].result.next_action);
});

test('statement kinds: observed, inferred, engineer report', () => {
  assert.equal(G.kindOf('The agent received 36 bytes, all zero (line 10).'), 'observed');
  assert.equal(G.kindOf('So the port did not cause the failure.'), 'inferred');
  assert.equal(G.kindOf('tcpdump on the agent host shows the datagram as zeros'), 'reported');
});

test('Veyra\'s own answers, tables and charts say what the agent received and sent', () => {
  const texts = [];
  for (const c of X.CHECKS) { const a = c.run(log); if (!a) continue; texts.push(a.title, a.text, ...a.points.filter((p) => p.k === 'observed').map((p) => p.t));
    for (const p of a.points) assert.ok(['observed', 'inferred', 'lead'].includes(p.k)); if (a.table) texts.push(...a.table.cols, ...a.table.rows.flat().map(String)); }
  for (const e of X.expectations(log)) texts.push(e.observed);
  texts.push(...X.packetTable(log).cols, ...X.packetTable(log).rows.flat().map(String));
  const svg = S.packetTimeline(log.packets.map((p) => ({ ...p, bad: !p.xrce.wellFormed, zero: p.allZero })));
  texts.push(svg);
  for (const s of texts) assert.ok(!/board → agent|agent → board|\bboard (sent|sends|keeps sending|kept sending)\b|\bthe board identified\b/i.test(s), s.slice(0, 160));
});
