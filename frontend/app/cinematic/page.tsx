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
const SCENES = ["open", "deploy", "whereelse", "compare", "decision", "late", "outcome", "memory", "end"];

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
    if (SCENES[next] === "outcome") await recordOutcome();
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
      {SCENES[scene] === "outcome" && <OutcomeScene onNext={nextScene} />}
      {SCENES[scene] === "memory" && <MemoryScene memoryReady={memoryReady} onNext={nextScene} />}
      {SCENES[scene] === "end" && <EndScene />}
    </main>
  );
}

function OpenScene({ onNext }: { onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Operational intelligence for Physical AI</span><h1>6 robots. One model update. 90 seconds.</h1><p>Veyra reconstructs what changed, what the team did, and what happened next.</p><button className="button lime" onClick={onNext}>Start</button></section>;
}

function DeployScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 1 · Signal" title="Six robots run the same policy. Two begin to diverge." subtitle="The team needs to understand the split before the next action." /><CinematicFleet affected={affected} /><div className="cinema-events"><EventLine time="14:02:11" label="policy v0.9 test rollout started" /><EventLine time="14:04:37" label="6/6 robots updated" /><EventLine time="14:11:08" label="first known grip pose drift signal" /><EventLine time="14:18:42" label={`${affected} affected · ${healthy} stable`} /><EventLine time="14:26:03" label="engineer note recorded" /></div><HeroLine text="Same model. Same task. Different behavior." /><button className="button primary" onClick={onNext}>Where else?</button></section>;
}

function WhereElseScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 2 · Where else" title="Where else is this pattern appearing?" subtitle="The first question is scope: which robot/run combinations show the signal, and which exposed runs should be watched now?" /><CinematicFleet affected={affected} /><div className="site-grid"><div><span>Test Cell A</span><b>1 affected</b><small>2 stable</small></div><div><span>Test Cell B</span><b>1 affected</b><small>3 stable</small></div><div><span>Same calibration</span><b>2 affected</b><small>1 exposed stable</small></div><div><span>Other runs</span><b>0 affected</b><small>3 stable</small></div></div><HeroLine text={`${affected} robots show the pattern. R06 shares the same calibration and gripper firmware without a matching signal yet.`} /><button className="button primary" onClick={onNext}>Compare affected and stable</button></section>;
}

function CompareScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  const rows = useMemo(() => comparison?.table ?? [], [comparison]);
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 3 · Decision-time compare" title="Same model. Same task. Why do only two robots diverge?" subtitle="This is the comparison available before late evidence arrives." /><div className="split-count"><div><strong>{comparison?.same_signal ?? 2}</strong><span>affected</span></div><div><strong>{comparison?.no_signal ?? 4}</strong><span>stable</span></div></div><table className="table cinema-table"><thead><tr><th>Context</th><th>Affected</th><th>Stable</th></tr></thead><tbody>{rows.map((row) => <tr className={row.context === "Camera calibration C" || row.context === "Gripper firmware 7.3" ? "highlight-row" : ""} key={row.context}><td>{row.context}</td><td>{row.affected}</td><td>{row.unaffected}</td></tr>)}</tbody></table><HeroLine text="Calibration C and gripper firmware 7.3 co-occur across the affected runs." /><p className="scene-footnote">R06 shares the same combination and appeared stable at decision time. Low-light bin was ambient in the decision-time comparison. This narrows the investigation; root cause remains open.</p><button className="button primary" onClick={onNext}>Generate Decision Package</button></section>;
}

function DecisionScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 4 · Decision" title="The team pauses the exposed subset." subtitle="The action is based on what was known at 14:27." /><div className="decision-card"><div><span>Evidence available</span><b>2 affected robots</b></div><div><span>Evidence available</span><b>policy v0.9 ran on 6</b></div><div><span>Evidence available</span><b>R06 exposed, no known signal</b></div><div><span>Pattern</span><b>calibration C + firmware 7.3</b></div><div><span>Open question</span><b>low-light relevance</b></div><div><span>Open question</span><b>rollback outcome</b></div><div><span>Team action</span><b>Pause v0.9 on exposed robots</b></div><div><span>Follow-up</span><b>watch R06</b></div><div><span>State saved</span><b>14:27</b></div></div><p className="scene-footnote">The package preserves evidence, open questions, action scope and follow-up.</p><div className="seal-card quiet-seal"><span>Decision state saved · 14:27</span><small>Hash and verification available in details.</small></div><button className="button primary" onClick={onNext}>Inject delayed evidence</button></section>;
}

function LateScene({ comparison, pkg, onNext }: { comparison: Comparison | null; pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 5 · Decision-time integrity" title="Late evidence reveals R06 had already shown the signal at 14:09." subtitle="This happened before the decision. The team learned about it after." /><p className="evidence-detail">Late evidence: R06 post-run telemetry was re-analyzed at 14:31; grip pose drift was present at 14:09:11.</p><div className="time-rail cinematic-rail"><div><b>14:09</b><span>event_time</span><small>post-run telemetry shows drift</small></div><i /><div><b>14:27</b><span>decision saved</span><small>2 affected in the package</small></div><i /><div><b>14:31</b><span>known_at</span><small>late evidence becomes knowable</small></div><i /><div><b>14:31:04</b><span>ingested_at</span><small>received by Veyra</small></div></div><div className="late-grid"><div><span>Decision-time view</span><b>2 affected</b></div><div><span>Current view after late evidence</span><b>{Math.max(comparison?.same_signal ?? 3, 3)} affected</b></div><div><span>Saved decision state</span><b>{pkg?.sealed ? "unchanged" : "preserved"}</b></div></div><HeroLine text="Veyra updates what we know now, not what the team knew then." /><button className="button primary" onClick={onNext}>Show outcome</button></section>;
}

function OutcomeScene({ onNext }: { onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Scene 6 · Outcome</span><h1>The team paused the exposed subset. What happened next?</h1><div className="outcome-grid"><div><span>R03</span><b>recovered after rollback</b></div><div><span>R05</span><b>recovered after rollback</b></div><div><span>R06</span><b>later confirmed affected</b></div><div><span>Follow-up window</span><b>no recurrence on reverted robots</b></div></div><HeroLine text="Veyra links the action to what actually happened." /><button className="button primary" onClick={onNext}>12 days later</button></section>;
}

function MemoryScene({ memoryReady, onNext }: { memoryReady: boolean; onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Scene 7 · Similar previous case</span><h1>A similar pattern appears. The investigation starts with what worked before.</h1><div className="memory-card"><p><b>Similar previous case</b></p><p>Matched context: policy update + calibration C + grip pose drift.</p><p>Previous action: pause v0.9 on robots with calibration C and firmware 7.3.</p><p>Previous outcome: recovered after targeted rollback.</p><p>Missing last time: targeted low-light validation runs.</p><p>Different this time: low-light bin now coincides with the policy-update window.</p></div><HeroLine text="Every operational decision makes the next one smarter." /><div className="future-strip"><span>Demo scenario</span><b>First-time reconstruction becomes precedent lookup.</b></div><button className="button lime" onClick={onNext}>{memoryReady ? "End" : "End"}</button></section>;
}

function EndScene() {
  return <section className="cinema-scene center end-frame"><h1>Today: six robots in a lab. Tomorrow: cranes, robot cells and autonomous machines in production.</h1><p>Veyra becomes the operational intelligence system for Physical AI.</p><div className="market-proof"><span>Validated wedge, broader scope</span><b>Incident reconstruction → action → outcome → next decision</b><small>Validated beyond robotics-native tooling through a connected smart-crane workflow.</small></div><div className="unlock-row"><div><span>Proven wedge</span><b>What changed · Where else</b></div><div><span>Broader scope</span><b>Robots · cranes · industrial machines</b></div><div><span>Compounding intelligence</span><b>Action → outcome → next decision</b></div></div><Link className="button lime" href={`/investigations/${INVESTIGATION_ID}`}>Open live system</Link></section>;
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
