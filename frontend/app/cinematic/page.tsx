"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { postJson } from "../../lib/api";

type SceneKey =
  | "overview"
  | "changed"
  | "whereelse"
  | "causes"
  | "nextcheck"
  | "decision"
  | "outcome"
  | "reuse";

const INVESTIGATION_ID = "inv-120-robots-bad-rollout";

const SCENES: SceneKey[] = [
  "overview",
  "changed",
  "whereelse",
  "causes",
  "nextcheck",
  "decision",
  "outcome",
  "reuse",
];

const changeRows = [
  ["Autonomy 2.7", "3/3", "9/9", "No"],
  ["Localization L4", "3/3", "2/9", "Yes"],
  ["Zone B", "3/3", "1/9", "Yes"],
  ["LiDAR 5.3", "3/3", "7/9", "Weak"],
];

export default function CinematicDemo() {
  return (
    <Suspense fallback={<main className="cinematic"><section className="cinema-scene center"><h1>Loading field case...</h1></section></main>}>
      <FieldCaseReplay />
    </Suspense>
  );
}

function FieldCaseReplay() {
  const [scene, setScene] = useState(0);
  const sceneKey = SCENES[scene];

  async function reset(goToStart = true) {
    await postJson("/demo/reset");
    if (goToStart) setScene(0);
  }

  async function recordDecisionAndOutcome() {
    const pkg = await postJson<{ id: string }>(`/investigations/${INVESTIGATION_ID}/decision-package`);
    await postJson(`/investigations/${INVESTIGATION_ID}/decision`, {
      decision: "Rollback localization L4 on EX03, EX05 and EX08",
      owner: "Operations Lead",
      rationale: "Production pressure. Faster recovery mattered more than clean diagnosis.",
      package_id: pkg.id,
    });
    await postJson(`/decision-packages/${pkg.id}/seal`);
    await postJson(`/investigations/${INVESTIGATION_ID}/outcome`, {
      outcome: "All 3 affected machines recovered after rollback. Result supports a localization-related explanation, but does not prove whether L4 alone caused the issue or interacted with Zone B.",
      payload: {
        previous_action: "Rollback localization L4 on EX03, EX05 and EX08",
        recovery_minutes: 37,
        field_visit: "avoided",
        attribution_level: "likely_related",
        recommended_follow_up: "Test EX11 in Zone B before the next full rollout",
      },
    });
  }

  async function nextScene() {
    const next = Math.min(scene + 1, SCENES.length - 1);
    setScene(next);
    if (SCENES[next] === "outcome") {
      recordDecisionAndOutcome().catch(() => undefined);
    }
  }

  function prevScene() {
    setScene((value) => Math.max(value - 1, 0));
  }

  useEffect(() => {
    reset(false).catch(() => undefined);
  }, []);

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
  }, [scene]);

  return (
    <main className="cinematic field-case-replay">
      <div className="cinematic-top demo-visible">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <div className="mode-switch">
          <button className="active" onClick={() => reset()}>Replay field case</button>
          <button onClick={() => nextScene().catch(() => undefined)}>Next</button>
          <Link href="/product-demo">Open Product Demo</Link>
          <button onClick={() => reset()}>Reset</button>
        </div>
      </div>

      <div className="progress"><span style={{ width: `${((scene + 1) / SCENES.length) * 100}%` }} /></div>

      {sceneKey === "overview" && <CaseOverview onNext={nextScene} />}
      {sceneKey === "changed" && <WhatChanged onNext={nextScene} />}
      {sceneKey === "whereelse" && <WhereElse onNext={nextScene} />}
      {sceneKey === "causes" && <PossibleCauses onNext={nextScene} />}
      {sceneKey === "nextcheck" && <NextCheck onNext={nextScene} />}
      {sceneKey === "decision" && <TeamDecision onNext={nextScene} />}
      {sceneKey === "outcome" && <Outcome onNext={nextScene} />}
      {sceneKey === "reuse" && <Reuse />}
    </main>
  );
}

function CaseOverview({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="Field case"
        title="FC 021 · Unexpected safe stop after autonomy 2.7"
        subtitle="All 12 machines received the same release, but only 3 stopped. The release alone does not explain the issue."
      />
      <div className="case-kpis">
        <Kpi label="Updated" value="12 machines" />
        <Kpi label="Affected" value="3 machines" />
        <Kpi label="First issue" value="14:11" />
        <Kpi label="Case status" value="Unresolved" />
      </div>
      <CinematicFleet />
      <button className="button primary" onClick={onNext}>What changed?</button>
    </section>
  );
}

function WhatChanged({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="1 · What changed"
        title="Which changes still separate affected from healthy machines?"
        subtitle="Veyra compares each change against the machines that stopped and the machines that stayed healthy."
      />
      <table className="table cinema-table field-table">
        <thead><tr><th>Change</th><th>Affected</th><th>Healthy</th><th>Useful?</th></tr></thead>
        <tbody>
          {changeRows.map(([change, affected, healthy, useful]) => (
            <tr key={change} className={useful === "Yes" ? "highlight-row" : ""}>
              <td>{change}</td><td>{affected}</td><td>{healthy}</td><td>{useful}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <HeroLine text="Autonomy 2.7 is common to all machines. Localization L4 and Zone B still separate affected from healthy machines." />
      <button className="button primary" onClick={onNext}>Where else?</button>
    </section>
  );
}

function WhereElse({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="2 · Where else"
        title="Where else is this happening?"
        subtitle="The point is not every machine detail. The point is finding the useful counterexample."
      />
      <div className="cohort-motion field-cohorts">
        <div className="cohort-group affected-group">
          <span>Affected</span>
          <b>EX03</b><b>EX05</b><b>EX08</b>
        </div>
        <div className="filter-stack">
          <i>Autonomy 2.7</i>
          <i>Localization L4</i>
          <i>Zone B</i>
        </div>
        <div className="cohort-group healthy-group">
          <span>Healthy with similar exposure</span>
          <b className="watch">EX11</b>
          <small>autonomy 2.7 · localization L4 · Zone C</small>
        </div>
      </div>
      <HeroLine text="EX11 matters because it has autonomy 2.7 and localization L4, but is still healthy in Zone C." />
      <p className="scene-footnote">That means localization L4 alone may not explain the issue.</p>
      <button className="button primary" onClick={onNext}>Possible causes</button>
    </section>
  );
}

function PossibleCauses({ onNext }: { onNext: () => void }) {
  const hypotheses = [
    {
      title: "Localization L4",
      status: "Possible",
      forText: "All affected machines use L4.",
      against: "EX11 also uses L4 and is healthy.",
      missing: "EX11 behavior in Zone B.",
    },
    {
      title: "Localization L4 + Zone B",
      status: "Strong",
      forText: "All affected machines share both.",
      against: "Limited exposure.",
      missing: "Healthy L4 machine in Zone B.",
    },
    {
      title: "LiDAR firmware",
      status: "Weak",
      forText: "Recently changed.",
      against: "Many healthy machines use the same version.",
      missing: "None worth checking first.",
    },
  ];

  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="3 · Possible causes"
        title="Keep the explanations separate."
        subtitle="No fake confidence score. Just what supports each explanation, what pushes against it, and what is missing."
      />
      <div className="hypothesis-grid">
        {hypotheses.map((item) => (
          <article key={item.title} className={`hypothesis-card ${item.status.toLowerCase()}`}>
            <div className="hypothesis-head"><h3>{item.title}</h3><span>{item.status}</span></div>
            <dl>
              <div><dt>For</dt><dd>{item.forText}</dd></div>
              <div><dt>Against</dt><dd>{item.against}</dd></div>
              <div><dt>Missing</dt><dd>{item.missing}</dd></div>
            </dl>
          </article>
        ))}
      </div>
      <button className="button primary" onClick={onNext}>What should we check?</button>
    </section>
  );
}

function NextCheck({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="4 · Next check"
        title="What should we check next?"
        subtitle="The best next move is the one that separates the strongest remaining explanations without disrupting production."
      />
      <div className="next-move-layout">
        <article className="next-move-card recommended">
          <span>Veyra suggests</span>
          <h2>Observe EX11 in Zone B</h2>
          <p>It separates the two strongest explanations without changing a production machine.</p>
          <div className="option-meta"><b>Cost: Low</b><b>Time: ~15 min</b><b>Risk: Low</b></div>
        </article>
        <article className="next-move-card">
          <span>Alternative</span>
          <h2>Roll back L4 on one affected machine</h2>
          <p>Faster operationally, but less clean as a diagnostic step.</p>
          <div className="option-meta"><b>Cost: Medium</b><b>Disruption: Medium</b><b>Information value: Lower</b></div>
        </article>
      </div>
      <button className="button primary" onClick={onNext}>What did the team choose?</button>
    </section>
  );
}

function TeamDecision({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="5 · Team decision"
        title="The recommendation and the human action are not the same thing."
        subtitle="This is what real operations look like. The team can choose speed over clean diagnosis."
      />
      <div className="decision-compare">
        <article>
          <span>Veyra suggested</span>
          <h3>Observe EX11 in Zone B</h3>
          <p>Best information value. Low cost. Low risk.</p>
        </article>
        <article className="chosen">
          <span>Team chose</span>
          <h3>Roll back localization L4 on EX03, EX05 and EX08</h3>
          <p><b>Reason:</b> Production pressure. Faster recovery mattered more than clean diagnosis.</p>
        </article>
      </div>
      <button className="button primary" onClick={onNext}>What happened?</button>
    </section>
  );
}

function Outcome({ onNext }: { onNext: () => void }) {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="6 · Outcome"
        title="What happened after the action?"
        subtitle="The result supports a localization-related explanation, but it does not turn similarity into proof."
      />
      <div className="outcome-grid">
        <div><span>EX03</span><b>Recovered after rollback</b></div>
        <div><span>EX05</span><b>Recovered after rollback</b></div>
        <div><span>EX08</span><b>Recovered after rollback</b></div>
        <div><span>Field visit</span><b>Avoided</b></div>
        <div><span>What it supports</span><b>Localization-related explanation</b></div>
        <div><span>What it does not prove</span><b>L4 alone vs L4 + Zone B</b></div>
      </div>
      <HeroLine text="Recommended follow-up: test EX11 in Zone B before the next full rollout." />
      <button className="button primary" onClick={onNext}>Reuse next time</button>
    </section>
  );
}

function Reuse() {
  return (
    <section className="cinema-scene">
      <SceneTitle
        eyebrow="7 · Reuse"
        title="FC 034 · EX21 shows a similar safe stop 12 days later"
        subtitle="The new case starts with the previous evidence, decision and outcome already attached."
      />
      <div className="reuse-panel">
        <article>
          <span>Relevant past case</span>
          <h3>FC 021</h3>
          <p>Previous rollback restored operation. Zone B and localization L4 were still not fully separated.</p>
        </article>
        <article>
          <span>Shared context</span>
          <ul>
            <li>Autonomy 2.7</li>
            <li>Localization L4</li>
            <li>Zone B</li>
            <li>Previous rollback restored operation</li>
          </ul>
        </article>
        <article className="start-card">
          <span>Next case</span>
          <h3>Start from previous evidence</h3>
          <p>This case does not start from zero.</p>
        </article>
      </div>
      <Link className="button lime" href="/product-demo">Open Product Demo</Link>
    </section>
  );
}

function CinematicFleet() {
  const affectedIds = new Set([2, 4, 7]);
  return (
    <div className="cinema-fleet">
      {Array.from({ length: 12 }).map((_, index) => (
        <span key={index} style={{ animationDelay: `${index * 35}ms` }} className={affectedIds.has(index) ? "robot-dot affected" : "robot-dot"}>
          <small>EX{String(index + 1).padStart(2, "0")}</small>
        </span>
      ))}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><b>{value}</b></div>;
}

function SceneTitle({ eyebrow, title, subtitle }: { eyebrow: string; title: string; subtitle: string }) {
  return <header className="scene-title"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></header>;
}

function HeroLine({ text }: { text: string }) {
  return <div className="hero-line">{text}</div>;
}
