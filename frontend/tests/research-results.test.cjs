// The numbers on the research preview page must match the preregistered results file.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { RESULTS } = require('../app/research-preview/results.ts');
const R = JSON.parse(fs.readFileSync(path.join(__dirname, '../../research/experiment-001/transfer-results.json'), 'utf8'));
const pts = (x) => Math.round(x * 1000) / 10;

test('research preview numbers match transfer-results.json', () => {
  assert.equal(RESULTS.fingerprint, R.fingerprint);
  const n = Object.values(R.outcomes).reduce((s, w) => s + w.fixed.length, 0);
  assert.equal(RESULTS.cases, n);
  assert.equal(RESULTS.unknown.detectorA, pts(R.perMethod.A_unknown.classical_widen.accuracy.mean));
  assert.equal(RESULTS.unknown.detectorB, pts(R.perMethod.B_unknown.classical_widen.accuracy.mean));
  for (const w of ['A_unknown', 'B_unknown']) for (const m of ['fixed', 'random', 'classical']) assert.equal(R.perMethod[w][m].confidentlyWrong.mean, 1);
  const d = R.paired.A_known['classical - fixed'].accuracy;
  assert.equal(RESULTS.known.accuracyGainA, pts(d.mean));
  assert.equal(RESULTS.known.ciA, `95% CI +${pts(d.ci[0]).toFixed(1)} to +${pts(d.ci[1]).toFixed(1)} pts`);
  const wrong = Object.values(R.perMethod.B_known).map((m) => pts(m.confidentlyWrong.mean));
  assert.equal(RESULTS.transfer.wrongLow, Math.round(Math.min(...wrong)));
  assert.equal(RESULTS.transfer.wrongHigh, Math.round(Math.max(...wrong)));
});
