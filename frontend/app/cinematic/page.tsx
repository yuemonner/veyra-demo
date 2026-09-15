"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "../../lib/api";

type Comparison = {
  same_change: number;
  same_signal: number;
  no_signal: number;
  table: Array<{ context: string; affected: string; unaffected: string }>;
};

type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string; package?: any };

const INVESTIGATION_ID = "inv-120-robots-bad-rollout";
const SCENES = ["open", "deploy", "whereelse", "compare", "decision", "late", "memory", "end"];

export default function CinematicDemo() {
  const [scene, setScene] = useState(0);
  const [mode, setMode] = useState<"Presenter" | "Auto">("Presenter");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [pkg, setPkg] = useState<DecisionPackage | null>(null);
  const [memoryReady, setMemoryReady] = useState(false);

  async function reset() {
    await postJson("/demo/reset");
    setComparison(await getJson(`/investigations/${INVESTIGATION_ID}/comparison`));
    setPkg(null);
    setMemoryReady(false);
    setScene(0);
  }

  async function ensurePackage() {
    if (pkg?.sealed) return pkg;
    const next = await postJson<DecisionPackage>(`/investigations/${INVESTIGATION_ID}/decision-package`);
    await postJson(`/investigations/${INVESTIGATION_ID}/decision`, {
      decision: "Pause v0.9 on robots with calibration C and gripper firmware 7.3",
      owner: "Robotics Engineering",
      rationale: "Policy v0.9 ran everywhere, while the affected runs share calibration C and gripper firmware 7.3 with one exposed robot to watch.",
      package_id: next.id,
    });
    const sealed = await postJson<DecisionPackage>(`/decision-packages/${next.id}/seal`);
    setPkg(sealed);
    return sealed;
  }

  async function injectLateEvidence() {
    await ensurePackage();
    await postJson("/demo/late-evidence");
    setComparison(await getJson(`/investigations/${INVESTIGATION_ID}/comparison`));
  }

  async function recordOutcome() {
    await postJson(`/investigations/${INVESTIGATION_ID}/outcome`, {
      outcome: "Calibration C and gripper firmware 7.3 held; affected robots recovered after targeted rollback",
      payload: { previous_action: "Pause v0.9 on robots with calibration C and firmware 7.3", recovery_minutes: 18, days_later: 12 },
    });
    setMemoryReady(true);
  }

  async function nextScene() {
    const next = Math.min(scene + 1, SCENES.length - 1);
    if (SCENES[next] === "decision") await ensurePackage();
    if (SCENES[next] === "late") await injectLateEvidence();
    if (SCENES[next] === "memory") await recordOutcome();
    setScene(next);
  }

  function prevScene() {
    setScene((value) => Math.max(value - 1, 0));
  }

  useEffect(() => { reset().catch(() => undefined); }, []);

  useEffect(() => {
    if (mode !== "Auto") return;
    const timer = window.setTimeout(() => { nextScene().catch(() => undefined); }, scene === 0 ? 5000 : 12000);
    return () => window.clearTimeout(timer);
  }, [mode, scene, pkg]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.code === "Space" || event.code === "ArrowRight") {
        event.preventDefault();
        nextScene().catch(() => undefined);
      }
      if (event.code === "ArrowLeft") prevScene();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [scene, pkg]);

  const affected = comparison?.same_signal ?? 2;
  const healthy = comparison?.no_signal ?? 4;

  return (
    <main className={`cinematic ${mode === "Presenter" ? "presenter-mode" : ""}`}>
      <div className="cinematic-top">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <div className="mode-switch">
          {(["Presenter", "Auto"] as const).map((item) => <button key={item} className={mode === item ? "active" : ""} onClick={() => setMode(item)}>{item}</button>)}
          <Link href={`/investigations/${INVESTIGATION_ID}`}>Live</Link>
          <button onClick={reset}>Reset</button>
        </div>
      </div>

      <div className="progress"><span style={{ width: `${((scene + 1) / SCENES.length) * 100}%` }} /></div>

      {SCENES[scene] === "open" && <OpenScene onNext={nextScene} />}
      {SCENES[scene] === "deploy" && <DeployScene affected={affected} healthy={healthy} onNext={nextScene} />}
      {SCENES[scene] === "whereelse" && <WhereElseScene affected={affected} healthy={healthy} onNext={nextScene} />}
      {SCENES[scene] === "compare" && <CompareScene comparison={comparison} onNext={nextScene} />}
      {SCENES[scene] === "decision" && <DecisionScene pkg={pkg} onNext={nextScene} />}
      {SCENES[scene] === "late" && <LateScene comparison={comparison} pkg={pkg} onNext={nextScene} />}
      {SCENES[scene] === "memory" && <MemoryScene memoryReady={memoryReady} onNext={nextScene} />}
      {SCENES[scene] === "end" && <EndScene />}
    </main>
  );
}

function OpenScene({ onNext }: { onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Decision infrastructure for machines</span><h1>6 robots. One model update. 90 seconds.</h1><p>This is what Physical AI looks like before fleet scale: repeated real-world runs, constant change, fragmented context.</p><button className="button lime" onClick={onNext}>Start</button></section>;
}

function DeployScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 1 · Signal" title="Six robots run the same policy. Two should not quietly diverge." subtitle="Today, they do." /><CinematicFleet affected={affected} /><div className="cinema-events"><EventLine time="14:02:11" label="policy v0.9 test rollout started" /><EventLine time="14:04:37" label="6/6 robots updated" /><EventLine time="14:11:08" label="first known grip pose drift signal" /><EventLine time="14:18:42" label={`${affected} affected · ${healthy} stable`} /><EventLine time="14:26:03" label="engineer note recorded" /></div><HeroLine text="Same model. Same task. Different behavior." /><button className="button primary" onClick={onNext}>Where else?</button></section>;
}

function WhereElseScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 2 · Where else" title="Where else is this pattern appearing?" subtitle="The first question is scope: which robot/run combinations show the signal, and which exposed runs should be watched now?" /><CinematicFleet affected={affected} /><div className="site-grid"><div><span>Test Cell A</span><b>1 affected</b><small>2 stable</small></div><div><span>Test Cell B</span><b>1 affected</b><small>3 stable</small></div><div><span>Same calibration</span><b>2 affected</b><small>1 exposed stable</small></div><div><span>Other runs</span><b>0 affected</b><small>3 stable</small></div></div><HeroLine text={`${affected} robots show the pattern. R06 shares the same calibration and gripper firmware without a matching signal yet.`} /><button className="button primary" onClick={onNext}>Compare affected and stable</button></section>;
}

function CompareScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  const rows = useMemo(() => comparison?.table ?? [], [comparison]);
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 3 · Compare" title="Same model. Same task. Why do only two robots diverge?" subtitle="This is the question that matters." /><div className="split-count"><div><strong>{comparison?.same_signal ?? 2}</strong><span>affected</span></div><div><strong>{comparison?.no_signal ?? 4}</strong><span>stable</span></div></div><table className="table cinema-table"><thead><tr><th>Context</th><th>Affected</th><th>Stable</th></tr></thead><tbody>{rows.map((row) => <tr className={row.context === "Camera calibration C" || row.context === "Gripper firmware 7.3" ? "highlight-row" : ""} key={row.context}><td>{row.context}</td><td>{row.affected}</td><td>{row.unaffected}</td></tr>)}</tbody></table><HeroLine text="Calibration C and gripper firmware 7.3 co-occur across the affected runs." /><p className="scene-footnote">R06 shares the same combination without a known signal at decision time. Low-light bin is present in 3/6 runs, but did not differentiate affected vs stable at decision time. This narrows the investigation; it does not establish cause.</p><button className="button primary" onClick={onNext}>Generate Decision Package</button></section>;
}

function DecisionScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 4 · Decision Record" title="A decision made during one run should not be rewritable after the next log arrives." subtitle="This package locks what the team knew, who approved action, and what must be checked later." /><div className="decision-card"><div><span>Observed</span><b>2 robots affected</b></div><div><span>Observed</span><b>policy v0.9 ran on 6</b></div><div><span>Observed</span><b>engineer note at 14:26</b></div><div><span>Inferred</span><b>calibration C and firmware 7.3 co-occur across affected runs</b></div><div><span>Human asserted</span><b>engineer suspects calibration mismatch</b></div><div><span>Substantiation</span><b>{pkg?.package?.decision_substantiation?.status || "incomplete"}</b></div><div><span>Human identity</span><b>{pkg?.package?.human_decision?.identity || "human:robotics-engineering"}</b></div><div><span>Human decision</span><b>Pause v0.9 on robots with calibration C + firmware 7.3</b></div><div><span>Outcome</span><b>pending follow-up</b></div></div><p className="scene-footnote">First-generation tools make incident reconstruction obvious. Veyra continues into what the team did, what happened afterward, and what the next decision should inherit.</p><div className="seal-card"><span>Sealed Decision Package · 14:27</span><b>{pkg?.digest ? pkg.digest.slice(0, 28) + "..." : "SHA-256 pending"}</b><small>Hash recorded at 14:27:00 · tamper-evident · independent timestamp</small><small>Human owner · action scope · standalone verification</small></div><button className="button primary" onClick={onNext}>Inject delayed evidence</button></section>;
}

function LateScene({ comparison, pkg, onNext }: { comparison: Comparison | null; pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 5 · Decision-time integrity" title="Late evidence reveals R06 had already shown the signal at 14:09." subtitle="This happened before the decision. The team learned about it after." /><p className="evidence-detail">Late evidence: R06 post-run telemetry was re-analyzed at 14:31; grip pose drift was present at 14:09:11.</p><div className="time-rail cinematic-rail"><div><b>14:09</b><span>event_time</span><small>post-run telemetry shows drift</small></div><i /><div><b>14:27</b><span>decision sealed</span><small>2 affected in the package</small></div><i /><div><b>14:31</b><span>known_at</span><small>late evidence becomes knowable</small></div><i /><div><b>14:31:04</b><span>ingested_at</span><small>received by Veyra</small></div></div><div className="late-grid"><div><span>Decision-time view</span><b>2 affected</b></div><div><span>Current view</span><b>{Math.max(comparison?.same_signal ?? 3, 3)} affected</b></div><div><span>Sealed package</span><b>{pkg?.sealed ? "unchanged" : "decision-time snapshot"}</b></div></div><HeroLine text="Veyra updates what we know now, not what the team knew then." /><button className="button primary" onClick={onNext}>12 days later</button></section>;
}

function MemoryScene({ memoryReady, onNext }: { memoryReady: boolean; onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Scene 6 · Decision Precedent</span><h1>12 days later, the next run starts with precedent.</h1><div className="memory-card"><p><b>Decision precedent found</b></p><p>Similar change pattern: policy update + calibration C + grip pose drift.</p><p>Previous evidence state: one calibration/firmware subset diverged while similarly updated robots stayed stable.</p><p>Previous human action: pause v0.9 on robots with calibration C and firmware 7.3.</p><p>Previous outcome: recovered after targeted rollback.</p><p>Missing last time: targeted low-light validation runs.</p><p>Different this time: low-light bin now coincides with the policy-update window.</p></div><HeroLine text="The first decision creates the evidence base for the next one." /><div className="time-saved"><b>The first run took 42 minutes.</b><b>The next run took 42 seconds.</b></div><div className="future-strip"><span>What this unlocks</span><b>Better experiments · Faster pilot review · Decision memory</b></div><button className="button lime" onClick={onNext}>{memoryReady ? "End" : "End"}</button></section>;
}

function EndScene() {
  return <section className="cinema-scene center end-frame"><h1>Today: six robots in a lab. Tomorrow: cranes, robot cells and autonomous machines in production.</h1><p>Veyra becomes the decision system of record for Physical AI.</p><div className="market-proof"><span>Validated wedge, broader scope</span><b>Incident reconstruction → decision + outcome intelligence</b><small>Already validated beyond robotics-native tooling through a connected smart-crane workflow.</small></div><div className="unlock-row"><div><span>Proven motion</span><b>What changed / where else</b></div><div><span>Broader machines</span><b>Cranes · robot cells · smart equipment</b></div><div><span>Next abstraction</span><b>What did we do, and did it work?</b></div><div><span>Governance</span><b>What future autonomy inherits</b></div></div><Link className="button lime" href={`/investigations/${INVESTIGATION_ID}`}>Open live system</Link></section>;
}

function CinematicFleet({ affected }: { affected: number }) {
  return <div className="cinema-fleet">{Array.from({ length: 6 }).map((_, index) => <span key={index} className={index < affected ? "robot-dot affected" : "robot-dot"} />)}</div>;
}

function SceneTitle({ eyebrow, title, subtitle }: { eyebrow: string; title: string; subtitle: string }) {
  return <header className="scene-title"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></header>;
}

function EventLine({ time, label }: { time: string; label: string }) {
  return <div className="event-line"><time>{time}</time><span>{label}</span></div>;
}

function HeroLine({ text }: { text: string }) {
  return <div className="hero-line">{text}</div>;
}
