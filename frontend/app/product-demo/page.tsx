"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type TabKey = "overview" | "timeline" | "compare" | "explanations" | "scoring" | "mcp" | "action";

const tabs: Array<[TabKey, string]> = [
  ["overview", "Field Case"],
  ["timeline", "Timeline"],
  ["compare", "Contrast"],
  ["explanations", "Explanations"],
  ["scoring", "Scoring"],
  ["mcp", "MCP Tools"],
  ["action", "Action & Outcome"],
];

const evidenceEvents = [
  { time: "14:02", source: "deployment", machine: "all", event: "autonomy_release", key: "autonomy", value: "2.6 -> 2.7" },
  { time: "14:03", source: "config", machine: "EX03", event: "profile_change", key: "localization", value: "L3 -> L4" },
  { time: "14:04", source: "config", machine: "EX05", event: "profile_change", key: "localization", value: "L3 -> L4" },
  { time: "14:07", source: "site", machine: "EX08", event: "mission_zone", key: "zone", value: "loading zone B" },
  { time: "14:11", source: "telemetry", machine: "EX03", event: "safe_stop", key: "planner_state", value: "safe-stop" },
  { time: "14:18", source: "telemetry", machine: "EX05", event: "safe_stop", key: "planner_state", value: "safe-stop" },
  { time: "14:26", source: "field note", machine: "EX08", event: "operator_note", key: "symptom", value: "same stop near zone B" },
];

const timelineWindows = [
  ["Before incident", "Release 2.6 stable. EX03, EX05 and EX08 completed comparable missions."],
  ["Change window", "Autonomy 2.7, localization L4, LiDAR 5.3 and map M19 appear in the same review window."],
  ["Incident window", "Three machines enter safe-stop near loading zone B."],
  ["Intervention window", "Team rolls back localization L4 on affected machines."],
  ["Post-intervention", "Recovered after rollback. Follow-up still needs EX11 in Zone B."],
];

const matrix = [
  ["Autonomy 2.7", "3/3", "9/9", "Common to all", "Does not explain the split alone"],
  ["Localization L4", "3/3", "2/9", "Strong separator", "Still has a healthy counterexample"],
  ["Zone B", "3/3", "1/9", "Strong separator", "Needs more exposure"],
  ["LiDAR 5.3", "3/3", "7/9", "Weak", "Too common in healthy machines"],
  ["Maintenance state", "0/3", "2/9", "Not supported", "No useful lead"],
];

const hypotheses = [
  {
    id: "H1",
    title: "Localization L4",
    strength: "Possible",
    support: ["3/3 affected machines use L4"],
    against: ["EX11 also uses L4 and is healthy"],
    missing: "EX11 behavior in Zone B",
    next: "Observe EX11 in Zone B",
  },
  {
    id: "H2",
    title: "Localization L4 + Zone B",
    strength: "Strong",
    support: ["All affected machines share L4 and Zone B"],
    against: ["Limited healthy exposure in Zone B"],
    missing: "Healthy L4 machine in Zone B",
    next: "Run a low-risk observation with EX11",
  },
  {
    id: "H3",
    title: "LiDAR firmware 5.3",
    strength: "Weak",
    support: ["Recently changed"],
    against: ["7/9 healthy machines also use 5.3"],
    missing: "No useful next check",
    next: "Deprioritize for this review",
  },
];

const fieldCaseObjects = [
  ["FieldCase", "FC 021 · safe-stop after autonomy 2.7"],
  ["EvidenceEvent", "Telemetry, release, config, notes and service records"],
  ["MachineState", "Affected, healthy, exposed and unknown assets"],
  ["Change", "Autonomy release, localization profile, map and firmware"],
  ["Hypothesis", "Competing explanations with evidence for and against"],
  ["EvidenceGap", "The next useful check that separates explanations"],
  ["Intervention", "What the team actually changed or observed"],
  ["Outcome", "What happened afterwards and what remains unresolved"],
];

const mcpTools = [
  ["get_case", "Return the field case, status, machines, sources and current open questions."],
  ["get_case_timeline", "Return the before, change, incident, intervention and post-intervention windows."],
  ["compare_assets", "Compare affected machines with healthy machines across selected dimensions."],
  ["list_hypotheses", "Return live explanations with support, contradictions and missing evidence."],
  ["find_contradicting_evidence", "Find evidence that weakens a selected explanation."],
  ["rank_next_checks", "Rank the next checks by information value, cost, risk and disruption."],
  ["record_intervention", "Capture the human action, owner, rationale and timestamp."],
  ["record_outcome", "Attach recovery, recurrence, field work, downtime and unresolved questions."],
  ["find_precedents", "Find similar cases by condition, intervention and observed outcome."],
];

const scoringRows = [
  ["H1 Localization L4", "0.31", "Possible, but weakened by EX11"],
  ["H2 L4 + Zone B", "0.54", "Best supported by current contrast"],
  ["H3 LiDAR 5.3", "0.15", "Too common in healthy machines"],
];

const nextCheckRows = [
  ["Observe EX11 in Zone B", "0.62", "Best information value, low disruption"],
  ["Rollback one affected machine", "0.24", "Faster recovery, weaker diagnostic value"],
  ["Inspect LiDAR firmware first", "0.14", "Low separation from healthy cohort"],
];

const routingRows = [
  ["compare_assets", "0.38", "Need cohort separation first"],
  ["rank_next_checks", "0.33", "Need next evidence recommendation"],
  ["find_precedents", "0.19", "Useful after current evidence is ranked"],
  ["deep_reasoning", "0.10", "Only if confidence stays low"],
];

export default function ProductDemoPage() {
  const [active, setActive] = useState<TabKey>("overview");
  const activeIndex = useMemo(() => tabs.findIndex(([key]) => key === active), [active]);

  return (
    <main className="pilot-workspace">
      <header className="pilot-header">
        <div>
          <Link className="pilot-brand" href="/veyra"><span>V</span> Veyra</Link>
          <p>Product Demo · Field Case Infrastructure</p>
          <h1>Make one real field case queryable by any trusted model.</h1>
        </div>
        <div className="pilot-kpis" aria-label="case status">
          <b>FC 021</b>
          <b>12 machines</b>
          <b>3 affected</b>
          <b>scorer ready</b>
        </div>
      </header>

      <section className="pilot-frame">
        <aside className="pilot-sidebar">
          <div className="pilot-case-card">
            <span>Field Case</span>
            <h2>Robot data, engineering changes and field notes become one case graph.</h2>
            <p>The model queries the case. Veyra owns the structure, provenance and outcome record.</p>
          </div>

          <nav className="pilot-tabs" aria-label="product demo sections">
            {tabs.map(([key, label], index) => (
              <button key={key} className={active === key ? "active" : ""} onClick={() => setActive(key)}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {label}
              </button>
            ))}
          </nav>

          <div className="pilot-source-card">
            <span>Evidence sources</span>
            <b>Alloy MCP or existing robot data</b>
            <b>Telemetry</b>
            <b>Software + config</b>
            <b>Jira, Slack, GitHub</b>
            <b>Service records + field notes</b>
          </div>
        </aside>

        <section className="pilot-main">
          <div className="pilot-progress"><span style={{ width: `${((activeIndex + 1) / tabs.length) * 100}%` }} /></div>
          {active === "overview" && <Overview />}
          {active === "timeline" && <Timeline />}
          {active === "compare" && <Compare />}
          {active === "explanations" && <Explanations />}
          {active === "scoring" && <Scoring />}
          {active === "mcp" && <McpTools />}
          {active === "action" && <ActionOutcome />}
        </section>
      </section>
    </main>
  );
}

function Overview() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Field Case Graph"
        title="Veyra turns a field incident into a case any model can reason over."
        subtitle="The first pilot makes one historical field case agent-queryable: evidence, timeline, contrast, hypotheses, missing evidence, intervention and outcome."
      />
      <div className="case-graph">
        {fieldCaseObjects.map(([object, value], index) => (
          <article key={object} className={index > 3 ? "case-graph-core" : ""}>
            <span>{object}</span>
            <p>{value}</p>
          </article>
        ))}
      </div>
      <div className="pilot-pipeline agent-substrate">
        {[
          ["Evidence adapters", "Meet data where it already lives: robot data, releases, tickets, notes and service history."],
          ["Field Case Graph", "Normalize the case into objects a model can query without reading every raw log."],
          ["Fast scorer", "Score hypotheses, next checks and tool routes before expensive reasoning."],
          ["Veyra MCP", "Expose specialized tools for timeline, contrast, hypotheses, checks and outcomes."],
          ["Trusted models", "Claude, Codex, ChatGPT or customer agents call Veyra tools for case reasoning."],
        ].map(([title, text]) => (
          <article key={title}>
            <span>{title}</span>
            <p>{text}</p>
          </article>
        ))}
      </div>
      <div className="pilot-note">
        <b>Architecture principle</b>
        <p>Models reason. Veyra gives them the structured field case, evidence links, missing evidence, intervention record, outcome history and fast decision scores they need to reason over.</p>
      </div>
    </div>
  );
}

function Timeline() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Timeline"
        title="Normalize raw evidence into a traceable case timeline."
        subtitle="Every EvidenceEvent keeps timestamp, source, machine, event type, value and raw reference. The model receives compact case context, not a pile of logs."
      />
      <div className="event-table">
        <div className="event-head">
          <span>Time</span><span>Source</span><span>Machine</span><span>Event</span><span>Value</span>
        </div>
        {evidenceEvents.map((event) => (
          <div className="event-row" key={`${event.time}-${event.machine}-${event.event}`}>
            <span>{event.time}</span>
            <b>{event.source}</b>
            <span>{event.machine}</span>
            <span>{event.event}</span>
            <strong>{event.value}</strong>
          </div>
        ))}
      </div>
      <div className="timeline-windows">
        {timelineWindows.map(([title, text]) => (
          <article key={title}>
            <span>{title}</span>
            <p>{text}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function Compare() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Contrast"
        title="Compare affected machines with the healthy fleet."
        subtitle="This is the core investigation wedge: classify each feature as common to all, separating the groups, weakly correlated or missing."
      />
      <table className="pilot-table">
        <thead>
          <tr><th>Feature</th><th>Affected</th><th>Healthy</th><th>Class</th><th>Meaning</th></tr>
        </thead>
        <tbody>
          {matrix.map(([feature, affected, healthy, klass, meaning]) => (
            <tr key={feature} className={klass === "Strong separator" ? "strong" : ""}>
              <td>{feature}</td><td>{affected}</td><td>{healthy}</td><td>{klass}</td><td>{meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="pilot-note split">
        <b>Useful counterexample</b>
        <p>EX11 has autonomy 2.7 and localization L4 but is healthy in Zone C. That means L4 alone may not explain the issue.</p>
      </div>
    </div>
  );
}

function Explanations() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Explanations"
        title="Keep competing explanations explicit."
        subtitle="Each hypothesis carries support, contradiction, missing evidence and the next check that would separate it from the others."
      />
      <div className="pilot-hypotheses">
        {hypotheses.map((hypothesis) => (
          <article key={hypothesis.id} className={hypothesis.strength.toLowerCase()}>
            <header>
              <span>{hypothesis.id}</span>
              <b>{hypothesis.strength}</b>
            </header>
            <h2>{hypothesis.title}</h2>
            <dl>
              <div><dt>For</dt><dd>{hypothesis.support.join("; ")}</dd></div>
              <div><dt>Against</dt><dd>{hypothesis.against.join("; ")}</dd></div>
              <div><dt>Missing</dt><dd>{hypothesis.missing}</dd></div>
              <div><dt>Next check</dt><dd>{hypothesis.next}</dd></div>
            </dl>
          </article>
        ))}
      </div>
      <div className="planner-card">
        <span>Evidence planner</span>
        <h2>Observe EX11 in Zone B</h2>
        <p>It separates the two strongest remaining explanations without changing a production machine.</p>
        <div><b>Cost: low</b><b>Risk: low</b><b>Time: ~15 min</b></div>
      </div>
    </div>
  );
}

function Scoring() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Fast Decision Scorer"
        title="Score the options before the model writes a long answer."
        subtitle="The scorer is replaceable. It ranks hypotheses, next checks and tool routes on top of the Field Case Graph. Low confidence triggers deeper reasoning or human review."
      />
      <div className="scoring-layout">
        <ScorePanel title="Hypothesis ranking" rows={scoringRows} />
        <ScorePanel title="Next-check ranking" rows={nextCheckRows} />
        <ScorePanel title="Tool routing" rows={routingRows} />
      </div>
      <div className="system-loop">
        <article>
          <span>System 1</span>
          <h2>Fast scoring</h2>
          <p>Cheap, low-latency probability over typed options.</p>
        </article>
        <article>
          <span>Gate</span>
          <h2>Confidence check</h2>
          <p>If the score is weak or conflicted, escalate the case.</p>
        </article>
        <article>
          <span>System 2</span>
          <h2>Deep reasoning or human review</h2>
          <p>Use a frontier model or engineer only when the case needs it.</p>
        </article>
      </div>
      <div className="pilot-note split">
        <b>Product boundary</b>
        <p>The scorer is not the product moat. The durable asset is the Field Case: options, attached evidence, missing evidence, intervention, outcome and precedent.</p>
      </div>
    </div>
  );
}

function ScorePanel({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <article className="score-panel">
      <span>{title}</span>
      <div>
        {rows.map(([label, score, reason]) => (
          <section key={label}>
            <header>
              <b>{label}</b>
              <strong>{score}</strong>
            </header>
            <div className="score-track"><i style={{ width: `${Math.round(Number(score) * 100)}%` }} /></div>
            <p>{reason}</p>
          </section>
        ))}
      </div>
    </article>
  );
}

function McpTools() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Veyra MCP"
        title="The product surface is a set of field-case tools any frontier model can call."
        subtitle="A robotics engineer can ask from Claude, Codex, ChatGPT or an internal agent. Veyra returns compact, source-linked case answers."
      />
      <div className="mcp-demo">
        <aside>
          <span>Engineer asks</span>
          <h2>Why are EX03, EX05 and EX08 stopping while the others are healthy?</h2>
          <p>The model calls tools instead of guessing from a chat transcript.</p>
        </aside>
        <section>
          {mcpTools.map(([tool, description]) => (
            <article key={tool}>
              <code>{tool}()</code>
              <p>{description}</p>
            </article>
          ))}
        </section>
      </div>
      <div className="pilot-note split">
        <b>Positioning</b>
        <p>Alloy can be an upstream robot-data source. Veyra's core object is the cross-system Field Case: machine evidence, engineering changes, human action, physical outcome and precedent.</p>
      </div>
    </div>
  );
}

function ActionOutcome() {
  return (
    <div className="pilot-screen">
      <ScreenTitle
        eyebrow="Action & Outcome"
        title="Record the intervention and outcome so the next model query starts ahead."
        subtitle="The case graph compounds when it includes what the team did, what happened afterwards and what remains unresolved."
      />
      <div className="action-outcome-grid">
        <article>
          <span>Veyra suggested</span>
          <h2>Observe EX11 in Zone B</h2>
          <p>Highest information value. Low disruption.</p>
        </article>
        <article>
          <span>Team chose</span>
          <h2>Roll back L4 on EX03, EX05 and EX08</h2>
          <p>Production pressure. Faster recovery mattered more than clean diagnosis.</p>
        </article>
        <article>
          <span>Observed result</span>
          <h2>All three recovered after rollback</h2>
          <p>Supports a localization-related explanation. Does not prove L4 alone versus L4 plus Zone B.</p>
        </article>
        <article>
          <span>Reusable precedent</span>
          <h2>Next case starts with this record</h2>
          <p>Previous evidence, team action, observed outcome and unresolved question are attached to the next similar case.</p>
        </article>
      </div>
    </div>
  );
}

function ScreenTitle({ eyebrow, title, subtitle }: { eyebrow: string; title: string; subtitle: string }) {
  return (
    <header className="pilot-title">
      <span>{eyebrow}</span>
      <h2>{title}</h2>
      <p>{subtitle}</p>
    </header>
  );
}
