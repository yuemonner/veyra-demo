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
    <Suspense fallback={<main className="cinematic"><section className="cinema-scene center"><h1>Loading operational case...</h1></section></main>}>
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
      decision: "Remote restart affected machines and hold field dispatch",
      owner: "Operations Lead",
      rationale: "R03 and R05 are affected after the same deployment. R06 shares the same firmware, profile and site network exposure and needs monitoring.",
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
      outcome: "R03 and R05 recovered after remote restart; R06 later confirmed affected; field visit was avoided",
      payload: {
        previous_action: "Remote restart affected machines; hold field dispatch; monitor R06",
        recovery_minutes: 41,
        days_later: 12,
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

  const affected = comparison?.same_signal ?? 2;
  const healthy = comparison?.no_signal ?? 4;

  return (
    <main className={`cinematic ${mode === "Presenter" ? "presenter-mode" : ""}`}>
      {presenter && <div className="cinematic-top">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <div className="mode-switch">
          {(["Presenter", "Auto"] as const).map((item) => <button key={item} className={mode === item ? "active" : ""} onClick={() => setMode(item)}>{item}</button>)}
          <Link href="/product-demo">Live product</Link>
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
      <span className="eyebrow">Operational Case · Physical AI</span>
      <h1>One deployment. Seven machines. Two start dropping offline.</h1>
      <p>Veyra reconstructs what changed, shows where else the same conditions exist, and follows the case through action and outcome.</p>
      <button className="button lime" onClick={onNext}>Start case</button>
    </section>
  );
}

function SignalScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="1 · Signal" title="Two machines started dropping offline after the same deployment." subtitle="The team needs to understand what changed before deciding whether to fix remotely or dispatch someone onsite." />
      <CinematicFleet affected={affected} />
      <div className="cinema-events">
        <EventLine time="14:02" label="application 0.36 deployment begins" />
        <EventLine time="14:04" label="7/7 machines updated" />
        <EventLine time="14:11" label="first known signal on R03" />
        <EventLine time="14:18" label="R05 shows the same signal" />
        <EventLine time="14:26" label="engineer note recorded" />
      </div>
      <HeroLine text={`${affected} unhealthy · ${healthy} healthy · same release`} />
      <button className="button primary" onClick={onNext}>What changed?</button>
    </section>
  );
}

function ChangedScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="2 · What changed?" title="What changed around the connectivity failure?" subtitle="The system reconstructs machine state, software changes, configuration and human observations around the case." />
      <div className="decision-card">
        <div><span>Application</span><b>app 0.35 → 0.36</b></div>
        <div><span>Device profile</span><b>C16 → C17</b></div>
        <div><span>Module firmware</span><b>4.8 → 4.9</b></div>
        <div><span>Machine state</span><b>module unhealthy · reconnect failures</b></div>
        <div><span>Human context</span><b>engineer note at 14:26</b></div>
        <div><span>Site context</span><b>firewall state unconfirmed</b></div>
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
        <div><span>Current group</span><b>7 machines</b><small>same application release</small></div>
        <div><span>Same application</span><b>7 machines</b><small>app 0.36</small></div>
        <div><span>Same exposed profile</span><b>3 machines</b><small>profile C17 · firmware 4.9</small></div>
        <div><span>Currently affected</span><b>{comparison?.same_signal ?? 2}</b><small>R03 · R05</small></div>
      </div>
      <table className="table cinema-table">
        <thead><tr><th>Context</th><th>Affected</th><th>Healthy</th></tr></thead>
        <tbody>{rows.map((row) => <tr className={row.context === "Device profile C17" || row.context === "Module firmware 4.9" || row.context === "Site network profile N7" ? "highlight-row" : ""} key={row.context}><td>{row.context}</td><td>{row.affected}</td><td>{row.unaffected}</td></tr>)}</tbody>
      </table>
      <p className="scene-footnote">Profile C17, module firmware 4.9 and site network N7 are shared by both affected machines, while R06 has the same exposure without a known issue at decision time. The exposure is relevant. It is not sufficient to explain the failure.</p>
      <button className="button primary" onClick={onNext}>Decision</button>
    </section>
  );
}

function DecisionStateScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="4 · Decision" title="What did the team actually know at 14:27?" subtitle="The case separates known evidence from open questions before the team acts." />
      <div className="split-count">
        <div><strong>What the team knew at 14:27</strong><span>R03 affected · R05 affected · R06 no known issue</span></div>
        <div><strong>What Veyra knows now</strong><span>R06 had earlier reconnect failures that became available later</span></div>
      </div>
      <div className="time-rail cinematic-rail">
        <div><b>14:09</b><span>event_time</span><small>R06 reconnect failures existed in edge buffer</small></div>
        <i />
        <div><b>14:27</b><span>decision point</span><small>2 affected known to the team</small></div>
        <i />
        <div><b>14:31</b><span>known_at</span><small>delayed evidence becomes knowable</small></div>
        <i />
        <div><b>14:31:04</b><span>ingested_at</span><small>evidence reaches Veyra</small></div>
      </div>
      <HeroLine text="Veyra preserves what the team knew when the decision was made." />
      <div className="seal-card quiet-seal"><span>Decision package saved · 14:27</span><small>{pkg?.sealed ? "Operational case ready for action and outcome follow-up." : "Decision package generated from backend evidence."}</small></div>
      <button className="button primary" onClick={onNext}>What did the team do?</button>
    </section>
  );
}

function ActionScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="5 · Team action" title="The team chooses a response." subtitle="Veyra keeps the decision, the executed action and the evidence available at the time together." />
      <div className="decision-card">
        <div><span>Decision</span><b>Remote fix before dispatch</b></div>
        <div><span>Executed</span><b>Remote restart R03 and R05</b></div>
        <div><span>Watch</span><b>Monitor R06</b></div>
        <div><span>Customer</span><b>Notify support</b></div>
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
      <SceneTitle eyebrow="6 · Outcome" title="Did the action work?" subtitle="A few hours later, and then three days later, the case keeps updating." />
      <div className="outcome-grid">
        <div><span>R03</span><b>recovered after remote restart</b></div>
        <div><span>R05</span><b>recovered after remote restart</b></div>
        <div><span>Field visit</span><b>avoided</b></div>
        <div><span>Follow-up window</span><b>clean for 24 hours</b></div>
        <div><span>Rollout</span><b>paused, then resumed</b></div>
        <div><span>Escalation</span><b>contained for R03/R05</b></div>
        <div><span>+3 days</span><b>R06 shows the same pattern</b></div>
        <div><span>Current population</span><b>{Math.max(comparison?.same_signal ?? 3, 3)} affected</b></div>
      </div>
      <HeroLine text="New evidence updates the case without rewriting the original decision." />
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
        <p>Same application release.</p>
        <p>Same profile C17 and module firmware 4.9 exposure.</p>
        <p>Remote restart restored 2/2 affected machines.</p>
        <p>No field visit was required.</p>
        <p>One additional exposed machine failed later.</p>
        <p>Previous action available: remote restart, hold dispatch and monitor exposed machines.</p>
      </div>
      <HeroLine text="The company is no longer solving the same problem from zero." />
      <div className="future-strip"><span>{memoryReady ? "Operational Case" : "Case preview"}</span><b>Evidence → decision state → action → outcome → reusable learning</b></div>
      <button className="button lime" onClick={onNext}>End</button>
    </section>
  );
}

function EndScene() {
  return (
    <section className="cinema-scene center end-frame">
      <h1>A company should not solve the same machine problem twice.</h1>
      <p>Veyra turns each operational case into evidence for the next one.</p>
      <p>Now run the same scenario in the live product.</p>
      <p>Operational intelligence for Physical AI.</p>
      <div className="unlock-row">
        <div><span>See</span><b>what changed</b></div>
        <div><span>Decide</span><b>what to do</b></div>
        <div><span>Learn</span><b>whether it worked</b></div>
      </div>
      <Link className="button lime" href="/product-demo">Open live product</Link>
    </section>
  );
}

function CinematicFleet({ affected }: { affected: number }) {
  return <div className="cinema-fleet">{Array.from({ length: 7 }).map((_, index) => <span key={index} className={index < affected ? "robot-dot affected" : "robot-dot"} />)}</div>;
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
