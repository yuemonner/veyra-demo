"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type StepKey = "signal" | "changed" | "where" | "explain" | "check" | "decision" | "outcome" | "reuse";

const steps: Array<{
  key: StepKey;
  label: string;
  title: string;
  subtitle: string;
  result: string;
  detail: string;
  tool: string;
}> = [
  {
    key: "signal",
    label: "Signal",
    title: "Three robots entered safe-stop after the same release.",
    subtitle: "The team opens one field case instead of jumping between dashboards, tickets and release notes.",
    result: "12 updated · 3 affected · first signal 14:11",
    detail: "The case starts with a simple question: why did only these machines stop?",
    tool: "get_case",
  },
  {
    key: "changed",
    label: "Changes",
    title: "The release changed everywhere. The failure did not.",
    subtitle: "Veyra reconstructs the changes around the first failure and separates common changes from useful clues.",
    result: "Autonomy 2.7 is common to all 12 machines.",
    detail: "Localization L4 and Zone B are more useful than the release itself.",
    tool: "get_case_timeline",
  },
  {
    key: "where",
    label: "Fleet",
    title: "EX11 becomes the useful comparison.",
    subtitle: "It shares the release and localization profile, but has no known safe-stop at decision time.",
    result: "3 affected · 8 healthy · 1 exposed healthy",
    detail: "That counterexample keeps the team from jumping to a simple answer too early.",
    tool: "compare_assets",
  },
  {
    key: "explain",
    label: "Causes",
    title: "Veyra keeps competing explanations alive.",
    subtitle: "The product does not declare root cause. It shows what still fits the evidence.",
    result: "Strongest: Localization L4 + Zone B",
    detail: "Still possible: L4 alone. Weak: LiDAR firmware 5.3.",
    tool: "list_hypotheses",
  },
  {
    key: "check",
    label: "Next check",
    title: "The next useful check is clear.",
    subtitle: "Before changing production machines, Veyra finds the check that separates the strongest explanations.",
    result: "Observe EX11 in Zone B",
    detail: "Low cost. Low risk. About 15 minutes. High information value.",
    tool: "rank_next_checks",
  },
  {
    key: "decision",
    label: "Decision",
    title: "The team chooses the practical action.",
    subtitle: "Veyra suggested the cleanest check. Operations chose faster recovery because production pressure mattered.",
    result: "Rollback L4 on EX03, EX05 and EX08",
    detail: "Decision owner and rationale are recorded at 14:27.",
    tool: "record_intervention",
  },
  {
    key: "outcome",
    label: "Outcome",
    title: "The machines recover, but cause is still handled carefully.",
    subtitle: "Veyra links what happened after the action without pretending correlation is proof.",
    result: "3 of 3 recovered after rollback",
    detail: "Supports a localization-related explanation. It does not prove L4 alone versus L4 plus Zone B.",
    tool: "record_outcome",
  },
  {
    key: "reuse",
    label: "Next case",
    title: "Twelve days later, the next case does not start from zero.",
    subtitle: "A similar safe-stop appears on EX21. Veyra brings back the prior evidence, action, outcome and open question.",
    result: "Relevant past case: FC 021",
    detail: "The team starts with precedent, not a blank search.",
    tool: "find_precedents",
  },
];

const stateByStep: Record<StepKey, string> = {
  signal: "Case opened from 3 source records",
  changed: "Change set reconstructed",
  where: "Cohorts compared",
  explain: "Explanation space updated",
  check: "Next check ranked",
  decision: "Human decision recorded",
  outcome: "Outcome linked to intervention",
  reuse: "Precedent attached to new case",
};

const machines = [
  ["EX01", "healthy"], ["EX02", "healthy"], ["EX03", "affected"], ["EX04", "healthy"],
  ["EX05", "affected"], ["EX06", "healthy"], ["EX07", "healthy"], ["EX08", "affected"],
  ["EX09", "healthy"], ["EX10", "healthy"], ["EX11", "watch"], ["EX12", "healthy"],
];

const evidenceByStep: Record<StepKey, string[]> = {
  signal: ["EX03 safe-stop · 14:11", "EX05 safe-stop · 14:18", "EX08 operator note · 14:26"],
  changed: ["Autonomy 2.6 → 2.7", "Localization L3 → L4", "Map M18 → M19", "LiDAR firmware 5.2 → 5.3"],
  where: ["Affected: EX03, EX05, EX08", "Healthy with same release: 9", "Useful comparison: EX11"],
  explain: ["For L4 + Zone B: 3/3 affected", "Against L4 alone: EX11", "Against LiDAR: 7/9 healthy"],
  check: ["Next check: EX11 in Zone B", "Cost: low", "Risk: low", "Time: ~15 min"],
  decision: ["Suggested: observe EX11", "Chosen: rollback L4", "Reason: production pressure"],
  outcome: ["Recovered: EX03, EX05, EX08", "Attribution: observed after action", "Open question: L4 alone or L4 + Zone B"],
  reuse: ["New case: FC 034", "Shared: safe-stop + L4", "Bring forward: action, outcome, open question"],
};

const structuredOutput: Record<StepKey, Array<{ label: string; value: string }>> = {
  signal: [
    { label: "Telemetry", value: "safe_stop event stream" },
    { label: "Release", value: "autonomy 2.7 deployment" },
    { label: "Human note", value: "operator review opened" },
  ],
  changed: [
    { label: "Common change", value: "autonomy 2.7 · 12/12" },
    { label: "Useful split", value: "localization L4 · 3/3 vs 2/9" },
    { label: "Useful split", value: "Zone B · 3/3 vs 1/9" },
  ],
  where: [
    { label: "Affected cohort", value: "EX03 · EX05 · EX08" },
    { label: "Healthy baseline", value: "8 healthy without same exposure" },
    { label: "Counterexample", value: "EX11 shares L4, no known failure" },
  ],
  explain: [
    { label: "H1", value: "L4 alone · possible" },
    { label: "H2", value: "L4 plus Zone B · strongest" },
    { label: "H3", value: "LiDAR 5.3 · weak" },
  ],
  check: [
    { label: "Selected check", value: "observe EX11 in Zone B" },
    { label: "Why first", value: "separates H1 from H2" },
    { label: "Cost", value: "low risk · about 15 min" },
  ],
  decision: [
    { label: "Veyra suggested", value: "observe EX11 in Zone B" },
    { label: "Team chose", value: "rollback L4 on affected robots" },
    { label: "Reason", value: "production pressure mattered" },
  ],
  outcome: [
    { label: "Observed", value: "3/3 returned to service" },
    { label: "Supports", value: "localization-related explanation" },
    { label: "Still open", value: "L4 alone or L4 plus Zone B" },
  ],
  reuse: [
    { label: "Inherited", value: "hypothesis · action · outcome" },
    { label: "Open question", value: "L4 alone or L4 plus Zone B" },
    { label: "New case", value: "FC 034 starts with FC 021" },
  ],
};

const hypotheses = [
  {
    id: "H1",
    title: "Localization L4 alone",
    status: "Possible",
    supports: "3/3 affected use L4",
    against: "EX11 also uses L4 and is healthy",
    missing: "EX11 behavior in Zone B",
  },
  {
    id: "H2",
    title: "L4 plus Zone B",
    status: "Strong",
    supports: "3/3 affected share both",
    against: "No direct counterexample yet",
    missing: "Healthy L4 machine in Zone B",
  },
  {
    id: "H3",
    title: "LiDAR firmware 5.3",
    status: "Weak",
    supports: "Changed before first stop",
    against: "7/9 healthy also use 5.3",
    missing: "No useful next check",
  },
];

export default function ProductDemoPage() {
  const [active, setActive] = useState<StepKey>("signal");
  const activeIndex = steps.findIndex((step) => step.key === active);
  const current = steps[activeIndex];

  const status = useMemo(() => {
    if (activeIndex >= 7) return "precedent found";
    if (activeIndex >= 6) return "outcome linked";
    if (activeIndex >= 5) return "decision recorded";
    if (activeIndex >= 4) return "next check ready";
    return "case open";
  }, [activeIndex]);

  const nextStep = () => setActive(steps[Math.min(activeIndex + 1, steps.length - 1)].key);

  return (
    <main className="product-story-demo">
      <header className="product-story-top">
        <Link className="pilot-brand" href="/veyra"><span>V</span> Veyra</Link>
        <div className="story-status">
          <b>FC 021</b>
          <b>12 machines</b>
          <b>{activeIndex >= 6 ? "3 recovered" : "3 affected"}</b>
          <b>{status}</b>
        </div>
      </header>

      <section className="product-story-hero">
        <div>
          <p>Product Demo · Field Case</p>
          <h1>One field case. Evidence, uncertainty, action, outcome.</h1>
          <span>Synthetic replay based on a real deployed-machine investigation pattern.</span>
        </div>
        <button onClick={nextStep}>{activeIndex === steps.length - 1 ? "Replay ending" : "Continue case"}</button>
      </section>

      <section className="case-workbench">
        <aside className="case-strip" aria-label="case workflow">
          {steps.map((step, index) => (
            <button
              key={step.key}
              onClick={() => setActive(step.key)}
              className={step.key === active ? "active" : index < activeIndex ? "done" : ""}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              {step.label}
            </button>
          ))}
        </aside>

        <section className="case-main-card">
          <div className="case-main-copy">
            <span>{current.label}</span>
            <h2>{current.title}</h2>
            <p>{current.subtitle}</p>
          </div>

          <div className="fleet-visual" aria-label="machine state">
            {machines.map(([id, state]) => (
              <div key={id} className={`machine-dot ${state}`}>
                <i />
                <span>{id}</span>
              </div>
            ))}
          </div>

          <div className="case-result">
            <span>Veyra shows</span>
            <h3>{current.result}</h3>
            <p>{current.detail}</p>
          </div>

          <div className="field-case-output">
            <div className="state-change">
              <span>Case state change</span>
              <b>{stateByStep[active]}</b>
            </div>

            {active === "explain" ? (
              <div className="hypothesis-mini-grid">
                {hypotheses.map((hypothesis) => (
                  <article key={hypothesis.id} className={hypothesis.status.toLowerCase()}>
                    <header>
                      <span>{hypothesis.id}</span>
                      <b>{hypothesis.status}</b>
                    </header>
                    <h4>{hypothesis.title}</h4>
                    <dl>
                      <div>
                        <dt>Supports</dt>
                        <dd>{hypothesis.supports}</dd>
                      </div>
                      <div>
                        <dt>Against</dt>
                        <dd>{hypothesis.against}</dd>
                      </div>
                      <div>
                        <dt>Missing</dt>
                        <dd>{hypothesis.missing}</dd>
                      </div>
                    </dl>
                  </article>
                ))}
              </div>
            ) : (
              <div className="structured-output-grid">
                {structuredOutput[active].map((item) => (
                  <article key={`${item.label}-${item.value}`}>
                    <span>{item.label}</span>
                    <b>{item.value}</b>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>

        <aside className="case-side-panel">
          <div className="evidence-card">
            <span>Evidence in view</span>
            {evidenceByStep[active].map((item) => <b key={item}>{item}</b>)}
          </div>

          <div className="quiet-tech-card">
            <span>System step</span>
            <code>{current.tool}()</code>
            <p>Input, output and source links stay attached to the same field case record.</p>
          </div>
        </aside>
      </section>
    </main>
  );
}
