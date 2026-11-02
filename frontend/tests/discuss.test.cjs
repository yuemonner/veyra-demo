// Plots, tables, normal behaviour, the discussion and the HTML report: every number comes from the case's own data.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const X = require('../lib/xrce-log.ts');
const K = require('../lib/run-knowledge.ts');
const R = require('../lib/case-report.ts');
const S = require('../lib/svg-chart.ts');

const lines = fs.readFileSync(path.join(__dirname, 'fixtures/microros-log.txt'), 'utf8').split('\n').filter(Boolean);
const log = X.parseAgentLog(lines);
const runs = require('./fixtures/runs.json');

test('micro-ROS log: every packet decoded from the bytes printed in the log', () => {
  assert.equal(lines.length, 34);
  assert.equal(log.packets.length, 13);
  const [create, reply, zero, err] = log.packets;
  assert.equal(create.xrce.create.clientKey, '0x2A2ED975');
  assert.equal(create.xrce.create.session, 0x81);
  assert.equal(reply.xrce.status.name, 'OK');
  assert.ok(zero.allZero && zero.len === 36 && zero.line === 10 && !zero.xrce.wellFormed);
  assert.equal(err.xrce.status.name, 'ERR_INVALID_DATA');
  assert.deepEqual(X.asInt32(log.packets[4].bytes), [1000, 1000000000, 119000000]);
  assert.equal(log.packets[12].complete, false, 'the last packet is cut off at the end of the log');
  assert.deepEqual(log.port, { requested: 457879, used: 64663, wrapped: true });
  assert.equal(log.sessions[0].openLine, 7); assert.equal(log.sessions[0].closeLine, 14);
});

test('a healthy session against this log: the first failure is the first message after the handshake', () => {
  const ex = X.expectations(log);
  assert.equal(ex[0].ok, true);
  assert.equal(ex[1].ok, false);
  assert.deepEqual(ex[1].lines, [10]);
  assert.ok(ex.filter((e) => e.ok === false).length >= 3);
});

test('the discussion routes a proposed cause to the check that tests it, and cites real lines', () => {
  const cases = { 'I think a bad switch corrupted it': ['network', 'against'], 'is the agent the problem?': ['agent', 'against'],
    'did port 457879 break it': ['port', 'against'], 'did something time out': ['timing', 'against'], 'what is the 13 byte packet': ['repeat', 'facts'],
    'is it the d-cache on the H7': ['buffer', 'cannot_tell'], 'is the 36 byte packet really zeros': ['zero', 'supports'] };
  for (const [q, [id, verdict]] of Object.entries(cases)) {
    const a = X.answer(log, q);
    assert.ok(a.length >= 1, q);
    assert.equal(a[0].check, id, q);
    assert.equal(a[0].verdict, verdict, q);
    for (const n of a[0].lines) assert.ok(n >= 1 && n <= lines.length, `${q}: line ${n}`);
  }
  assert.deepEqual(X.answer(log, 'banana'), []);
  for (const c of X.CHECKS) assert.ok(X.route(c.ask).some((r) => r.id === c.id), `suggestion routes to its own check: ${c.ask}`);
});

test('normal behaviour: a recording is judged against the machine\'s own healthy data', () => {
  const real = K.axes(runs['case-ca747f15bad8']), inj = K.axes(runs['case-fb178a6862d9']), alfa = K.axes(runs['case-a0942333eb32']);
  assert.equal(real.filter((r) => r.status === 'unusual').length, 0, 'the real ANYmal mission has no departure');
  assert.deepEqual(inj.filter((r) => r.status === 'unusual').map((r) => r.axis), ['LF_KFE'], 'only the injected knee departs');
  assert.deepEqual(alfa.filter((r) => r.status === 'unusual').map((r) => r.axis), ['roll']);
  const g = K.treatedAsNormal(runs['case-ca747f15bad8']);
  assert.equal(g[0].count, 60);
  assert.match(g[0].reason, /standing up or lying down/);
  const spikes = K.answerRun(runs['case-ca747f15bad8'], 'are the spikes just noise or contact?');
  assert.equal(spikes[0].check, 'noise'); assert.equal(spikes[0].verdict, 'against');
  assert.equal(K.answerRun(runs['case-fb178a6862d9'], 'is the left front knee ok')[0].verdict, 'supports');
  assert.equal(K.answerRun(runs['case-ca747f15bad8'], 'is the left front knee ok')[0].verdict, 'against');
  for (const run of Object.values(runs)) for (const q of K.RUN_SUGGESTIONS(run)) assert.ok(K.answerRun(run, q).length, `suggestion answers: ${q}`);
});

test('the HTML report is self-contained and carries the case as JSON for an AI assistant', () => {
  const html = R.buildReport({ title: 'micro-ROS <test>', caseId: 'case-x', headline: 'Zero packet', hypotheses: [{ id: 'h1', area: 'Network path', status: 'ruled_out', claim: 'c', because: 'b' }],
    feedback: [{ id: 'h1', area: 'Network path', verdict: 'ruled_out', note: 'direct cable, same result', at: '2026-11-02T00:00:00Z' }],
    data: [{ title: 'Packets', svg: S.packetTimeline(log.packets.map((p) => ({ ...p, bad: !p.xrce.wellFormed, zero: p.allZero }))), table: X.packetTable(log) }],
    discussion: [{ at: 'x', who: 'engineer', text: '</script><b>x</b>' }], log: lines });
  assert.ok(!/<script(?! type="application\/json")/.test(html), 'no executable script');
  assert.ok(!/src="http|href="http/.test(html.replace(/<a href="[^"]*">open the live case<\/a>/, '')), 'nothing fetched');
  assert.ok(html.includes('micro-ROS &lt;test&gt;'));
  const json = JSON.parse(html.match(/<script type="application\/json" id="veyra-case">([\s\S]*?)<\/script>/)[1]);
  assert.equal(json.schema, 'veyra-case-report-v1');
  assert.equal(json.hypotheses[0].engineer_feedback.note, 'direct cable, same result');
  assert.equal(json.log_lines.length, 34);
  assert.equal(json.discussion[0].text, '</script><b>x</b>');
});
