// Numbers shown on the research preview, copied from research/experiment-001/transfer-results.json
// (preregistered run, fingerprint below). tests/research-results.test.cjs checks they still match that file.
export const RESULTS = {
  fingerprint: "38a5950e96b95e75",
  cases: 800,
  unknown: { detectorA: 97, detectorB: 89 },                 // classical + detector, correct on unlisted faults (robot A, robot B)
  known: { accuracyGainA: 2.7, ciA: "95% CI +0.7 to +5.0 pts" }, // classical vs best fixed checklist, robot A, paired
  transfer: { wrongLow: 61, wrongHigh: 74 },                 // robot B known faults, confidently wrong, range across methods
};
