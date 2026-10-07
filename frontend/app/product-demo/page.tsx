"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type ObjectKey =
  | "timeline"
  | "machines"
  | "changes"
  | "evidence"
  | "hypotheses"
  | "checks"
  | "actions"
  | "outcomes";

type HypothesisKey = "h2" | "h1" | "h3";
type TimelineMode = "reality" | "known";
type ComparatorKey = "healthy" | "same-release" | "same-localization" | "same-site" | "custom";
type KnownCursor = "14:11" | "14:18" | "14:23" | "14:24" | "14:26" | "14:27";

const objectNav: Array<{ key: ObjectKey; label: string; count: string }> = [
  { key: "timeline", label: "Timeline", count: "8" },
  { key: "machines", label: "Machines", count: "12" },
  { key: "changes", label: "Changes", count: "18" },
  { key: "evidence", label: "Evidence", count: "7" },
  { key: "hypotheses", label: "Hypotheses", count: "3" },
  { key: "checks", label: "Checks", count: "2" },
  { key: "actions", label: "Actions", count: "1" },
  { key: "outcomes", label: "Outcomes", count: "1" },
];

const machines = [
  { id: "EX01", status: "healthy", release: "2.7", localization: "L3", zone: "A", lidar: "5.2", last: "map sync", exposure: "none" },
  { id: "EX02", status: "healthy", release: "2.7", localization: "L3", zone: "B", lidar: "5.3", last: "firmware", exposure: "partial" },
  { id: "EX03", status: "affected", release: "2.7", localization: "L4", zone: "B", lidar: "5.3", last: "safe-stop 14:11", exposure: "full" },
  { id: "EX04", status: "healthy", release: "2.7", localization: "L4", zone: "C", lidar: "5.2", last: "calibration", exposure: "partial" },
  { id: "EX05", status: "affected", release: "2.7", localization: "L4", zone: "B", lidar: "5.3", last: "safe-stop 14:18", exposure: "full" },
  { id: "EX06", status: "healthy", release: "2.7", localization: "L3", zone: "B", lidar: "5.3", last: "mission start", exposure: "partial" },
  { id: "EX07", status: "healthy", release: "2.7", localization: "L3", zone: "C", lidar: "5.3", last: "none", exposure: "none" },
  { id: "EX08", status: "affected", release: "2.7", localization: "L4", zone: "B", lidar: "5.3", last: "safe-stop 14:26", exposure: "full" },
  { id: "EX09", status: "healthy", release: "2.7", localization: "L3", zone: "B", lidar: "5.3", last: "planner fallback", exposure: "partial" },
  { id: "EX10", status: "healthy", release: "2.7", localization: "L3", zone: "A", lidar: "5.2", last: "none", exposure: "none" },
  { id: "EX11", status: "watch", release: "2.7", localization: "L4", zone: "C", lidar: "5.3", last: "counterexample", exposure: "counter" },
  { id: "EX12", status: "healthy", release: "2.7", localization: "L3", zone: "C", lidar: "5.3", last: "none", exposure: "none" },
];

const changes = [
  { name: "Autonomy 2.7", affected: "3/3", healthy: "9/9", result: "common change", use: "low" },
  { name: "Localization L4", affected: "3/3", healthy: "2/9", result: "separates some machines", use: "high" },
  { name: "Loading zone B", affected: "3/3", healthy: "1/9", result: "strong exposure", use: "high" },
  { name: "LiDAR firmware 5.3", affected: "3/3", healthy: "7/9", result: "too common", use: "low" },
  { name: "L4 + Zone B", affected: "3/3", healthy: "0/9", result: "best current split", use: "high" },
];

const evidence = [
  { source: "Telemetry", record: "safe_stop events", time: "14:11, 14:18, 14:26", status: "observed" },
  { source: "Deployment", record: "autonomy 2.7 rollout", time: "13:52", status: "observed" },
  { source: "Config", record: "localization L4", time: "14:23 known", status: "observed" },
  { source: "Map", record: "zone B mission segment", time: "14:24 known", status: "observed" },
  { source: "Operator note", record: "review opened", time: "14:26", status: "human note" },
  { source: "Runtime history", record: "EX11 in zone B", time: "missing", status: "gap" },
  { source: "Prior cases", record: "37 searched", time: "14:25", status: "retrieved" },
];

const hypotheses: Record<HypothesisKey, {
  id: string;
  label: string;
  state: string;
  supports: string[];
  against: string[];
  missing: string;
  mind: string;
}> = {
  h2: {
    id: "H2",
    label: "L4 + Zone B",
    state: "Strong",
    supports: ["3/3 affected share both conditions", "0/9 healthy share both at decision time"],
    against: ["No healthy L4 machine has yet been observed in Zone B"],
    missing: "Healthy L4 machine observed in Zone B",
    mind: "EX11 remains healthy in Zone B",
  },
  h1: {
    id: "H1",
    label: "L4 alone",
    state: "Possible",
    supports: ["3/3 affected use L4"],
    against: ["EX11 uses L4 and has no known safe-stop"],
    missing: "EX11 behavior in Zone B",
    mind: "Multiple healthy L4 machines remain stable in the same zone",
  },
  h3: {
    id: "H3",
    label: "LiDAR 5.3",
    state: "Weak",
    supports: ["Changed before the first safe-stop"],
    against: ["7/9 healthy machines also use 5.3"],
    missing: "No useful next check",
    mind: "A healthy LiDAR 5.3 cohort shows the same exposure without safe-stop",
  },
};

const comparators: Record<ComparatorKey, { label: string; cohort: string; count: string }> = {
  healthy: { label: "Healthy fleet", cohort: "all healthy machines", count: "9" },
  "same-release": { label: "Same release", cohort: "healthy autonomy 2.7 machines", count: "9" },
  "same-localization": { label: "Same localization", cohort: "healthy L4 machines", count: "2" },
  "same-site": { label: "Same site", cohort: "healthy zone B machines", count: "1" },
  custom: { label: "Custom cohort", cohort: "EX04 EX06 EX11", count: "3" },
};

const realityEvents = [
  ["12:40", "Sensor recalibration completed"],
  ["13:52", "Autonomy 2.7 rollout complete"],
  ["14:09", "EX11 enters zone B without uploaded runtime segment"],
  ["14:11", "EX03 safe-stop"],
  ["14:18", "EX05 safe-stop"],
  ["14:26", "EX08 safe-stop"],
  ["14:27", "Team records rollback decision"],
  ["14:51", "3/3 affected machines recovered after rollback"],
];

const knownEvents = [
  ["13:52", "Rollout known"],
  ["14:11", "First safe-stop known"],
  ["14:18", "Second safe-stop known"],
  ["14:23", "Localization L4 discovered"],
  ["14:24", "EX11 counterexample found"],
  ["14:26", "Third safe-stop known"],
  ["14:27", "Decision snapshot sealed"],
  ["14:31", "Delayed EX11 runtime arrives"],
];

const railEvents: Array<[KnownCursor, string]> = [
  ["14:11", "first stop"],
  ["14:18", "second stop"],
  ["14:23", "L4 discovered"],
  ["14:24", "EX11 counterexample"],
  ["14:26", "third stop"],
  ["14:27", "decision"],
];

const precedentRows = [
  ["FC-021", "0.89", "L4 + safe-stop + same family", "Rollback L4", "3/3 recovered"],
  ["FC-008", "0.61", "localization drift", "Recalibration", "recovered"],
  ["FC-014", "0.43", "safe-stop", "Remote restart", "no effect"],
];

const lensReadout: Record<HypothesisKey, string[]> = {
  h2: ["affected match 3/3", "healthy match 0/9", "counterexamples 0", "missing comparator 1"],
  h1: ["affected match 3/3", "healthy match 2/9", "counterexamples 1", "missing comparator 1"],
  h3: ["affected match 3/3", "healthy match 7/9", "counterexamples 7", "missing comparator 0"],
};

function getBeliefState(cursor: KnownCursor) {
  if (cursor === "14:11" || cursor === "14:18") {
    return {
      label: "BELIEF STATE · early signal",
      h1: "not formed",
      h2: "not formed",
      h3: "possible",
      detail: "Only safe-stop evidence is known. Change context has not been joined yet.",
    };
  }
  if (cursor === "14:23") {
    return {
      label: "BELIEF STATE · 14:23",
      h1: "stronger",
      h2: "not yet formed",
      h3: "weak",
      detail: "L4 is known. EX11 counterexample and zone exposure are not yet available.",
    };
  }
  if (cursor === "14:24") {
    return {
      label: "BELIEF STATE · 14:24",
      h1: "weakened",
      h2: "emerging",
      h3: "weak",
      detail: "EX11 weakens L4 alone. Zone B begins to matter as the next distinction.",
    };
  }
  return {
    label: "BELIEF STATE · 14:27",
    h1: "possible",
    h2: "strong",
    h3: "weak",
    detail: "At decision time, H2 is strongest and EX11 in Zone B is the highest value gap.",
  };
}

function lensClass(machine: typeof machines[number], hypothesis: HypothesisKey) {
  if (machine.status === "affected") return "affected";
  if (hypothesis === "h1" && machine.localization === "L4") return "counter";
  if (hypothesis === "h2" && machine.localization === "L4" && machine.zone === "B") return "counter";
  if (hypothesis === "h3" && machine.lidar === "5.3") return "counter";
  return "muted";
}

export default function ProductDemoPage() {
  const [objectKey, setObjectKey] = useState<ObjectKey>("machines");
  const [hypothesisKey, setHypothesisKey] = useState<HypothesisKey>("h2");
  const [comparatorKey, setComparatorKey] = useState<ComparatorKey>("healthy");
  const [timelineMode, setTimelineMode] = useState<TimelineMode>("known");
  const [knownCursor, setKnownCursor] = useState<KnownCursor>("14:27");
  const [traceOpen, setTraceOpen] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const activeHypothesis = hypotheses[hypothesisKey];
  const activeComparator = comparators[comparatorKey];
  const beliefState = getBeliefState(knownCursor);
  const timeline = timelineMode === "reality" ? realityEvents : knownEvents;

  const workspaceTitle = useMemo(() => {
    const titles: Record<ObjectKey, string> = {
      timeline: "Timeline",
      machines: "Machine contrast",
      changes: "Change matrix",
      evidence: "Evidence sources",
      hypotheses: "Explanation lattice",
      checks: "Next Evidence",
      actions: "Decision ledger",
      outcomes: "Outcome and precedent",
    };
    return titles[objectKey];
  }, [objectKey]);

  return (
    <main className="ide-shell">
      <header className="ide-topbar">
        <Link className="ide-brand" href="/veyra"><span>V</span> Veyra</Link>
        <button
          className={isAnalyzing ? "ide-command analyzing" : "ide-command"}
          onClick={() => {
            setIsAnalyzing(true);
            setObjectKey("machines");
            setHypothesisKey("h2");
            setComparatorKey("same-localization");
            setKnownCursor("14:27");
            window.setTimeout(() => setIsAnalyzing(false), 900);
          }}
        >
          <span>⌘ Ask Veyra</span>
          <strong>show what separates affected from healthy machines</strong>
          <em>{isAnalyzing ? "Analyzing" : "Analyze"}</em>
        </button>
        <div className="ide-source-counts">
          <b>Sources 7</b>
          <b>Gaps 2</b>
          <b>Hypotheses 3</b>
        </div>
      </header>

      <section className="ide-casebar">
        <div>
          <span>FC-021</span>
          <h1>Safe-stop cluster</h1>
        </div>
        <b className="open">OPEN</b>
        <b>12 MACHINES</b>
        <b>7 SOURCES</b>
        <b>18 CHANGES</b>
        <b>UPDATED 14:27:08</b>
      </section>

      <section className="ide-grid">
        <aside className="ide-left">
          <div className="ide-left-title">Case objects</div>
          {objectNav.map((item) => (
            <button
              key={item.key}
              className={objectKey === item.key ? "active" : ""}
              onClick={() => setObjectKey(item.key)}
            >
              <span>{item.label}</span>
              <b>{item.count}</b>
            </button>
          ))}
          <div className="ide-mini-status">
            <span>Cohort control</span>
            <div className="cohort-group">
              <label>Affected</label>
              <p>EX03 EX05 EX08</p>
            </div>
            <div className="cohort-group">
              <label htmlFor="comparator">Comparator</label>
              <select
                id="comparator"
                value={comparatorKey}
                onChange={(event) => setComparatorKey(event.target.value as ComparatorKey)}
              >
                {Object.entries(comparators).map(([key, value]) => (
                  <option key={key} value={key}>{value.label}</option>
                ))}
              </select>
              <p>{activeComparator.count} machines · {activeComparator.cohort}</p>
            </div>
            <div className="cohort-group">
              <label>Watch</label>
              <p>EX11</p>
            </div>
          </div>
        </aside>

        <section className="ide-center">
          <div className="ide-center-head">
            <div>
              <span>{workspaceTitle}</span>
              <h2>
                {objectKey === "checks"
                  ? "Next Evidence"
                  : objectKey === "machines"
                    ? "Compare affected machines against healthy comparators"
                    : "Active field case data"}
              </h2>
              {objectKey === "machines" && (
                <div className="compare-summary">
                  <span>LENS: {activeHypothesis.id}</span>
                  <span>Comparator: {activeComparator.label}</span>
                  {lensReadout[hypothesisKey].map((item) => <span key={item}>{item}</span>)}
                </div>
              )}
              {objectKey === "checks" && (
                <p className="workspace-note">Ranked by information value, cost and operational risk.</p>
              )}
            </div>
            <div className="ide-lens-tabs" aria-label="hypothesis lens">
              {(Object.keys(hypotheses) as HypothesisKey[]).map((key) => (
                <button
                  key={key}
                  className={hypothesisKey === key ? "active" : ""}
                  onClick={() => setHypothesisKey(key)}
                >
                  {hypotheses[key].id} {hypotheses[key].label}
                </button>
              ))}
            </div>
          </div>

          {(objectKey === "machines" || objectKey === "hypotheses") && (
            <div className="ide-table-wrap">
              <table className="ide-data-table">
                <thead>
                  <tr>
                    <th>Machine</th>
                    <th>Status</th>
                    <th>Release</th>
                    <th>Localization</th>
                    <th>Zone</th>
                    <th>LiDAR</th>
                    <th>Key difference</th>
                  </tr>
                </thead>
                <tbody>
                  {machines.map((machine) => (
                    <tr key={machine.id} className={lensClass(machine, hypothesisKey)}>
                      <td title="source_id ASSET_REGISTRY"><b>{machine.id}</b></td>
                      <td title="source telemetry_safe_stop">{machine.status}</td>
                      <td title="source deployment_config · event_time 13:52 · known_at 13:52">{machine.release}</td>
                      <td title="source localization_config · known_at 14:23">{machine.localization}</td>
                      <td title="source mission_map · known_at 14:24">{machine.zone}</td>
                      <td title="source sensor_manifest">{machine.lidar}</td>
                      <td title="open source evidence">{machine.last}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {objectKey === "changes" && (
            <div className="ide-table-wrap">
              <table className="ide-data-table">
                <thead><tr><th>Change</th><th>Affected</th><th>Healthy</th><th>Result</th><th>Use</th></tr></thead>
                <tbody>
                  {changes.map((change) => (
                    <tr key={change.name} className={change.use === "high" ? "affected" : "muted"}>
                      <td><b>{change.name}</b></td>
                      <td>{change.affected}</td>
                      <td>{change.healthy}</td>
                      <td>{change.result}</td>
                      <td>{change.use}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {objectKey === "evidence" && (
            <div className="ide-table-wrap">
              <table className="ide-data-table">
                <thead><tr><th>Source</th><th>Record</th><th>Time</th><th>Status</th></tr></thead>
                <tbody>
                  {evidence.map((row) => (
                    <tr key={row.source} className={row.status === "gap" ? "counter" : ""}>
                      <td><b>{row.source}</b></td>
                      <td>{row.record}</td>
                      <td>{row.time}</td>
                      <td>{row.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {objectKey === "timeline" && (
            <div className="ide-timeline-mode">
              <div className="timeline-toggle">
                <button className={timelineMode === "reality" ? "active" : ""} onClick={() => setTimelineMode("reality")}>Reality</button>
                <button className={timelineMode === "known" ? "active" : ""} onClick={() => setTimelineMode("known")}>What the team knew</button>
              </div>
              <div className="ide-event-list large">
                {timeline.map(([time, event]) => (
                  <div key={`${timelineMode}-${time}`}>
                    <b>{time}</b>
                    <span>{event}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {objectKey === "checks" && (
            <div className="ide-next-check">
              <div>
                <span>Next Evidence</span>
                <h3>01 EX11 → Zone B</h3>
                <p>Separates H1 / H2. Info high. Cost low. Risk low. About 15 min.</p>
                <div className="next-evidence-rank">
                  <button className="active">01 EX11 → Zone B</button>
                  <button>02 rollback EX03</button>
                  <button>03 LiDAR recalibration</button>
                </div>
              </div>
              <div className="branch">
                <section><b>If EX11 safe-stops</b><span>H2 strengthens</span></section>
                <section><b>If EX11 stays healthy</b><span>H2 weakens</span></section>
              </div>
            </div>
          )}

          {objectKey === "actions" && (
            <div className="ide-table-wrap">
              <table className="ide-data-table">
                <thead><tr><th>Time</th><th>Veyra suggested</th><th>Team chose</th><th>Reason</th><th>Outcome</th></tr></thead>
                <tbody>
                  <tr className="affected">
                    <td>14:27</td>
                    <td>Observe EX11 in Zone B</td>
                    <td><b>Rollback L4</b></td>
                    <td>restore service first</td>
                    <td>3/3 recovered</td>
                  </tr>
                </tbody>
              </table>
              <div className="belief-update">
                <span>Hypothesis update after outcome</span>
                <b>Localization-related family stronger</b>
                <p>L4 alone remains unresolved. L4 + Zone B remains unresolved. The rollback result supports the family, not a single proven cause.</p>
              </div>
            </div>
          )}

          {objectKey === "outcomes" && (
            <div className="ide-table-wrap">
              <table className="ide-data-table">
                <thead><tr><th>Case</th><th>Similarity</th><th>Shared conditions</th><th>Intervention</th><th>Outcome</th></tr></thead>
                <tbody>
                  {precedentRows.map(([id, score, shared, intervention, outcome], index) => (
                    <tr key={id} className={index === 0 ? "affected" : ""}>
                      <td><b>{id}</b></td>
                      <td>{score}</td>
                      <td>{shared}</td>
                      <td>{intervention}</td>
                      <td>{outcome}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="precedent-operation">
                <button>Start FC-034 from FC-021</button>
                <span>Imports hypothesis, unresolved question, successful intervention and suggested checks.</span>
              </div>
            </div>
          )}
        </section>

        <aside className="ide-right">
          <section className="reasoning-panel">
            <span>{beliefState.label}</span>
            <div className="uncertainty-pill">
              <b>UNCERTAINTY 02</b>
              <strong>2 unresolved distinctions</strong>
              <small>01 L4 alone vs L4 × Zone B</small>
              <small>02 localization-related vs unknown interaction</small>
            </div>
            {(Object.keys(hypotheses) as HypothesisKey[]).map((key) => {
              const item = hypotheses[key];
              const temporalState = key === "h1" ? beliefState.h1 : key === "h2" ? beliefState.h2 : beliefState.h3;
              return (
                <button key={key} className={hypothesisKey === key ? "active" : ""} onClick={() => setHypothesisKey(key)}>
                  <b>{item.id}</b>
                  <strong>{item.label}</strong>
                  <em>{temporalState}</em>
                </button>
              );
            })}
            <p className="belief-note">{beliefState.detail}</p>
          </section>

          <section className="reason-detail">
            <span>{activeHypothesis.id} detail</span>
            <h3>{activeHypothesis.label}</h3>
            <dl>
              <div><dt>Supports</dt><dd>{activeHypothesis.supports.join("; ")}</dd></div>
              <div><dt>Against</dt><dd>{activeHypothesis.against.join("; ")}</dd></div>
              <div><dt>Missing</dt><dd>{activeHypothesis.missing}</dd></div>
              <div><dt>Would change our mind</dt><dd>{activeHypothesis.mind}</dd></div>
            </dl>
          </section>

          <section className="next-gap-panel">
            <span>Highest value gap</span>
            <h3>EX11 in Zone B</h3>
            <p>Separates the two strongest remaining explanations. Estimated 15 minutes, low operational risk.</p>
            <div>
              <button onClick={() => setObjectKey("evidence")}>Inspect evidence</button>
              <button onClick={() => setObjectKey("checks")}>Create check</button>
            </div>
            <button
              className="test-explanation"
              onClick={() => {
                setObjectKey("checks");
                setHypothesisKey("h2");
              }}
            >
              Test this explanation
            </button>
          </section>

          <section className="provenance-panel">
            <span>Why this result?</span>
            <p className="provenance-summary">4 sources · 12 machines · 18 changes · 1 counterexample · 37 historical cases searched</p>
            <button className="trace-button" onClick={() => setTraceOpen(!traceOpen)}>
              {traceOpen ? "Hide execution trace" : "View execution trace"}
            </button>
            {traceOpen && (
              <>
                <dl>
                  <div><dt>Evidence sources queried</dt><dd>4</dd></div>
                  <div><dt>Machines compared</dt><dd>12</dd></div>
                  <div><dt>Changes evaluated</dt><dd>18</dd></div>
                  <div><dt>Counterexamples found</dt><dd>1</dd></div>
                  <div><dt>Historical cases searched</dt><dd>37</dd></div>
                </dl>
                <div className="trace-list">
                  <code>case.get(FC-021)</code>
                  <code>evidence.query(window=24h)</code>
                  <code>assets.compare(affected, healthy)</code>
                  <code>hypotheses.rank()</code>
                  <code>checks.rank_by_information_value()</code>
                </div>
              </>
            )}
          </section>
        </aside>
      </section>

      <footer className="ide-bottom">
        <div className="ide-event-rail">
          {railEvents.map(([time, event]) => (
            <button
              key={`bottom-${time}`}
              className={knownCursor === time ? "active" : ""}
              onClick={() => {
                setTimelineMode("known");
                setKnownCursor(time);
              }}
            >
              <b>{time}</b>
              <span>{event}</span>
            </button>
          ))}
        </div>
      </footer>
    </main>
  );
}
