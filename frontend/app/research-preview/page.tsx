"use client";
// Research preview: Veyra diagnosing a simulated robot it has never seen, by choosing its own experiments.
// Every episode is computed live in the browser by lib/experiment-sim.ts (the same engine as the preregistered
// benchmark in research/experiment-001). The page animates the steps the engine actually took; nothing is scripted.
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import * as S from "../../lib/experiment-sim";
import { FaultSpace } from "../field-case/Investigation";
import st from "../field-case/investigation.module.css";
import r from "./research.module.css";
import { RESULTS } from "./results";

type RobotId = "A" | "B";
type World = "known" | "unknown";
type Episode = { seed: number; robot: RobotId; world: World; truth: { family: string; value: number }; veyra: S.Outcome; checklist: S.Outcome; listOnly: S.Outcome };

const SPECS = {
  A: { model: S.SPEC_A, world: S.SPEC_A, faults: S.FAULTS_A, name: "Robot A", detail: "the robot the method was developed on" },
  B: { model: S.SPEC_B_MODEL, world: S.SPEC_B_WORLD, faults: S.FAULTS_B, name: "Robot B", detail: "heavier, slower motors, noisier sensors; only its spec sheet is known" },
};
const pct = (p: number) => (p >= 0.995 ? ">99" : p < 0.005 ? "<1" : Math.round(p * 100).toString());
const statusOf = (p: number) => (p >= 0.5 ? "leading" : p >= 0.1 ? "open" : p >= 0.005 ? "weakened" : "ruled_out");
const famName = (id: string) => S.familyById(id).name;
const valueText = (family: string, v: number) => ({
  nominal: "", slip_left: `traction limit ${v.toFixed(2)} m/s²`, slip_right: `traction limit ${v.toFixed(2)} m/s²`,
  weak_left: `top speed ${v.toFixed(1)} rad/s`, weak_right: `top speed ${v.toFixed(1)} rad/s`, encoder_left: `scale ${v.toFixed(3)}`,
  encoder_right: `scale ${v.toFixed(3)}`, gyro_bias: `${v.toFixed(3)} rad/s`, delay: `${(v * 1000).toFixed(0)} ms`,
  intermittent_left: `cuts out ${(v * 100).toFixed(0)}% of the time`, intermittent_right: `cuts out ${(v * 100).toFixed(0)}% of the time`,
} as Record<string, string>)[family] || "";

function hypsFor(families: Record<string, number>) {
  return Object.entries(families).sort((a, b) => b[1] - a[1]).map(([id, p]) => ({
    id, area: famName(id), status: statusOf(p), claim: `${pct(p)}% likely`, because: "", lines: [],
  }));
}

/** One test, top-down: the path each leading explanation predicts, and the end position actually measured.
 *  (Like a field engineer, the simulated robot's position is measured once, at the end of each test.) */
function TestPlot({ name, observed, predicted }: { name: string; observed: S.Trace; predicted: { label: string; trace: S.Trace }[] }) {
  const end = observed.t.length - 1, mx = observed.x[end], my = observed.y[end];
  const xs = [mx, ...predicted.flatMap((p) => p.trace.x)], ys = [my, ...predicted.flatMap((p) => p.trace.y)];
  const pad = 0.1, minX = Math.min(0, ...xs) - pad, maxX = Math.max(0.2, ...xs) + pad, minY = Math.min(-0.12, ...ys) - pad, maxY = Math.max(0.12, ...ys) + pad;
  const W = 220, H = 130, k = Math.min(W / (maxX - minX), H / (maxY - minY));
  const px = (x: number) => (x - minX) * k + 6, py = (y: number) => H - (y - minY) * k + 6;
  const path = (t: S.Trace) => "M" + px(0).toFixed(1) + " " + py(0).toFixed(1) + t.x.map((x, i) => `L${px(x).toFixed(1)} ${py(t.y[i]).toFixed(1)}`).join("");
  const turned = (observed.th[end] * 180) / Math.PI;
  return (
    <figure className={r.plot}>
      <svg viewBox={`0 0 ${W + 12} ${H + 12}`} role="img" aria-label={`${name}: predicted paths and measured end position`}>
        <circle cx={px(0)} cy={py(0)} r={3.5} className={r.start} />
        {predicted.map((p, i) => <path key={p.label} d={path(p.trace)} className={i === 0 ? r.predLead : r.pred} />)}
        <g className={r.measured}><line x1={px(mx) - 5} y1={py(my) - 5} x2={px(mx) + 5} y2={py(my) + 5} /><line x1={px(mx) - 5} y1={py(my) + 5} x2={px(mx) + 5} y2={py(my) - 5} /></g>
      </svg>
      <figcaption><b>{name}</b> · measured turn {turned.toFixed(1)}°
        {predicted.map((p, i) => <span key={p.label} className={i === 0 ? r.capLead : r.capAlt}> · {i === 0 ? "dashed" : "dotted"}: {p.label}</span>)}</figcaption>
    </figure>
  );
}

export default function ResearchPreview() {
  const [robot, setRobot] = useState<RobotId>("A");
  const [world, setWorld] = useState<World>("known");
  const [seed, setSeed] = useState(100417);
  const [models, setModels] = useState<Partial<Record<RobotId, S.Model>>>({});
  const [ep, setEp] = useState<Episode | null>(null);
  const [k, setK] = useState(0);
  const [busy, setBusy] = useState("Preparing the simulator: about 32,000 simulated test runs to learn what each fault looks like…");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Build the robot's diagnostic model lazily (a few seconds), off the first paint.
  useEffect(() => {
    if (models[robot]) return;
    setBusy(`Preparing ${SPECS[robot].name}: about 32,000 simulated test runs to learn what each fault looks like…`);
    const id = setTimeout(() => setModels((m) => ({ ...m, [robot]: S.buildModel(SPECS[robot].model) })), 60);
    return () => clearTimeout(id);
  }, [robot, models]);

  // Run the episode for this robot, world and seed: Veyra, the best checklist (same detector), and list-only reasoning.
  useEffect(() => {
    const model = models[robot];
    if (!model) return;
    setBusy("Veyra is choosing experiments…");
    const id = setTimeout(() => {
      const spec = SPECS[robot], fams = world === "known" ? S.CLOSED_WORLD : S.OPEN_WORLD;
      const f = S.drawFault(seed, fams, spec.faults);
      const run = (m: S.MethodId) => S.runEpisode(model, m, f.params, f.family, seed, spec.world);
      setEp({ seed, robot, world, truth: { family: f.family, value: f.value }, veyra: run("veyra"), checklist: run("fixed_widen"), listOnly: run("classical") });
      setK(0); setBusy("");
    }, 30);
    return () => clearTimeout(id);
  }, [models, robot, world, seed]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const n = ep?.veyra.steps.length ?? 0;
  const play = () => {
    if (!ep) return;
    const tick = (i: number) => { setK(i); if (i < n) timer.current = setTimeout(() => tick(i + 1), 1400); };
    if (timer.current) clearTimeout(timer.current);
    tick(k >= n ? 0 : k + 1);
  };
  const shuffle = () => { if (timer.current) clearTimeout(timer.current); setSeed(Math.floor(Math.random() * 900000) + 1000); };

  const model = models[robot];
  const view = useMemo(() => {
    if (!ep || !model) return null;
    const steps = ep.veyra.steps;
    const prior = S.belief(model, [], false).families;
    const families = k === 0 ? prior : steps[k - 1].families;
    const prevFamilies = k <= 1 ? (k === 1 ? prior : null) : steps[k - 2].families;
    const widened = steps.slice(0, k).some((s) => s.widened);
    const obs = steps.slice(0, k).map((s) => ({ experiment: s.experiment, y: S.features(s.trace!), trace: s.trace! }));
    const b = S.belief(model, obs, widened);
    const best = S.bestPerFamily(model, b);
    const lead = Object.entries(families).sort((a, c) => c[1] - a[1]).slice(0, 2).map(([id]) => id);
    const plots = steps.slice(0, k).map((s) => ({
      name: S.experimentById(s.experiment).name, observed: s.trace!,
      predicted: lead.filter((id) => best[id]).map((id) => ({ label: famName(id), trace: S.simulate(best[id].params, S.experimentById(s.experiment), null, SPECS[ep.robot].model) })),
    }));
    return { families, prevFamilies, widenedNow: k > 0 && steps[k - 1].widened, misfitP: k > 0 ? steps[k - 1].misfitP : 1, next: k < n ? steps[k] : null, plots };
  }, [ep, model, k, n]);

  const costSoFar = ep ? ep.veyra.steps.slice(0, k).reduce((s, x) => s + S.experimentById(x.experiment).cost, 0) : 0;
  const done = ep && k >= n;

  return (
    <div className={r.page}>
      <header className={r.topbar}>
        <Link href="/field-case" className={r.brand}><span className={r.mark}>V</span>Veyra</Link>
        <label className={r.pick}>
          <span className={r.hidden}>Demo</span>
          <select value="research" onChange={(e) => { if (e.target.value === "field") window.location.href = "/field-case"; }}>
            <option value="field">Real data · micro-ROS on STM32H7: investigation</option>
            <option value="research">Research preview · Diagnose a robot Veyra has never seen</option>
          </select>
        </label>
        <span className={r.badge}>Research preview · live simulation</span>
      </header>

      <main className={r.main}>
        <section className={r.hero}>
          <p className={r.kicker}>Silken Reason research · experiment 001</p>
          <h1>Every intelligent model eventually meets a machine it wasn’t trained for.</h1>
          <p className={r.lede}>
            Here Veyra meets a simulated two-wheeled robot with a hidden fault. It chooses its own experiments, predicts what each
            explanation would produce, runs the test, and updates what it believes. Nothing is scripted: every run below is computed in
            your browser.
          </p>
        </section>

        <section className={r.controls} aria-label="Scenario">
          <div className={r.seg} role="group" aria-label="Robot">
            {(["A", "B"] as RobotId[]).map((x) => (
              <button key={x} aria-pressed={robot === x} className={robot === x ? r.on : ""} onClick={() => setRobot(x)}>
                {SPECS[x].name}<small>{x === "A" ? "known robot" : "unfamiliar robot"}</small></button>))}
          </div>
          <div className={r.seg} role="group" aria-label="Fault">
            {(["known", "unknown"] as World[]).map((x) => (
              <button key={x} aria-pressed={world === x} className={world === x ? r.on : ""} onClick={() => setWorld(x)}>
                {x === "known" ? "Fault on its list" : "Fault nobody listed"}<small>{x === "known" ? "9 candidate causes" : "a command cutting out"}</small></button>))}
          </div>
          <button className={r.ghost} onClick={shuffle}>Shuffle hidden fault</button>
          <button className={r.primary} onClick={play} disabled={!ep || !!busy}>{k === 0 ? "Run Veyra" : done ? "Replay" : "Continue"}</button>
          {ep && <span className={r.caseId}>Case #{ep.seed} · {SPECS[ep.robot].detail}</span>}
        </section>

        {busy && <p className={r.busy}>{busy}</p>}

        {ep && view && !busy && (
          <div className={r.grid}>
            <div className={r.col}>
              {view.widenedNow && (
                <p className={r.alert}>None of the listed causes explains this test (misfit p = {view.misfitP.toExponential(0)}). Veyra widens its search
                  to fault types that were not on its list.</p>)}
              <FaultSpace hyps={hypsFor(view.families)} prev={view.prevFamilies ? hypsFor(view.prevFamilies) : null} changedLead="This test changed the picture" />
              {done && (
                <div className={ep.veyra.correct ? r.verdictOk : ep.veyra.confident ? r.verdictBad : r.verdictOpen}>
                  <b>{ep.veyra.confident ? `Answer: ${famName(ep.veyra.declared!)}` : "No answer: not 95% sure within the test budget"}</b>
                  <span>Hidden fault: {famName(ep.truth.family)}{valueText(ep.truth.family, ep.truth.value) ? ` (${valueText(ep.truth.family, ep.truth.value)})` : ""}
                    {" · "}{ep.veyra.correct ? "correct" : ep.veyra.confident ? "confidently wrong" : "abstained"} · test cost {ep.veyra.cost}</span>
                </div>)}
            </div>

            <div className={r.col}>
              <div className={st.block}>
                <div className={st.spaceHead}><h2 className={st.h2}>{view.next ? "Next experiment" : "Experiments run"}</h2>
                  <span className={st.dim}>cost {costSoFar} of {S.POLICY.budget}</span></div>
                {view.next?.scores && (
                  <ul className={r.bars}>
                    {S.EXPERIMENTS.map((e) => {
                      const v = view.next!.scores![e.id], max = Math.max(...Object.values(view.next!.scores!), 1e-9);
                      const chosen = e.id === view.next!.experiment;
                      return (
                        <li key={e.id} className={chosen ? r.chosen : ""}>
                          <span>{e.name} <small>cost {e.cost}</small></span>
                          <span className={r.bar}><i style={{ width: `${v === undefined ? 0 : Math.max(2, (v / max) * 100)}%` }} /></span>
                          <span className={r.num}>{v === undefined ? "used" : `${v.toFixed(2)} bits/cost`}</span>
                        </li>);
                    })}
                  </ul>)}
                {view.next && <p className={r.why}>Veyra runs <b>{S.experimentById(view.next.experiment).name.toLowerCase()}</b>: of the tests it can still afford, it is expected to remove the most uncertainty about the cause, per unit of cost.</p>}
                {view.plots.length > 0 && <div className={r.plots}>{view.plots.map((p, i) => <TestPlot key={i} {...p} />)}</div>}
              </div>

              {done && (
                <div className={r.compare}>
                  <h2 className={st.h2}>Same robot, same hidden fault, other methods</h2>
                  <table>
                    <tbody>
                      {[["Veyra", ep.veyra], ["Best hand-tuned checklist (same detector)", ep.checklist], ["Same reasoning without mismatch detection", ep.listOnly]].map(([label, o]) => {
                        const x = o as S.Outcome;
                        return (
                          <tr key={label as string}>
                            <th>{label as string}</th>
                            <td>{x.confident ? famName(x.declared!) : "no answer"}</td>
                            <td className={x.correct ? r.ok : x.confident ? r.bad : r.open}>{x.correct ? "correct" : x.confident ? "confidently wrong" : "abstained"}</td>
                            <td>cost {x.cost}</td>
                          </tr>);
                      })}
                    </tbody>
                  </table>
                </div>)}
            </div>
          </div>
        )}

        <section className={r.findings}>
          <p className={r.kicker}>What {RESULTS.cases} preregistered simulated cases showed</p>
          <h2>Bold where it held up. Honest where it didn’t.</h2>
          <div className={r.cards}>
            <article>
              <p className={r.big}>0% → {RESULTS.unknown.detectorB}–{RESULTS.unknown.detectorA}%</p>
              <h3>It knows when its list is wrong.</h3>
              <p>On faults nobody listed, every list-only method was confidently wrong in 100% of cases. With mismatch detection, the right
                cause was found in {RESULTS.unknown.detectorA}% (robot A) and {RESULTS.unknown.detectorB}% (unfamiliar robot B). It held on both robots.</p>
            </article>
            <article>
              <p className={r.big}>+{RESULTS.known.accuracyGainA} pts</p>
              <h3>It matches the best expert checklist, without needing one.</h3>
              <p>On the robot it was developed on, choosing experiments by information gain was slightly more accurate than the best of all
                120 hand orderings ({RESULTS.known.ciA}), at the same test cost. The checklist needed 200 labelled faults to tune.</p>
            </article>
            <article>
              <p className={r.big}>{RESULTS.transfer.wrongLow}–{RESULTS.transfer.wrongHigh}%</p>
              <h3>Unfamiliar machines need their normal learned first.</h3>
              <p>On robot B every method, Veyra included, mistook the new robot’s normal behaviour for a fault and was confidently wrong in
                {" "}{RESULTS.transfer.wrongLow}–{RESULTS.transfer.wrongHigh}% of cases. That is the problem our next research program attacks: learn how a machine
                normally behaves, then diagnose it.</p>
            </article>
          </div>
          <p className={r.fine}>Simulation, one robot family. Protocol and held-out test preregistered before running; every run, failure and paired
            confidence interval is kept (fingerprint {RESULTS.fingerprint}). The fault library used when widening is pre-written today; having the AI
            propose new fault types is the research step.</p>
        </section>
      </main>
    </div>
  );
}
