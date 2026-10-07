"use client";

import Link from "next/link";
import { useState } from "react";

type SurfaceKey = "case" | "evidence" | "reasoning" | "action" | "precedent";

const surfaces: Array<{ key: SurfaceKey; label: string }> = [
  { key: "case", label: "Case" },
  { key: "evidence", label: "Evidence" },
  { key: "reasoning", label: "Reasoning" },
  { key: "action", label: "Action" },
  { key: "precedent", label: "Precedent" },
];

const machines = [
  ["EX01", "healthy"], ["EX02", "healthy"], ["EX03", "affected"], ["EX04", "healthy"],
  ["EX05", "affected"], ["EX06", "healthy"], ["EX07", "healthy"], ["EX08", "affected"],
  ["EX09", "healthy"], ["EX10", "healthy"], ["EX11", "watch"], ["EX12", "healthy"],
];

const graphNodes = [
  ["Signal", "3 safe-stops"],
  ["Changes", "release · L4 · Zone B"],
  ["Contrast", "affected vs healthy"],
  ["Hypotheses", "3 still open"],
  ["Next evidence", "EX11 in Zone B"],
  ["Intervention", "rollback selected"],
  ["Outcome", "3/3 recovered"],
  ["Precedent", "FC034 inherits context"],
];

const exposureRows = [
  ["Autonomy 2.7", "3/3", "9/9", "Common"],
  ["Localization L4", "3/3", "2/9", "Strong"],
  ["Zone B", "3/3", "1/9", "Strong"],
  ["L4 + Zone B", "3/3", "0/9", "Very strong"],
  ["LiDAR firmware 5.3", "3/3", "7/9", "Weak"],
];

const hypotheses = [
  {
    id: "H1",
    title: "Localization L4 alone",
    status: "Possible",
    supports: "3/3 affected machines use L4",
    against: "EX11 also uses L4 and has no known safe-stop",
    missing: "EX11 behavior in Zone B",
  },
  {
    id: "H2",
    title: "L4 plus Zone B",
    status: "Strong",
    supports: "3/3 affected machines share both conditions",
    against: "No direct counterexample yet",
    missing: "Healthy L4 machine observed in Zone B",
  },
  {
    id: "H3",
    title: "LiDAR firmware 5.3",
    status: "Weak",
    supports: "Changed before the first safe-stop",
    against: "7/9 healthy machines also use 5.3",
    missing: "No useful next check",
  },
];

const evidenceGaps = [
  ["EX11 in Zone B", "High", "Low", "Low", "15m"],
  ["Rollback EX03", "Medium", "Medium", "Medium", "30m"],
  ["Full LiDAR recalibration", "Low", "High", "Medium", "2h"],
];

const agentTrace = [
  "get_case(FC021)",
  "list_changes(window=-24h)",
  "compare_assets(affected, healthy)",
  "test_exposure(Localization L4)",
  "test_exposure(Zone B)",
  "find_counterexamples()",
  "rank_next_checks()",
];

const caseDiff = [
  ["14:11", "3 safe-stops", "No dominant hypothesis"],
  ["14:21", "L4 enriched in affected cohort", "L4 alone still possible"],
  ["14:24", "EX11 found healthy with L4", "L4 alone weakened"],
  ["14:26", "Zone B interaction rises", "H2 strongest"],
  ["14:27", "Operations chooses rollback", "Decision recorded"],
  ["14:51", "3/3 recover", "Localization-related explanation strengthened"],
];

function MiniFleet() {
  return (
    <div className="workbench-fleet-map" aria-label="machine cohort">
      {machines.map(([id, state]) => (
        <span key={id} className={state}>
          <i />
          <b>{id}</b>
        </span>
      ))}
    </div>
  );
}

export default function ProductDemoPage() {
  const [surface, setSurface] = useState<SurfaceKey>("case");
  const activeIndex = surfaces.findIndex((item) => item.key === surface);

  return (
    <main className="field-workbench">
      <header className="field-topbar">
        <Link className="pilot-brand" href="/veyra"><span>V</span> Veyra</Link>
        <nav aria-label="workbench surfaces">
          {surfaces.map((item) => (
            <button
              key={item.key}
              className={surface === item.key ? "active" : ""}
              onClick={() => setSurface(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <section className="field-case-header">
        <div>
          <span>Field Case Workbench</span>
          <h1>FC-021 · Safe-stop cluster</h1>
        </div>
        <div className="case-metrics">
          <b>3 / 12 affected</b>
          <b>first signal 14:11</b>
          <b>3 hypotheses open</b>
          <b>2 evidence gaps</b>
          <b>status open</b>
        </div>
      </section>

      <section className="field-workspace">
        <aside className="case-object-panel">
          <div className="case-object-title">
            <span>Case graph</span>
            <b>Evidence → hypothesis → action → outcome</b>
          </div>
          <div className="case-graph-list">
            {graphNodes.map(([label, value], index) => (
              <button key={label} className={index === activeIndex ? "active" : ""}>
                <span>{label}</span>
                <b>{value}</b>
              </button>
            ))}
          </div>
          <MiniFleet />
        </aside>

        <section className="workbench-main">
          {surface === "case" && (
            <div className="surface-grid case-surface">
              <article className="workbench-card wide">
                <span>Case state</span>
                <h2>All 12 machines received autonomy 2.7. Only 3 entered safe-stop.</h2>
                <p>The release alone does not explain the issue. Veyra keeps the case as linked objects, not as a summary paragraph.</p>
              </article>
              <article className="workbench-card">
                <span>Source provenance</span>
                <dl className="source-list">
                  <div><dt>Telemetry</dt><dd>safe_stop event stream</dd></div>
                  <div><dt>Deployment</dt><dd>autonomy 2.7 rollout</dd></div>
                  <div><dt>Human note</dt><dd>operator review at 14:26</dd></div>
                </dl>
              </article>
              <article className="workbench-card">
                <span>Known now</span>
                <dl className="source-list">
                  <div><dt>Affected</dt><dd>EX03 · EX05 · EX08</dd></div>
                  <div><dt>Useful comparator</dt><dd>EX11</dd></div>
                  <div><dt>Open question</dt><dd>L4 alone or L4 plus Zone B</dd></div>
                </dl>
              </article>
              <article className="workbench-card wide">
                <span>Case state diff</span>
                <div className="state-diff">
                  {caseDiff.map(([time, evidence, belief]) => (
                    <div key={time}>
                      <b>{time}</b>
                      <span>{evidence}</span>
                      <em>{belief}</em>
                    </div>
                  ))}
                </div>
              </article>
            </div>
          )}

          {surface === "evidence" && (
            <div className="surface-grid evidence-surface">
              <article className="workbench-card wide">
                <span>Contrast Lab</span>
                <h2>Affected cohort vs healthy comparators</h2>
                <table className="contrast-table">
                  <thead>
                    <tr>
                      <th>Exposure</th>
                      <th>Affected</th>
                      <th>Healthy</th>
                      <th>Signal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exposureRows.map(([exposure, affected, healthy, signal]) => (
                      <tr key={exposure}>
                        <td>{exposure}</td>
                        <td>{affected}</td>
                        <td>{healthy}</td>
                        <td><b>{signal}</b></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </article>
              <article className="workbench-card">
                <span>Why EX11 matters</span>
                <h3>Counterexample</h3>
                <p>EX11 has autonomy 2.7 and localization L4, but no known safe-stop at decision time. That weakens L4 alone.</p>
              </article>
              <article className="workbench-card">
                <span>Source links</span>
                <dl className="source-list">
                  <div><dt>event_time</dt><dd>14:11 · first safe-stop</dd></div>
                  <div><dt>known_at</dt><dd>14:26 · review opened</dd></div>
                  <div><dt>ingested_at</dt><dd>14:26:04 · case updated</dd></div>
                </dl>
              </article>
            </div>
          )}

          {surface === "reasoning" && (
            <div className="surface-grid reasoning-surface">
              <article className="workbench-card wide">
                <span>Hypothesis board</span>
                <h2>Veyra maintains the explanation space.</h2>
                <div className="hypothesis-board">
                  {hypotheses.map((hypothesis) => (
                    <article key={hypothesis.id} className={hypothesis.status.toLowerCase()}>
                      <header>
                        <span>{hypothesis.id}</span>
                        <b>{hypothesis.status}</b>
                      </header>
                      <h3>{hypothesis.title}</h3>
                      <dl>
                        <div><dt>Supports</dt><dd>{hypothesis.supports}</dd></div>
                        <div><dt>Against</dt><dd>{hypothesis.against}</dd></div>
                        <div><dt>Missing</dt><dd>{hypothesis.missing}</dd></div>
                      </dl>
                    </article>
                  ))}
                </div>
              </article>
              <article className="workbench-card wide">
                <span>Evidence Gap Map</span>
                <table className="contrast-table compact">
                  <thead>
                    <tr><th>Missing evidence</th><th>Info value</th><th>Cost</th><th>Risk</th><th>Time</th></tr>
                  </thead>
                  <tbody>
                    {evidenceGaps.map(([gap, value, cost, risk, time]) => (
                      <tr key={gap}><td>{gap}</td><td><b>{value}</b></td><td>{cost}</td><td>{risk}</td><td>{time}</td></tr>
                    ))}
                  </tbody>
                </table>
              </article>
            </div>
          )}

          {surface === "action" && (
            <div className="surface-grid action-surface">
              <article className="workbench-card wide">
                <span>Next Evidence Ranking</span>
                <h2>Observe EX11 in Zone B.</h2>
                <p>Why this first: it separates H1 from H2 with one low-risk observation, before changing another production machine.</p>
                <div className="ranking-list">
                  <div><b>1</b><span>Observe EX11 in Zone B</span><em>High info value · low risk · 15m</em></div>
                  <div><b>2</b><span>Roll back L4 on EX03</span><em>Medium info value · medium disruption</em></div>
                  <div><b>3</b><span>Full LiDAR recalibration</span><em>Low info value · high cost</em></div>
                </div>
              </article>
              <article className="workbench-card">
                <span>Agent trace</span>
                <div className="agent-trace-list">
                  {agentTrace.map((call) => <code key={call}>✓ {call}</code>)}
                </div>
              </article>
              <article className="workbench-card">
                <span>Human decision</span>
                <h3>Team chose rollback</h3>
                <p>Veyra suggested the cleanest check. Operations chose faster recovery because production pressure mattered.</p>
                <dl className="source-list">
                  <div><dt>Owner</dt><dd>Operations lead</dd></div>
                  <div><dt>Recorded</dt><dd>14:27</dd></div>
                  <div><dt>Rationale</dt><dd>restore service first</dd></div>
                </dl>
              </article>
            </div>
          )}

          {surface === "precedent" && (
            <div className="surface-grid precedent-surface">
              <article className="workbench-card wide">
                <span>Precedent injection</span>
                <h2>FC-034 does not start from zero.</h2>
                <p>A similar safe-stop appears 12 days later. Veyra brings forward the prior hypothesis, action, outcome and unresolved question.</p>
              </article>
              <article className="workbench-card">
                <span>Matched precedent</span>
                <h3>FC-021</h3>
                <dl className="source-list">
                  <div><dt>Shared</dt><dd>safe-stop · L4 · same family</dd></div>
                  <div><dt>Action</dt><dd>rollback L4</dd></div>
                  <div><dt>Outcome</dt><dd>3/3 recovered</dd></div>
                </dl>
              </article>
              <article className="workbench-card">
                <span>Inherited into FC-034</span>
                <h3>Starting checks</h3>
                <ol className="precedent-list">
                  <li>Confirm zone condition</li>
                  <li>Inspect localization config</li>
                  <li>Compare against EX11 precedent</li>
                </ol>
              </article>
            </div>
          )}
        </section>

        <aside className="operator-panel">
          <span>Operator</span>
          <h2>Why did only 3 stop?</h2>
          <div className="agent-trace-list">
            {agentTrace.slice(0, 5).map((call) => <code key={call}>✓ {call}</code>)}
          </div>
          <div className="operator-result">
            <b>Current result</b>
            <p>L4 plus Zone B best fits current evidence. EX11 in Zone B is the next useful check.</p>
          </div>
        </aside>
      </section>
    </main>
  );
}
