// Robot B, fixed in the preregistration (PREREGISTRATION.md). Do not edit after the held-out run.
import * as S from "../../frontend/lib/experiment-sim.ts";

// The world: a heavier robot with bigger wheels, a wider track, slower motors, noisier sensors and more latency.
export const B_WORLD = {
  robot: { ...S.ROBOT, wheelRadius: 0.06, track: 0.38, motorTau: 0.14 },
  noise: { pose: 0.04, heading: 0.04, encoder: 0.8, gyro: 0.04 },
  nominal: { ...S.NOMINAL, wL: 26, wR: 26, delay: 0.05 },
};
// What the diagnostic model is told: the spec sheet (wheel radius, track, top wheel speed). Motor lag, sensor noise and
// nominal latency are NOT re-identified; the model keeps robot A's assumptions for them. No method is retuned.
export const B_MODEL = {
  robot: { ...S.ROBOT, wheelRadius: 0.06, track: 0.38 },
  noise: S.NOISE,
  nominal: { ...S.NOMINAL, wL: 26, wR: 26 },
};
// Hidden faults on robot B: same fault types, ranges in robot B's terms, and a different mix (prevalence).
export const B_FAULTS = {
  nominal: B_WORLD.nominal,
  ranges: { ...S.TRUE_RANGES, weak_left: [[8.5, 13.5]], weak_right: [[8.5, 13.5]], gyro_bias: [[-0.06, -0.012], [0.012, 0.06]], delay: [[0.1, 0.3]] },
  prevalence: { nominal: 0.1, slip_left: 0.15, slip_right: 0.15, weak_left: 0.05, weak_right: 0.05, encoder_left: 0.1, encoder_right: 0.1,
                gyro_bias: 0.15, delay: 0.15, intermittent_left: 0.5, intermittent_right: 0.5 },
};
