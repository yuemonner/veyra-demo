"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type StepKey = "ingest" | "timeline" | "contrast" | "explain" | "next" | "decision" | "outcome" | "precedent";

type ToolResult = {
  title: string;
  lines: string[];
  json: Record<string, string | number | string[]>;
};

const steps: Array<{ key: StepKey; label: string; tool: string; verb: string }> = [
  { key: "ingest", label: "Open case", tool: "get_case", verb: "Create case graph" },
  { key: "timeline", label: "Build timeline", tool: "get_case_timeline", verb: "Order evidence" },
  { key: "contrast", label: "Compare fleet", tool: "compare_assets", verb: "Separate affected and healthy" },
  { key: "explain", label: "List explanations", tool: "list_hypotheses", verb: "Keep causes explicit" },
  { key: "next", label: "Rank next check", tool: "rank_next_checks", verb: "Choose useful evidence" },
  { key: "decision", label: "Record decision", tool: "record_intervention", verb: "Human chooses" },
  { key: "outcome", label: "Link outcome", tool: "record_outcome", verb: "Measure what happened" },
  { key: "precedent", label: "Find precedent", tool: "find_precedents", verb: "Reuse the case" },
];

const results: Record<StepKey, ToolResult> = {
  ingest: {
    title: "FC 021 opened",
    lines: ["12 machines received autonomy 2.7.", "EX03, EX05 and EX08 entered safe-stop.", "Case status: unresolved."],
    json: { case_id: "FC-021", machines: 12, affected: "EX03, EX05, EX08", status: "open" },
  },
  timeline: {
    title: "Evidence ordered",
    lines: ["14:02 release deployed.", "14:11 first safe-stop.", "14:26 operator note added."],
    json: { windows: ["before", "change", "incident", "review"], first_signal: "14:11", sources: 5 },
  },
  contrast: {
    title: "The release alone does not explain it",
    lines: ["Autonomy 2.7: 3/3 affected and 9/9 healthy.", "Localization L4: 3/3 affected and 2/9 healthy.", "Zone B: 3/3 affected and 1/9 healthy."],
    json: { release: "common", localization_l4: "separator", zone_b: "separator", useful_counterexample: "EX11" },
  },
  explain: {
    title: "Three explanations remain",
    lines: ["Strong: Localization L4 + Zone B.", "Possible: Localization L4 alone.", "Weak: LiDAR firmware 5.3."],
    json: { H1: "Localization L4", H2: "L4 + Zone B", H3: "LiDAR 5.3", open_explanations: 3 },
  },
  next: {
    title: "Next useful check selected",
    lines: ["Observe EX11 in Zone B.", "It separates the two strongest explanations.", "Cost: low. Risk: low. Time: about 15 minutes."],
    json: { next_check: "Observe EX11 in Zone B", information_value: "high", cost: "low", risk: "low" },
  },
  decision: {
    title: "Human decision recorded",
    lines: ["Veyra suggested observing EX11.", "Team chose rollback on EX03, EX05 and EX08.", "Reason: production pressure."],
    json: { suggested: "Observe EX11", chosen: "Rollback L4", owner: "Operations", decision_time: "14:27" },
  },
  outcome: {
    title: "Outcome linked",
    lines: ["All three recovered after rollback.", "Result supports a localization-related explanation.", "It does not prove L4 alone versus L4 plus Zone B."],
    json: { recovered: "3/3", attribution: "observed_after_action", unresolved: "L4 alone vs L4 + Zone B" },
  },
  precedent: {
    title: "FC 034 starts ahead",
    lines: ["EX21 shows a similar safe-stop 12 days later.", "Veyra retrieves FC 021.", "Previous action, outcome and unresolved question are attached."],
    json: { new_case: "FC-034", precedent: "FC-021", reused: ["conditions", "action", "outcome", "open question"] },
  },
};

const graphNodes = [
  ["Evidence", "Telemetry, releases, config and notes"],
  ["Contrast", "Affected vs healthy fleet"],
  ["Hypotheses", "For, against and missing"],
  ["Next check", "Highest information value"],
  ["Decision", "Human action and rationale"],
  ["Outcome", "Observed result"],
  ["Precedent", "Reusable case memory"],
];

const machines = [
  ["EX01", "healthy"], ["EX02", "healthy"], ["EX03", "affected"], ["EX04", "healthy"],
  ["EX05", "affected"], ["EX06", "healthy"], ["EX07", "healthy"], ["EX08", "affected"],
  ["EX09", "healthy"], ["EX10", "healthy"], ["EX11", "watch"], ["EX12", "healthy"],
];

export default function ProductDemoPage() {
  const [active, setActive] = useState<StepKey>("ingest");
  const [completed, setCompleted] = useState<StepKey[]>(["ingest"]);
  const activeIndex = steps.findIndex((step) => step.key === active);
  const current = results[active];

  function runStep(key: StepKey) {
    setActive(key);
    setCompleted((prev) => Array.from(new Set([...prev, key])));
  }

  function runNext() {
    const next = steps[Math.min(activeIndex + 1, steps.length - 1)];
    runStep(next.key);
  }

  function resetCase() {
    setActive("ingest");
    setCompleted(["ingest"]);
  }

  const stageStatus = useMemo(() => {
    if (completed.includes("precedent")) return "precedent ready";
    if (completed.includes("outcome")) return "outcome linked";
    if (completed.includes("decision")) return "decision recorded";
    if (completed.includes("next")) return "next check ranked";
    return "case open";
  }, [completed]);

  return (
    <main className="pilot-workspace live-product-demo">
      <header className="pilot-header live-header">
        <div>
          <Link className="pilot-brand" href="/veyra"><span>V</span> Veyra</Link>
          <p>Product Demo · live field case workflow</p>
          <h1>Click the case tools. Watch the decision record change.</h1>
        </div>
        <div className="pilot-kpis" aria-label="case status">
          <b>FC 021</b>
          <b>12 machines</b>
          <b>{completed.includes("outcome") ? "3 recovered" : "3 affected"}</b>
          <b>{stageStatus}</b>
        </div>
      </header>

      <section className="live-demo-grid">
        <aside className="live-rail">
          <div className="live-case-summary">
            <span>Field case</span>
            <h2>Unexpected safe-stop after autonomy 2.7</h2>
            <p>All machines got the same release. Only three stopped. Veyra helps decide what to check next and records what worked.</p>
          </div>

          <div className="live-machine-panel">
            <div><span>Machines</span><b>12</b></div>
            <div className="machine-grid-mini">
              {machines.map(([id, status]) => (
                <i key={id} className={status} title={`${id} ${status}`}>{id}</i>
              ))}
            </div>
            <p><b>Affected</b> EX03 · EX05 · EX08</p>
            <p><b>Useful comparison</b> EX11</p>
          </div>

          <nav className="tool-runner" aria-label="Veyra tools">
            {steps.map((step, index) => {
              const done = completed.includes(step.key);
              return (
                <button key={step.key} className={active === step.key ? "active" : done ? "done" : ""} onClick={() => runStep(step.key)}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{step.label}</b>
                  <code>{step.tool}()</code>
                </button>
              );
            })}
          </nav>
        </aside>

        <section className="live-stage">
          <div className="stage-topline">
            <span>{steps[activeIndex].verb}</span>
            <div className="stage-actions">
              <button onClick={runNext}>{activeIndex === steps.length - 1 ? "Replay result" : "Run next tool"}</button>
              <button onClick={resetCase}>Reset</button>
            </div>
          </div>

          <div className="case-graph-live">
            {graphNodes.map(([label, text], index) => {
              const isLit = index <= activeIndex;
              const isNow = index === activeIndex || (active === "timeline" && index === 0);
              return (
                <article key={label} className={isLit ? "lit" : ""}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <h3>{label}</h3>
                  <p>{text}</p>
                  {isNow && <i>active</i>}
                </article>
              );
            })}
          </div>

          <div className="tool-result-layout">
            <article className="tool-result-card">
              <span>Veyra returned</span>
              <h2>{current.title}</h2>
              <ul>
                {current.lines.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </article>
            <article className="json-card">
              <span>Tool response</span>
              <pre>{JSON.stringify(current.json, null, 2)}</pre>
            </article>
          </div>

          <DynamicDetail active={active} completed={completed} />
        </section>
      </section>
    </main>
  );
}

function DynamicDetail({ active, completed }: { active: StepKey; completed: StepKey[] }) {
  if (active === "contrast") {
    return (
      <div className="live-detail contrast-detail">
        <h2>What separates the stopped machines?</h2>
        <table>
          <thead><tr><th>Context</th><th>Affected</th><th>Healthy</th><th>Meaning</th></tr></thead>
          <tbody>
            <tr><td>Autonomy 2.7</td><td>3/3</td><td>9/9</td><td>Common to all</td></tr>
            <tr className="strong"><td>Localization L4</td><td>3/3</td><td>2/9</td><td>Useful separator</td></tr>
            <tr className="strong"><td>Zone B</td><td>3/3</td><td>1/9</td><td>Useful separator</td></tr>
            <tr><td>LiDAR 5.3</td><td>3/3</td><td>7/9</td><td>Weak lead</td></tr>
          </tbody>
        </table>
      </div>
    );
  }

  if (active === "explain" || active === "next") {
    return (
      <div className="live-detail hypothesis-detail">
        {[
          ["Strong", "Localization L4 + Zone B", "For: all affected share both", "Missing: healthy L4 machine in Zone B"],
          ["Possible", "Localization L4", "For: all affected use L4", "Against: EX11 uses L4 and is healthy"],
          ["Weak", "LiDAR firmware 5.3", "For: recently changed", "Against: many healthy machines use it"],
        ].map(([status, title, a, b]) => (
          <article key={title} className={status.toLowerCase()}>
            <span>{status}</span>
            <h3>{title}</h3>
            <p>{a}</p>
            <p>{b}</p>
          </article>
        ))}
      </div>
    );
  }

  if (active === "decision" || active === "outcome" || active === "precedent") {
    return (
      <div className="live-detail decision-detail">
        <article>
          <span>Veyra suggested</span>
          <h3>Observe EX11 in Zone B</h3>
          <p>Best information value. Low disruption.</p>
        </article>
        <article className={completed.includes("decision") ? "lit" : ""}>
          <span>Team chose</span>
          <h3>Rollback L4 on EX03, EX05 and EX08</h3>
          <p>Production pressure. Faster recovery mattered more than clean diagnosis.</p>
        </article>
        <article className={completed.includes("outcome") ? "lit" : ""}>
          <span>Observed outcome</span>
          <h3>3/3 recovered after rollback</h3>
          <p>Supports localization-related explanation. Causality still not fully proven.</p>
        </article>
      </div>
    );
  }

  return (
    <div className="live-detail prompt-detail">
      <h2>What the customer feels</h2>
      <p>Instead of opening logs, dashboards and tickets by hand, the team opens one field case and asks focused questions through Veyra tools.</p>
      <div><b>What changed?</b><b>Where else?</b><b>What could explain it?</b><b>What should we check?</b><b>What happened after?</b></div>
    </div>
  );
}
