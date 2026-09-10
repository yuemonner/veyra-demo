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

type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string };

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
      decision: "Pause rollout on Profile C sites",
      owner: "Fleet Operations",
      rationale: "Application v2.4 changed everywhere, while Profile C contains all known affected robots plus exposed peers to watch.",
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
      outcome: "Profile C rollout isolated; affected robots recovered in 18 minutes",
      payload: { previous_action: "Pause rollout on Profile C sites", recovery_minutes: 18, days_later: 47 },
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

  const affected = comparison?.same_signal ?? 37;
  const healthy = comparison?.no_signal ?? 83;

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
  return <section className="cinema-scene center"><span className="eyebrow">Cinematic live demo</span><h1>120 robots. One bad rollout. 90 seconds.</h1><p>Veyra — Operational context for Physical AI</p><button className="button lime" onClick={onNext}>Start</button></section>;
}

function DeployScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 1 · Signal" title="When 120 robots deploy, 37 should not silently fail." subtitle="Today, they do." /><CinematicFleet affected={affected} /><div className="cinema-events"><EventLine time="14:02:11" label="deployment started" /><EventLine time="14:04:37" label="120/120 updated" /><EventLine time="14:11:08" label="first abnormal navigation signal" /><EventLine time="14:18:42" label={`${affected} affected · ${healthy} healthy`} /><EventLine time="14:26:03" label="customer ticket arrives" /></div><HeroLine text="7 minutes and 21 seconds. That is how long customer trust is exposed when you know before they call." /><button className="button primary" onClick={onNext}>Where else?</button></section>;
}

function WhereElseScene({ affected, healthy, onNext }: { affected: number; healthy: number; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 2 · Where else" title="Where else is this pattern appearing?" subtitle="The first question is scope: which machines show the signal, and which exposed machines should be watched now?" /><CinematicFleet affected={affected} /><div className="site-grid"><div><span>Site A</span><b>12 affected</b><small>8 healthy</small></div><div><span>Site B</span><b>15 affected</b><small>5 healthy</small></div><div><span>Site C</span><b>10 affected</b><small>0 healthy</small></div><div><span>Other sites</span><b>0 affected</b><small>70 healthy</small></div></div><HeroLine text={`${affected} machines show the pattern. 21 more share the same exposure without a matching signal yet.`} /><button className="button primary" onClick={onNext}>Compare affected and healthy</button></section>;
}

function CompareScene({ comparison, onNext }: { comparison: Comparison | null; onNext: () => void }) {
  const rows = useMemo(() => comparison?.table ?? [], [comparison]);
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 3 · Compare" title="Same software. Same deployment. Why does only one-third fail?" subtitle="This is the question that matters." /><div className="split-count"><div><strong>{comparison?.same_signal ?? 37}</strong><span>affected</span></div><div><strong>{comparison?.no_signal ?? 83}</strong><span>healthy</span></div></div><table className="table cinema-table"><thead><tr><th>Context</th><th>Affected</th><th>Healthy</th></tr></thead><tbody>{rows.map((row) => <tr className={row.context === "Network Profile C" ? "highlight-row" : ""} key={row.context}><td>{row.context}</td><td>{row.affected}</td><td>{row.unaffected}</td></tr>)}</tbody></table><HeroLine text="One lead rises above the noise: Network Profile C, 37/37 affected and 21/83 healthy." /><p className="scene-footnote">This narrows the investigation and identifies exposed machines to watch. It does not establish cause.</p><button className="button primary" onClick={onNext}>Generate Decision Package</button></section>;
}

function DecisionScene({ pkg, onNext }: { pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 4 · Decision Package" title="A decision made on Monday should not be rewritable on Tuesday." subtitle="This package locks what the team knew at the moment they knew it." /><div className="decision-card"><div><span>Observed</span><b>37 robots affected</b></div><div><span>Observed</span><b>v2.4 deployed to 120</b></div><div><span>Observed</span><b>customer ticket at 14:26</b></div><div><span>Inferred</span><b>Profile C is the highest-priority lead</b></div><div><span>Human asserted</span><b>engineer suspects site-side network instability</b></div><div><span>Missing evidence</span><b>site-side network validation</b></div><div><span>Last known healthy</span><b>14:02</b></div><div><span>Human decision</span><b>Pause rollout for Profile C</b></div><div><span>Outcome</span><b>pending</b></div></div><div className="seal-card"><span>Sealed Decision Package · 14:27</span><b>{pkg?.digest ? pkg.digest.slice(0, 18) + "..." : "SHA-256 pending"}</b><small>18 evidence items · 120 machines compared · human decision recorded</small></div><button className="button primary" onClick={onNext}>Inject delayed evidence</button></section>;
}

function LateScene({ comparison, pkg, onNext }: { comparison: Comparison | null; pkg: DecisionPackage | null; onNext: () => void }) {
  return <section className="cinema-scene"><SceneTitle eyebrow="Scene 5" title="Late evidence reveals AMR-038 had already shown the signal at 14:09." subtitle="This happened before the decision. The team learned about it after." /><div className="time-rail cinematic-rail"><div><b>14:09</b><span>event_time</span><small>AMR-038 was already affected</small></div><i /><div><b>14:27</b><span>decision sealed</span><small>37 affected in the package</small></div><i /><div><b>14:31</b><span>known_at</span><small>late evidence becomes knowable</small></div><i /><div><b>14:31:04</b><span>ingested_at</span><small>received by Veyra</small></div></div><div className="late-grid"><div><span>Decision-time view</span><b>37 affected</b></div><div><span>Current view</span><b>{comparison?.same_signal ?? 38} affected</b></div><div><span>Sealed package</span><b>{pkg?.sealed ? "unchanged" : "decision-time snapshot"}</b></div></div><HeroLine text="Veyra updates what we know now, not what the team knew then." /><button className="button primary" onClick={onNext}>47 days later</button></section>;
}

function MemoryScene({ memoryReady, onNext }: { memoryReady: boolean; onNext: () => void }) {
  return <section className="cinema-scene center"><span className="eyebrow">Scene 6 · Operational Memory</span><h1>47 days later, the next incident starts with memory.</h1><div className="memory-card"><p><b>Operational Memory match found</b></p><p>Similar change pattern: deployment + Profile C + navigation degradation.</p><p>Same pattern: one network-profile cluster diverges after deployment.</p><p>Previous human action: isolate rollout group.</p><p>Previous outcome: recovered in 18 minutes.</p><p>Missing last time: site-side network validation.</p><p>Different this time: motor firmware 7.3 on the affected subset.</p></div><div className="time-saved"><b>The first incident took 42 minutes.</b><b>The second took 42 seconds.</b></div><div className="future-strip"><span>What this unlocks</span><b>Better operations · Proactive support · Operational memory · Machine economics</b></div><button className="button lime" onClick={onNext}>{memoryReady ? "End" : "End"}</button></section>;
}

function EndScene() {
  return <section className="cinema-scene center end-frame"><h1>The first time you understand an incident, you earn the right to understand the next one in seconds.</h1><p>Veyra — Operational context for Physical AI.</p><div className="unlock-row"><div><span>Better operations</span><b>Faster review</b></div><div><span>Proactive support</span><b>Watch exposed machines</b></div><div><span>Operational memory</span><b>Reuse what worked</b></div><div><span>Machine economics</span><b>Evidence for maintenance, insurance and usage-based financing</b></div></div><p className="future-line">Reliable operational history can become the evidence behind maintenance, insurance, performance guarantees and usage-based financing.</p><Link className="button lime" href={`/investigations/${INVESTIGATION_ID}`}>Open live system</Link></section>;
}

function CinematicFleet({ affected }: { affected: number }) {
  return <div className="cinema-fleet">{Array.from({ length: 120 }).map((_, index) => <span key={index} className={index < affected ? "robot-dot affected" : "robot-dot"} />)}</div>;
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
