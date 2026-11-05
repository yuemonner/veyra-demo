// A closed case becomes one row of the quality team's table: main cause, triggers and amplifiers kept apart, with evidence.
const test = require('node:test');
const assert = require('node:assert/strict');
const Q = require('../lib/quality-row.ts');

const ladder = { level: 'probable', case: { created_at: '2026-10-28T07:50:00+00:00', machine_id: 'SHUTTLE-03', reported: 'Emergency stop on the ramp, code 7' } };
const result = { explanation: 'x', next_action: { action: 'Repeat the cold launch with HSA off' }, evidence: [{ line: 1416, shows: 'command above limit' }],
  hypotheses: [{ area: 'VCU HSA', claim: 'HSA added after the limit', status: 'leading', role: 'main_cause' },
    { area: 'Cold battery', claim: 'low DCL', status: 'leading', role: 'trigger' },
    { area: 'FR harness', claim: 'extra drop', status: 'open', role: 'amplifier' },
    { area: 'FR controller', claim: 'faulty', status: 'ruled_out', role: 'main_cause' }] };

test('main cause, triggers and amplifiers land in their own columns; ruled-out areas are left out', () => {
  const { cols, row } = Q.qualityRow(ladder, result, true, 'https://demo/x');
  assert.equal(cols[3], '主因');
  assert.equal(row[3], 'VCU HSA: HSA added after the limit');
  assert.match(row[4], /诱因 · Cold battery/); assert.match(row[4], /放大 · FR harness/);
  assert.ok(!row.join(' ').includes('FR controller'));
  assert.equal(row[6], '可能原因'); assert.equal(row[7], 'L1416: command above limit');
  const out = Q.csv(cols, [row]);
  assert.ok(out.startsWith('﻿')); assert.equal(out.split('\r\n').length, 2);
  assert.equal(Q.qualityRow(ladder, result, false, '').cols[3], 'Main cause');
});
