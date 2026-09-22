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
const SCENE_DURATIONS = [5200, 10500, 10500, 13000, 13500, 10000, 13000, 12000, 9000];

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

  async function replay() {
    await reset();
    setMode("Auto");
    setScene(1);
  }

  useEffect(() => { reset().catch(() => undefined); }, []);

  useEffect(() => {
    if (mode !== "Auto") return;
    if (scene === SCENES.length - 1) return;
    const timer = window.setTimeout(() => { nextScene().catch(() => undefined); }, SCENE_DURATIONS[scene] || 10000);
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
      <div className={presenter ? "cinematic-top" : "cinematic-top demo-visible"}>
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <div className="mode-switch">
          <button className={mode === "Auto" ? "active" : ""} onClick={replay}>Auto replay</button>
          <button className={mode === "Presenter" ? "active" : ""} onClick={() => setMode("Presenter")}>Manual</button>
          <button onClick={() => nextScene().catch(() => undefined)}>Next</button>
          <Link href="/product-demo">Open Product Demo</Link>
          <button onClick={reset}>Reset</button>
        </div>
      </div>

      <div className="progress"><span style={{ width: `${((scene + 1) / SCENES.length) * 100}%` }} /></div>

      {SCENES[scene] === "open" && <OpenScene onNext={nextScene} onReplay={replay} />}
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

function OpenScene({ onNext, onReplay }: { onNext: () => void; onReplay: () => void }) {
  return (
    <section className="cinema-scene center">
      <span className="eyebrow">Field Case Replay · Physical AI</span>
      <h1>One deployment. Twelve machines. Three enter safe-stop.</h1>
      <p>Veyra shows what changed, where else it is happening, what the team did, and what happened after.</p>
      <div className="hero-actions">
        <button className="button lime" onClick={onReplay}>Replay field case</button>
        <button className="button" onClick={onNext}>Step through manually</button>
      </div>
    </section>
  );
}

function SignalScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="1 · Signal" title="Three excavators entered safe-stop after the same deployment." subtitle="The team needs to know what changed before choosing remote recovery, rollback, or a field visit." />
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
      <SceneTitle eyebrow="2 · What changed?" title="What changed before the safe-stops?" subtitle="Veyra pulls together the release, localization, map, machine state and operator note." />
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
      <HeroLine text="Several things changed before the first known failure. Cause is not proven yet." />
      <button className="button primary" onClick={onNext}>Where else?</button>
    </section>
  );
}

function WhereElseScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  const rows = useMemo(() => comparison?.table ?? [], [comparison]);
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="3 · Where else?" title="Where else is this happening?" subtitle="The team checks which machines failed, which stayed healthy, and which should be watched." />
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
      <p className="scene-footnote">The affected machines share localization L4 and loading zone B. EX11 shares both but had no known issue when the team made the decision. This narrows the search. It does not prove the cause.</p>
      <button className="button primary" onClick={onNext}>What did we know?</button>
    </section>
  );
}

function DecisionStateScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="4 · Decision agent" title="Veyra recommends the next action from the evidence available now." subtitle="The team can approve the action. Later evidence updates the current view without changing the old decision." />
      <DecisionAgentMoment sealed={Boolean(pkg?.sealed)} />
      <button className="button primary" onClick={onNext}>What did the team do?</button>
    </section>
  );
}

function ActionScene({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="5 · Team action" title="What did the team do?" subtitle="The decision, the action and the evidence stay together." />
      <div className="action-flow">
        <div className="action-source">
          <span>Chosen action</span>
          <b>Remote recovery</b>
          <small>Hold rollout · hold field dispatch</small>
        </div>
        <div className="action-machine-results">
          <b>EX03 ✓</b>
          <b>EX05 ✓</b>
          <b>EX08 ✓</b>
        </div>
        <div className="action-source good">
          <span>Record</span>
          <b>Decision recorded</b>
          <small>Owner · Operations Lead · 14:31</small>
        </div>
      </div>
      <HeroLine text="Now the action can be checked against what happened next." />
      <button className="button primary" onClick={onNext}>What happened after?</button>
    </section>
  );
}

function DecisionAgentMoment({ sealed }: { sealed: boolean }) {
  return (
    <div className="agent-stage" aria-label="evidence to decision agent to action">
      <div className="decision-status-strip">
        <span>Decision snapshot: 3 affected</span>
        <b>{sealed ? "Decision recorded · 14:27" : "Ready for approval"}</b>
        <small>Current view can update later</small>
      </div>

      <div className="agent-pipeline">
        <section className="evidence-stream">
          <span className="eyebrow">Evidence stream</span>
          <b className="key-signal">3 affected</b>
          <b>9 healthy</b>
          <b className="key-signal">EX11 exposed</b>
          <b>$1K to $2K dispatch</b>
          <b>prior recovery worked</b>
        </section>

        <section className="veyra-agent-core">
          <span>Veyra Decision Agent</span>
          <div className="agent-orbit" />
          <ul>
            <li>Comparing fleet state</li>
            <li>Checking previous outcomes</li>
            <li>One signal still missing</li>
          </ul>
          <small>Fetching EX11 runtime history...</small>
        </section>

        <section className="recommended-action-hero">
          <span className="eyebrow">Suggested action</span>
          <h3>Remote recovery</h3>
          <strong>Hold rollout · Hold field dispatch</strong>
          <dl>
            <div><dt>Why</dt><dd>3 affected machines share the same exposure. Release 2.7 alone does not explain the failure.</dd></div>
            <div><dt>Missing evidence</dt><dd>EX11 runtime history.</dd></div>
            <div><dt>Confidence</dt><dd>Medium</dd></div>
          </dl>
        </section>
      </div>

      <div className="agent-approval-row">
        <div className="approved-action">
          <span>Approve action</span>
          <b>Decision recorded · 14:27</b>
        </div>
        <div className="late-event-card">
          <span>Late evidence</span>
          <b>EX11 · event_time 14:09</b>
          <small>arrived 14:31</small>
        </div>
        <div className="view-update-card">
          <span>Current view</span>
          <b>3 → 4 affected</b>
          <small>Decision snapshot stays 3</small>
        </div>
      </div>

      <div className="agent-final-line">
        <b>Current view updated. Decision snapshot preserved.</b>
        <span>Later facts do not rewrite the old decision.</span>
      </div>
    </div>
  );
}

function OutcomeScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle eyebrow="6 · Outcome" title="What happened after the action?" subtitle="The machines returned to service. Later, EX11 changed the current view." />
      <div className="outcome-grid">
        <div><span>EX03</span><b>returned to service after remote recovery</b></div>
        <div><span>EX05</span><b>returned to service after remote recovery</b></div>
        <div><span>Cause</span><b>seen after action, not proven yet</b></div>
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
      <HeroLine text="The current case updates. The old decision stays honest." />
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
        <p>Useful precedent. Cause not proven yet.</p>
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
      <div className="future-strip"><span>{memoryReady ? "Case ready" : "Case preview"}</span><b>Evidence → decision → action → outcome → learning</b></div>
      <button className="button lime" onClick={onNext}>End</button>
    </section>
  );
}

function EndScene() {
  return (
    <section className="cinema-scene center end-frame">
      <h1>A company should not start from zero when a similar machine problem appears again.</h1>
      <p>Veyra helps the next team reuse what the last team learned.</p>
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
