// Evidence acquisition in the downloadable report: what the data answered, what only a person can answer, and the re-test spec.
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../lib/case-report.ts');

test('the report carries answered questions, field questions and the re-test conditions', () => {
  const html = R.buildReport({ title: 'Shuttle', caseId: 'c1', status: 'cause_probable', headline: 'h',
    nextAction: { action: 'Repeat the cold launch with HSA off', where: 'on_site', conditions: 'cell temperature below 5 °C, SOC about 38',
      baseline: 'the 10/09 morning launch', record: ['Pack_I', 'MCU_FR.DC_V'], needs_approval: true, outcomes: [] },
    alreadyAnswered: [{ question: 'What was the state of charge?', answer: 'SOC 38', line: 1376, quote: 'SOC=38' }],
    fieldQuestions: [{ ask: 'field_engineer', question: 'Was the FR terminal re-torqued?', why_needed: 'splits harness from VCU', not_in_data: 'no torque signal',
      if_answers: [{ answer: 'yes', means: 'harness weakened' }, { answer: 'no', means: 'harness stays open' }] }],
    unobservable: [{ fact: 'Connector seating depth', how_to_observe: 'inspect the FR plug' }] });
  for (const s of ['Before asking the field', 'Answered from the data', 'What was the state of charge?', 'Was the FR terminal re-torqued?',
    'Connector seating depth', 'Conditions', 'cell temperature below 5', 'Pack_I, MCU_FR.DC_V', 'Needs an engineer'])
    assert.ok(html.includes(s.replace(/'/g, '&#39;')) || html.includes(s), s);
  const json = JSON.parse(html.match(/<script type="application\/json" id="veyra-case">([\s\S]*?)<\/script>/)[1]);
  assert.equal(json.field_questions.length, 1);
  assert.equal(json.already_answered[0].line, 1376);
});
