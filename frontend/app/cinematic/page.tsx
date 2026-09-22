"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getJson, postJson } from "../../lib/api";

type Comparison = {
  same_change: number;
  same_signal: number;
  no_signal: number;
  table: Array<{ context: string; affected: string; unaffected: string }>;
};

type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string; package?: any };

const INVESTIGATION_ID = "inv-120-robots-bad-rollout";
const SCENES = ["open", "signal", "changed", "whereelse", "decision", "action", "outcome", "memory", "end"];

export default function CinematicDemo() {
  return (
    <Suspense fallback={<main className="cinematic"><section className="cinema-scene center"><h1>Loading demo...</h1></section></main>}>
      <CinematicDemoInner />
    </Suspense>
  );
}

function CinematicDemoInner() {
  const presenter = useSearchParams().get("presenter") === "1";
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

  async function ensureDecisionState() {
    if (pkg?.sealed) return pkg;
    const next = await postJson<DecisionPackage>(`/investigations/${INVESTIGATION_ID}/decision-package`);
    await postJson(`/investigations/${INVESTIGATION_ID}/decision`, {
      decision: "Remote recovery on affected machines and hold field dispatch",
      owner: "Operations Lead",
      rationale: "EX03, EX05 and EX08 are affected after the same deployment. EX11 shares localization and loading-zone exposure and needs monitoring.",
      package_id: next.id,
    });
    const sealed = await postJson<DecisionPackage>(`/decision-packages/${next.id}/seal`);
    setPkg(sealed);
    return sealed;
  }

  async function injectLateEvidence() {
    await ensureDecisionState();
    await postJson("/demo/late-evidence");
    setComparison(await getJson(`/investigations/${INVESTIGATION_ID}/comparison`));
  }

  async function recordOutcome() {
    await postJson(`/investigations/${INVESTIGATION_ID}/outcome`, {
      outcome: "EX03, EX05 and EX08 returned to service after remote recovery; EX11 later confirmed affected; field visit was avoided",
      payload: {
        previous_action: "Remote recovery on affected machines; hold field dispatch; monitor EX11",
        recovery_minutes: 41,
        days_later: 12,
        attribution_level: "observed",
        attribution_rationale: "Return-to-service was observed after remote recovery. Remote recovery is not treated as proven causal.",
      },
    });
    setMemoryReady(true);
  }

  async function nextScene() {
    const next = Math.min(scene + 1, SCENES.length - 1);
    if (SCENES[next] === "decision") await ensureDecisionState();
    if (SCENES[next] === "outcome") {
      await injectLateEvidence();
      await recordOutcome();
    }
    setScene(next);
  }

  function prevScene() {
    setScene((value) => Math.max(value - 1, 0));
  }

  useEffect(() => { reset().catch(() => undefined); }, []);

  useEffect(() => {
    if (mode !== "Auto") return;
    const timer = window.setTimeout(() => { nextScene().catch(() => undefined); }, scene === 0 ? 5000 : 11500);
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

  const affected = comparison?.same_signal ?? 3;
  const healthy = comparison?.no_signal ?? 9;

  return (
    <main className={`cinematic ${mode === "Presenter" ? "presenter-mode" : ""}`}>
      {presenter && <div className="cinematic-top">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <div className="mode-switch">
          {(["Presenter", "Auto"] as const).map((item) => <button key={item} className={mode === item ? "active" : ""} onClick={() => setMode(item)}>{item}</button>)}
          <Link href="/product-demo">Open Product Demo</Link>
          <button onClick={reset}>Reset</button>
        </div>
      </div>}

      <div className="progress"><span style={{ width: `${((scene + 1) / SCENES.length) * 100}%` }} /></div>

      {SCENES[scene] === "open" && <OpenScene onNext={nextScene} />}
      {SCENES[scene] === "signal" && <SignalScene affected={affected} healthy={healthy} onNext={nextScene} />}
      {SCENES[scene] === "changed" && <ChangedScene onNext={nextScene} />}
      {SCENES[scene] === "whereelse" && <WhereElseScene comparison={comparison} onNext={nextScene} />}
      {SCENES[scene] === "decision" && <DecisionStateScene pkg={pkg} onNext={nextScene} />}
      {SCENES[scene] === "action" && <ActionScene onNext={nextScene} />}
      {SCENES[scene] === "outcome" && <OutcomeScene comparison={comparison} onNext={nextScene} />}
      {SCENES[scene] === "memory" && <MemoryScene memoryReady={memoryReady} onNext={nextScene} />}
      {SCENES[scene] === "end" && <EndScene />}
    </main>
  );
}

function OpenScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene center">
      <span className="eyebrow">Field Case Replay · Physical AI</span>
      <h1>One deployment. Twelve machines. Three enter safe-stop.</h1>
      <p>Veyra reconstructs what changed, shows where else the same conditions exist, and follows the case through action and observed outcome.</p>
      <button className="button lime" onClick={onNext}>Start case</button>
    </section>
  );
}

function SignalScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="1 · Signal" title="Three excavators entered safe-stop after the same deployment." subtitle="The team needs to understand what changed before deciding whether to recover remotely, roll back, or dispatch someone onsite." />
      <CinematicFleet affected={affected} />
      <div className="failure-counter" aria-label="failure burst">
        <span>Pattern detected</span>
        <b><i>0</i><i>1</i><i>2</i><i>3</i> affected</b>
      </div>
      <div className="cinema-events">
        <EventLine time="14:02" label="autonomy release 2.7 deployment begins" />
        <EventLine time="14:04" label="12/12 machines updated" />
        <EventLine time="14:11" label="first safe-stop on EX03" />
        <EventLine time="14:18" label="EX05 shows the same behavior" />
        <EventLine time="14:26" label="EX08 enters safe-stop" />
      </div>
      <HeroLine text={`${affected} unhealthy · ${healthy} healthy · same release`} />
      <button className="button primary" onClick={onNext}>What changed?</button>
    </section>
  );
}

function ChangedScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="2 · What changed?" title="What changed before the safe-stops?" subtitle="The system reconstructs autonomy release, localization config, map version, machine state and operator observations around the case." />
      <div className="scan-card">
        <span className="eyebrow">Scanning deployment history</span>
        <div className="decision-card change-assembly">
          <div><span>Autonomy stack</span><b>2.6 → 2.7</b></div>
          <div className="signal-change"><span>Localization</span><b>L3 → L4</b></div>
          <div><span>LiDAR firmware</span><b>5.2 → 5.3</b></div>
          <div><span>Map version</span><b>M18 → M19</b></div>
          <div className="signal-change"><span>Machine state</span><b>safe-stop near loading zone B</b></div>
          <div><span>Human context</span><b>operator review at 14:31</b></div>
        </div>
      </div>
      <HeroLine text="Three relevant changes occurred before the first known failure. Cause is not yet established." />
      <button className="button primary" onClick={onNext}>Scope the issue</button>
    </section>
  );
}

function WhereElseScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  const rows = useMemo(() => comparison?.table ?? [], [comparison]);
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="3 · Scope" title="Which machines share the same exposure?" subtitle="The case moves from one issue to operational scope." />
      <div className="site-grid">
        <div><span>Current group</span><b>12 machines</b><small>same autonomy release</small></div>
        <div><span>Same release</span><b>12 machines</b><small>autonomy 2.7</small></div>
        <div><span>Same exposure</span><b>4 machines</b><small>localization L4 · zone B</small></div>
        <div><span>Currently affected</span><b>{comparison?.same_signal ?? 3}</b><small>EX03 · EX05 · EX08</small></div>
      </div>
      <CohortMotion />
      <table className="table cinema-table">
        <thead><tr><th>Context</th><th>Affected</th><th>Healthy</th></tr></thead>
        <tbody>{rows.map((row) => <tr className={row.context === "Localization profile L4" || row.context === "Loading zone B" ? "highlight-row" : ""} key={row.context}><td>{row.context}</td><td>{row.affected}</td><td>{row.unaffected}</td></tr>)}</tbody>
      </table>
      <p className="scene-footnote">Localization L4 and loading zone B are shared by the affected machines, while EX11 has the same exposure without a known issue at decision time. The exposure is relevant. It is not sufficient to explain the failure.</p>
      <button className="button primary" onClick={onNext}>Decision</button>
    </section>
  );
}

function DecisionStateScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="4 · Decision" title="What did the team actually know at 14:27?" subtitle="The case separates known evidence from open questions before the team acts." />
      <div className="split-count">
        <div><strong>What the team knew at 14:27</strong><span>EX03, EX05 and EX08 affected · no known issue on EX11</span></div>
        <div><strong>What Veyra knows now</strong><span>EX11 had earlier safe-stop behavior that became available after the decision snapshot</span></div>
      </div>
      <div className="time-rail cinematic-rail">
        <div><b>14:09</b><span>event_time</span><small>EX11 safe-stop behavior existed in edge buffer</small></div>
        <i />
        <div><b>14:27</b><span>decision snapshot</span><small>3 affected known to the team</small></div>
        <i />
        <div><b>14:31</b><span>known_at</span><small>evidence becomes available to reconstruction</small></div>
        <i />
        <div><b>14:31:04</b><span>ingested_at</span><small>evidence reaches Veyra</small></div>
      </div>
      <HeroLine text="Veyra preserves what the team knew when the decision was made." />
      <div className="seal-card quiet-seal lock-motion"><span>Decision snapshot · 14:27</span><small>{pkg?.sealed ? "Sealed after owner and action were recorded." : "Generated from backend evidence."}</small></div>
      <button className="button primary" onClick={onNext}>What did the team do?</button>
    </section>
  );
}

function ActionScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="5 · Team action" title="The team chooses a response." subtitle="Veyra keeps the decision, the executed action and the evidence available at the time together." />
      <div className="decision-card">
        <div><span>Decision</span><b>Remote recovery before dispatch</b></div>
        <div><span>Executed</span><b>Remote recovery on EX03, EX05 and EX08</b></div>
        <div><span>Watch</span><b>Monitor EX11</b></div>
        <div><span>Operations</span><b>Notify support</b></div>
        <div><span>Field</span><b>Hold dispatch</b></div>
        <div><span>Owner</span><b>Operations Lead · 14:31</b></div>
      </div>
      <HeroLine text="The decision and the executed action stay linked." />
      <button className="button primary" onClick={onNext}>What happened after?</button>
    </section>
  );
}

function OutcomeScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="6 · Outcome" title="What happened after the action?" subtitle="A few hours later, and then three days later, the case keeps updating." />
      <div className="outcome-grid">
        <div><span>EX03</span><b>returned to service after remote recovery</b></div>
        <div><span>EX05</span><b>returned to service after remote recovery</b></div>
        <div><span>Attribution</span><b>observed after action, not causal proof</b></div>
        <div><span>Field visit</span><b>held by decision path</b></div>
        <div><span>Follow-up window</span><b>clean for 24 hours</b></div>
        <div><span>Rollout</span><b>paused, then resumed</b></div>
        <div><span>EX08</span><b>returned to service after remote recovery</b></div>
        <div><span>+3 days</span><b>EX11 shows the same pattern</b></div>
        <div><span>Current population</span><b>{Math.max(comparison?.same_signal ?? 3, 3)} affected</b></div>
      </div>
      <div className="late-evidence-motion">
        <div className="late-packet"><span>event_time 14:09</span><b>EX11 safe-stop</b><small>arrived 14:31</small></div>
        <div className="late-arrow" />
        <div className="late-result"><span>Current view</span><b>3 → 4 affected</b><small>Decision snapshot remains 3</small></div>
      </div>
      <HeroLine text="New evidence updates the case without rewriting the original decision snapshot." />
      <button className="button primary" onClick={onNext}>12 days later</button>
    </section>
  );
}

function MemoryScene({ memoryReady, onNext }: { memoryReady: boolean; onNext: () => void }) {
  return (
    <section className="cinema-scene center">
      <span className="eyebrow">7 · Twelve days later</span>
      <h1>A similar pattern appears again.</h1>
      <div className="memory-card">
        <p><b>Previous case</b></p>
        <p>Same autonomy release.</p>
        <p>Same localization L4 and loading zone B exposure.</p>
        <p>EX03, EX05 and EX08 returned to service after remote recovery.</p>
        <p>No field visit was required.</p>
        <p>One additional exposed machine failed later.</p>
        <p>Evidence strength: precedent, not causal proof.</p>
        <p>Previous action available: remote recovery, hold dispatch and monitor exposed machines.</p>
      </div>
      <div className="precedent-match">
        <div className="case-node"><span>Previous case</span><b>Autonomy 2.7 · EX03/05/08</b></div>
        <div className="match-lines">
          <span>Localization L4</span>
          <span>Zone B</span>
          <span>Safe-stop</span>
          <em>2.7 ≠ 2.8</em>
        </div>
        <div className="case-node new"><span>Current case</span><b>Autonomy 2.8 · EX21</b></div>
      </div>
      <HeroLine text="The next case does not start from zero." />
      <div className="future-strip"><span>{memoryReady ? "Operational Case" : "Case preview"}</span><b>Evidence → decision state → action → outcome → reusable learning</b></div>
      <button className="button lime" onClick={onNext}>End</button>
    </section>
  );
}

function EndScene() {
  return (
    <section className="cinema-scene center end-frame">
      <h1>A company should not start from zero when a similar machine problem appears again.</h1>
      <p>Veyra turns each machine decision into evidence for the next one.</p>
      <p>Now run the same scenario in the Product Demo.</p>
      <p>Operational intelligence for Physical AI.</p>
      <div className="unlock-row">
        <div><span>See</span><b>what changed</b></div>
        <div><span>Decide</span><b>what to do</b></div>
        <div><span>Learn</span><b>what happened after</b></div>
      </div>
      <Link className="button lime" href="/product-demo">Open Product Demo</Link>
    </section>
  );
}

function CinematicFleet({ affected }: { affected: number }) {
  const affectedIds = new Set([2, 4, 7]);
  return <div className="cinema-fleet">{Array.from({ length: 12 }).map((_, index) => <span key={index} style={{ animationDelay: `${index * 35}ms` }} className={affectedIds.has(index) ? "robot-dot affected" : "robot-dot"}><small>EX{String(index + 1).padStart(2, "0")}</small></span>)}</div>;
}

function CohortMotion() {
  return (
    <div className="cohort-motion" aria-label="fleet cohort split">
      <div className="cohort-group affected-group">
        <span>Safe-stop</span>
        {[3, 5, 8].map((id) => <b key={id}>EX{String(id).padStart(2, "0")}</b>)}
      </div>
      <div className="filter-stack">
        <i>Autonomy 2.7</i>
        <i>L4</i>
        <i>Zone B</i>
      </div>
      <div className="cohort-group healthy-group">
        <span>Updated and stable</span>
        {[1, 2, 4, 6, 7, 9, 10, 12].map((id) => <b key={id}>EX{String(id).padStart(2, "0")}</b>)}
        <b className="watch">EX11 watch</b>
      </div>
    </div>
  );
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
