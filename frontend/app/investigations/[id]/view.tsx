"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getJson, postJson } from "../../../lib/api";

type Reconstruction = any;
type Comparison = any;
type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string; public_key?: string; package: any };
type Verification = { valid: boolean; digest_matches: boolean; signature_valid: boolean; trusted_timestamp: string; timestamp_authority: string; verification_mode: string };

const DEMO_ID = "inv-120-robots-bad-rollout";

export default function InvestigationClient({ id }: { id: string }) {
  return (
    <Suspense fallback={<div className="shell"><main className="main"><section className="hero"><h1>Loading operational case...</h1></section></main></div>}>
      <InvestigationInner id={id} />
    </Suspense>
  );
}

function InvestigationInner({ id }: { id: string }) {
  const investigationId = id || DEMO_ID;
  const presenter = useSearchParams().get("presenter") === "1";
  const [rec, setRec] = useState<Reconstruction | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [pkg, setPkg] = useState<DecisionPackage | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [memory, setMemory] = useState<any>(null);
  const [decisionContext, setDecisionContext] = useState<any>(null);
  const [precedent, setPrecedent] = useState<any>(null);
  const [stage, setStage] = useState("overview");
  const [notice, setNotice] = useState("Ready.");

  function reportError(action: string, error: unknown) {
    const message = error instanceof Error ? error.message : "unknown error";
    setNotice(`${action} failed: ${message}. Check that the backend is running on 127.0.0.1:8050.`);
  }

  async function refresh() {
    try {
      setRec(await postJson(`/investigations/${investigationId}/reconstruct`));
      setComparison(await getJson(`/investigations/${investigationId}/comparison`));
      setMemory(await getJson(`/memory/similar?investigation_id=${investigationId}`));
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
    } catch (error) {
      reportError("Refresh", error);
    }
  }

  useEffect(() => { refresh().catch(() => undefined); }, [investigationId]);

  async function resetScenario() {
    try {
      await postJson("/demo/reset");
      setPkg(null);
      setVerification(null);
      setMemory(null);
      setDecisionContext(null);
      setPrecedent(null);
      setStage("overview");
      setNotice("Scenario reset. 2 machines are known affected at decision time.");
      await refresh();
    } catch (error) {
      reportError("Scenario reset", error);
    }
  }

  async function generatePackage() {
    try {
      const next = await postJson<DecisionPackage>(`/investigations/${investigationId}/decision-package`);
      setPkg(next);
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
      setVerification(null);
      setStage("decision");
      setNotice("Decision Package generated from backend evidence.");
    } catch (error) {
      reportError("Decision Package generation", error);
    }
  }

  async function seal() {
    if (!pkg) return;
    try {
      await postJson(`/investigations/${investigationId}/decision`, {
        decision: "Remote restart affected machines and hold field dispatch",
        owner: "Robotics Engineering",
        rationale: "Application 0.36 ran everywhere, while affected machines share module firmware 4.9, device profile C17 and site network profile N7 with one exposed machine to watch.",
        package_id: pkg.id,
      });
      const sealed = await postJson<DecisionPackage>(`/decision-packages/${pkg.id}/seal`);
      setPkg(sealed);
      setVerification(await getJson<Verification>(`/decision-packages/${sealed.id}/verify`));
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
      setNotice("Operational Case updated with team action and decision state.");
    } catch (error) {
      reportError("Decision Package sealing", error);
    }
  }

  async function lateEvidence() {
    try {
      await postJson("/demo/late-evidence");
      await refresh();
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setStage("outcome");
      setNotice("Delayed module-health evidence arrived with event_time before the engineer note.");
    } catch (error) {
      reportError("Delayed evidence injection", error);
    }
  }

  async function outcome() {
    try {
      await postJson(`/investigations/${investigationId}/outcome`, {
        outcome: "Remote restart restored connectivity; field visit avoided; rollout held pending review",
        payload: { previous_action: "Remote restart R03 and R05; hold field dispatch; monitor R06", recovery_minutes: 18, days_later: 12, field_visit: false, engineering_hours_saved: 3, attribution_level: "observed", attribution_rationale: "Connectivity recovery was observed after remote restart. Remote restart is not treated as proven causal." },
      });
      setMemory(await getJson(`/memory/similar?investigation_id=${investigationId}`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
      setStage("history");
      setNotice("Outcome linked. This case can now be reused by the next similar review.");
    } catch (error) {
      reportError("Outcome recording", error);
    }
  }

  const affected = comparison?.same_signal ?? 2;
  const healthy = comparison?.no_signal ?? 5;
  const lateEvidenceVisible = affected > 2;
  const detectionLead = useMemo(() => {
    if (!rec?.human_discovery?.event_time || !rec?.first_abnormal_evidence?.event_time) return "before engineer note";
    const first = new Date(rec.first_abnormal_evidence.event_time).getTime();
    const ticket = new Date(rec.human_discovery.event_time).getTime();
    const mins = Math.max(0, Math.round((ticket - first) / 60000));
    return `${mins} minutes before the engineer note`;
  }, [rec]);
  const stageLabel: Record<string, string> = {
    overview: "Overview",
    changes: "Changes",
    scope: "Scope",
    decision: "Decision",
    action: "Actions",
    outcome: "Outcome",
    history: "History",
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <nav className="nav">
          <Link href="/">Home</Link>
          <span className="nav-section-label">Case</span>
          <button className={stage === "overview" ? "active" : ""} onClick={() => setStage("overview")}>Overview</button>
          <button className={stage === "changes" ? "active" : ""} onClick={() => setStage("changes")}>Changes</button>
          <button className={stage === "scope" ? "active" : ""} onClick={() => setStage("scope")}>Scope</button>
          <button className={stage === "decision" ? "active" : ""} onClick={() => setStage("decision")}>Decision</button>
          <button className={stage === "action" ? "active" : ""} onClick={() => setStage("action")}>Actions</button>
          <button className={stage === "outcome" ? "active" : ""} onClick={() => setStage("outcome")}>Outcome</button>
          <button className={stage === "history" ? "active" : ""} onClick={() => setStage("history")}>History</button>
          {presenter && <Link className="nav-link-strong" href="/demo-control">Demo Control</Link>}
        </nav>
        <div className="boundary">Machine evidence becomes review context.</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <span className="eyebrow">Operational case · 7 machines · application release 0.36 · 2 known affected</span>
          {presenter && <div className="demo-controls">
            <Link className="button" href="/cinematic">Cinematic story</Link>
            <button className="button" onClick={refresh}>Refresh</button>
            <button className="button" onClick={resetScenario}>Reset scenario</button>
            <button className="button" onClick={lateEvidence}>Inject delayed evidence</button>
          </div>}
        </div>

        {stage === "overview" ? (
          <section className="case-header">
            <div className="case-header-copy">
              <span className="eyebrow">Operational case</span>
              <h1>Two machines changed after the same update.</h1>
              <p>Veyra reconstructs what changed, where else it appears, what the team did and whether it worked.</p>
            </div>
            <div className="case-header-rail">
              <div className="case-pills">
                <b>{lateEvidenceVisible ? `${affected} current` : `${affected} affected`}</b>
                {lateEvidenceVisible && <b>2 decision-time</b>}
                <b>{healthy} healthy</b>
                <b>7 updated</b>
              </div>
              <div className="hero-actions">
                <button className="button primary" onClick={() => setStage("overview")}>Open case</button>
                <button className="button lime" onClick={generatePackage}>Review case</button>
              </div>
            </div>
          </section>
        ) : (
          <section className="case-context-bar">
            <div>
              <span className="eyebrow">Operational case</span>
              <strong>{stageLabel[stage]}</strong>
            </div>
            <p>R03 / R05 connectivity degradation after app 0.36</p>
            <div className="case-pills">
              <b>{lateEvidenceVisible ? `${affected} current` : `${affected} affected`}</b>
              {lateEvidenceVisible && <b>2 decision-time</b>}
              <b>{healthy} healthy</b>
              <b>7 updated</b>
            </div>
          </section>
        )}

        <div className="status-strip">
          <span>{notice}</span>
        </div>

        {presenter && <section className="backend-strip">
          <span>Backend v0 live</span>
          <b>Source evidence ingestion</b>
          <b>What changed</b>
          <b>Where else</b>
          <b>Peer comparison</b>
          <b>Decision state</b>
          <b>Action record</b>
          <b>Outcome memory</b>
        </section>}

        {stage === "overview" && <LiveFailure rec={rec} comparison={comparison} detectionLead={detectionLead} onChanges={() => setStage("changes")} onPackage={generatePackage} />}
        {stage === "changes" && <ChangesStage onScope={() => setStage("scope")} />}
        {stage === "scope" && <CompareStage comparison={comparison} precedent={precedent} onPackage={generatePackage} />}
        {stage === "decision" && <PackageStage pkg={pkg} verification={verification} rec={rec} comparison={comparison} decisionContext={decisionContext} onGenerate={generatePackage} onSeal={seal} />}
        {stage === "action" && <ActionStage pkg={pkg} onGenerate={generatePackage} onSeal={seal} onOutcome={() => setStage("outcome")} />}
        {stage === "outcome" && <LateEvidenceStage pkg={pkg} verification={verification} comparison={comparison} onSeal={seal} onOutcome={outcome} />}
        {stage === "history" && <MemoryStage memory={memory} precedent={precedent} onOutcome={outcome} />}
      </main>
    </div>
  );
}

function LiveFailure({ rec, comparison, detectionLead, onChanges, onPackage }: any) {
  return (
    <>
      <div className="fleet-map" aria-label="fleet status">
        {Array.from({ length: 7 }).map((_, i) => {
          const affected = i < (comparison?.same_signal ?? 2);
          return <span key={i} className={affected ? "dot bad" : "dot good"} title={`R${String(i + 1).padStart(2, "0")}`} />;
        })}
      </div>
      <div className="grid four">
        <Metric label="14:02:11" value="App 0.36" note="deployment started" />
        <Metric label="14:04:37" value="7/7" note="machines updated" />
        <Metric label="14:11:08" value="First known signal" note={rec?.first_abnormal_evidence?.payload?.signal || "module unhealthy"} />
        <Metric label="14:18:42" value="2 affected" note="pattern detected before review" />
        <Metric label="14:26:03" value="Engineer note" note="human discovery recorded" />
      </div>
      <article className="panel dramatic">
        <span className="eyebrow">Post-deployment operations</span>
        <h2>Same deployment. Different connectivity behavior.</h2>
        <p>Every machine received app 0.36. Only two began showing module unhealthy and repeated reconnects. The team needs to decide whether this is application, firmware, site network or device-specific before sending someone onsite.</p>
        <div className="hero-actions"><button className="button primary" onClick={onChanges}>What changed?</button><button className="button" onClick={onPackage}>Review case</button></div>
      </article>
    </>
  );
}

function ChangesStage({ onScope }: { onScope: () => void }) {
  return (
    <article className="panel">
      <span className="eyebrow">Changes</span>
      <h2>What changed around the failure?</h2>
      <div className="package-grid">
        <PackageItem title="Application" value="app 0.35 to 0.36" />
        <PackageItem title="Device profile" value="profile C16 to C17" />
        <PackageItem title="Module firmware" value="module 4.8 to 4.9" />
        <PackageItem title="Machine state" value="module unhealthy, repeated reconnects" />
        <PackageItem title="Human context" value="engineer note at 14:26" />
        <PackageItem title="Site context" value="firewall and network state unconfirmed" />
      </div>
      <div className="callout">
        <b>Three relevant changes occurred before the first known failure.</b>
        <p>Cause is not yet established. The case now needs scope and comparison before the team acts.</p>
      </div>
      <button className="button primary" onClick={onScope}>Scope the issue</button>
    </article>
  );
}

function CompareStage({ comparison, precedent, onPackage }: any) {
  const outcomeRows = precedent?.outcome_comparison || [];
  return (
    <article className="panel">
      <span className="eyebrow">Scope</span>
      <h2>Where else does this pattern appear?</h2>
      <div className="grid three">
        <Metric label="Same app" value={`${comparison?.same_change ?? 7}`} note="machines on app 0.36" />
        <Metric label="Same signal" value={`${comparison?.same_signal ?? 2}`} note="module unhealthy detected" />
        <Metric label="No signal" value={`${comparison?.no_signal ?? 5}`} note="updated but stable" />
      </div>
      <table className="table focus-table">
        <thead><tr><th>Context</th><th>Signal present</th><th>No signal</th></tr></thead>
        <tbody>{comparison?.table?.map((r: any) => <tr key={r.context}><td>{r.context}</td><td>{r.affected}</td><td>{r.unaffected}</td></tr>)}</tbody>
      </table>
      <div className="callout">
        <b>What the evidence narrows</b>
        <p>Application 0.36 is shared across both groups. Device profile C17, module firmware 4.9 and site network profile N7 are shared by the affected machines, while R06 has the same exposure without a known issue at decision time. The exposure is relevant. It is not sufficient to explain the failure.</p>
      </div>
      <div className="callout">
        <b>Decision comparison</b>
        <p>Similar conditions are evaluated by response history, outcome and attribution strength.</p>
      </div>
      <table className="table focus-table">
        <thead><tr><th>Option</th><th>Prior outcome</th><th>Attribution</th></tr></thead>
        <tbody>
          {(outcomeRows.length ? outcomeRows : [
            { action: "remote_fix", outcome: "no outcome recorded", attribution: "not observed" },
            { action: "monitor", outcome: "not yet observed", attribution: "not observed" },
            { action: "dispatch", outcome: "not supported by current evidence", attribution: "not supported" },
          ]).map((row: any) => <tr key={row.action}><td>{row.action}</td><td>{row.outcome || row.status}</td><td>{row.attribution}</td></tr>)}
        </tbody>
      </table>
      <button className="button primary" onClick={onPackage}>Review case</button>
    </article>
  );
}

function PackageStage({ pkg, verification, rec, comparison, decisionContext, onGenerate, onSeal }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">Operational case</span><h2>No case package generated yet.</h2><p>Open the case package to assemble trigger, last healthy state, changes, peer comparison, missing context, decision state and outcome follow-up.</p><button className="button primary" onClick={onGenerate}>Open case package</button></article>;
  }
  const record = decisionContext?.decision_records?.[0];
  const options = decisionContext?.options_considered || pkg.package.options_considered || [];
  const observability = decisionContext?.observability_state || pkg.package.observability_state;
  return (
    <article className="panel">
      <div className="package-head">
        <div><span className="eyebrow">Decision</span><h2>What did the team know at 14:27?</h2><p>This case preserves the evidence available at the decision point and the uncertainty that remained.</p></div>
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Sealed" : "Record decision"}</button>
      </div>
      <div className="package-grid">
        <PackageItem title="Trigger" value="module unhealthy after deployment" />
        <PackageItem title="Last known healthy" value={short(rec?.last_known_healthy?.event_time)} />
        <PackageItem title="Recent changes" value="app 0.35 to 0.36 · profile C16 to C17 · module firmware 4.8 to 4.9" />
        <PackageItem title="Machine state" value={rec?.current_state?.health || "degraded"} />
        <PackageItem title="Affected vs healthy" value={`${comparison?.same_signal ?? 2} / ${comparison?.same_change ?? 7}`} />
        <PackageItem title="Observability status" value={`${observability?.status || "partial"} at decision time`} />
        <PackageItem title="Observed" value="engineer note recorded at 14:26" />
        <PackageItem title="Inferred" value="profile C17, module firmware 4.9 and network N7 co-occur across affected machines" />
        <PackageItem title="Human asserted" value="engineer suspects site network or module firmware setting" />
        <PackageItem title="Missing evidence" value={(pkg.package.missing_evidence || []).join(" · ")} />
        <PackageItem title="Approval policy" value={pkg.package.approval_policy?.name || "Physical system rollout review"} />
        <PackageItem title="Substantiation" value={pkg.package.decision_substantiation?.status || "incomplete"} />
        <PackageItem title="Human identity" value={pkg.package.human_decision?.identity || "pending named owner"} />
        <PackageItem title="Human action" value={pkg.package.human_decision?.decision || "pending"} />
        <PackageItem title="Outcome" value="pending" />
      </div>
      <div className="callout">
        <b>Decision record</b>
        <p>{record ? "Evidence snapshot, options, chosen option, owner and observability state are locked into an immutable decision-time record." : "Record the decision to lock evidence snapshot, options, chosen option, owner and observability state."}</p>
      </div>
      <div className="package-grid">
        <PackageItem title="Decision time" value={decisionContext?.decision_time?.slice(11, 16) || "14:27"} />
        <PackageItem title="Owner" value={record?.owner || pkg.package.human_decision?.owner || "pending"} />
        <PackageItem title="Chosen option" value={record?.chosen_option?.label || "pending"} />
        <PackageItem title="Record digest" value={record?.digest?.slice(0, 16) || "pending"} />
      </div>
      <table className="table focus-table">
        <thead><tr><th>Option</th><th>Expected cost</th><th>Expected risk</th><th>Status</th></tr></thead>
        <tbody>{options.map((option: any) => <tr key={option.id || option.option_type}><td>{option.label}</td><td>{summarizeObject(option.expected_cost)}</td><td>{option.expected_risk?.risk || option.expected_risk?.reason || "unknown"}</td><td>{option.selected ? "chosen" : "available"}</td></tr>)}</tbody>
      </table>
      <div className="split-count">
        <div><strong>What the team knew at 14:27</strong><span>R03 affected · R05 affected · R06 no known issue</span></div>
        <div><strong>What Veyra knows now</strong><span>R06 had an earlier signal that became available later</span></div>
      </div>
      <div className="evidence-columns">
        <div className="evidence-card">
          <span className="eyebrow">Evidence available at decision time</span>
          <ul>
            <li>R03 affected</li>
            <li>R05 affected</li>
            <li>R06 no known issue</li>
            <li>Telemetry completeness: partial</li>
          </ul>
        </div>
        <div className="evidence-card">
          <span className="eyebrow">Later evidence</span>
          <ul>
            <li>R06 event_time: 14:09</li>
            <li>Ingested: 14:31</li>
            <li>Arrived after decision</li>
            <li>Review required</li>
          </ul>
        </div>
      </div>
      <div className="callout warning">
        <b>{pkg.package.decision_substantiation?.question || "Is the team ready to act on this case?"}</b>
        <p>{pkg.package.decision_substantiation?.summary || "Decision package incomplete."}</p>
        <ul className="check-list">
          {(pkg.package.decision_substantiation?.checks || []).map((check: any) => <li key={check.name}><span>{check.status}</span><b>{check.name}</b><small>{check.evidence}</small></li>)}
        </ul>
      </div>
      <div className="callout">
        <b>What this package rules in / rules out</b>
        <p>Application-wide issue: weak support in current peer comparison. Firmware/profile issue: plausible. Site network issue: still unresolved. Field dispatch: current evidence is insufficient to support it.</p>
      </div>
      {pkg.sealed && <Signature pkg={pkg} verification={verification} />}
    </article>
  );
}

function ActionStage({ pkg, onGenerate, onSeal, onOutcome }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">Actions</span><h2>No decision state generated yet.</h2><p>Open the case first so the action is recorded with the evidence available at the time.</p><button className="button primary" onClick={onGenerate}>Open case package</button></article>;
  }
  return (
    <article className="panel">
      <span className="eyebrow">Actions</span>
      <h2>The team chooses a response.</h2>
      <p>Veyra keeps the decision, the action that was actually executed, and the evidence available at the time together.</p>
      <div className="package-grid">
        <PackageItem title="Decision" value="remote fix before dispatch" />
        <PackageItem title="Executed" value="remote restart R03 and R05" />
        <PackageItem title="Watch" value="monitor R06" />
        <PackageItem title="Customer" value="notify support" />
        <PackageItem title="Field" value="hold dispatch" />
        <PackageItem title="Owner" value="Operations Lead · 14:31" />
      </div>
      <div className="hero-actions">
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Decision sealed" : "Seal decision state"}</button>
        <button className="button primary" onClick={onOutcome}>Track outcome</button>
      </div>
    </article>
  );
}

function LateEvidenceStage({ pkg, verification, comparison, onSeal, onOutcome }: any) {
  return (
    <article className="panel dramatic">
      <span className="eyebrow">Outcome</span>
      <h2>Did the action work?</h2>
      <p>R03 and R05 recover after remote restart. R06 later shows the same pattern.</p>
      <div className="callout warning">
        <b>New decision-relevant evidence arrived</b>
        <p>The original decision record remains unchanged. The current case view is updated and flagged for review.</p>
      </div>
      <p className="evidence-detail">Late evidence: R06 module-health buffer was uploaded at 14:31; reconnect failures were present at 14:09:11.</p>
      <div className="time-rail">
        <div><b>14:09</b><span>event_time</span><small>module-health buffer shows reconnect failures</small></div>
        <i />
        <div><b>14:27</b><span>decision sealed</span><small>known evidence only</small></div>
        <i />
        <div><b>14:31</b><span>known_at</span><small>became knowable</small></div>
        <i />
        <div><b>14:31:04</b><span>ingested_at</span><small>received by Veyra</small></div>
      </div>
      <p>Veyra updates what we know now, not what the team knew then.</p>
      <div className="grid three">
        <Metric label="Decision-time view" value="2" note="affected in sealed package" />
        <Metric label="Current view" value={`${Math.max(comparison?.same_signal ?? 3, 3)}`} note="affected after delayed evidence" />
        <Metric label="Decision state" value="Intact" note="new evidence leaves old context intact" />
      </div>
      <div className="grid four">
        <Metric label="Field dispatch" value="Avoided" note="remote action was enough for R03/R05" />
        <Metric label="Rollout" value="Paused" note="then resumed after review" />
        <Metric label="Engineering time" value="Reduced" note="no added investigation for R03/R05" />
        <Metric label="Customer support" value="Informed" note="before escalation" />
      </div>
      {pkg?.sealed ? <Signature pkg={pkg} verification={verification} /> : <button className="button lime" onClick={onSeal}>Seal package first</button>}
      <button className="button primary" onClick={onOutcome}>Link outcome</button>
    </article>
  );
}

function MemoryStage({ memory, precedent, onOutcome }: any) {
  const hasMemory = memory?.similar_cases?.length > 0;
  const rows = precedent?.outcome_comparison || memory?.precedent_comparison?.outcome_comparison || [];
  return (
    <article className="panel final-stage">
      <span className="eyebrow">12 days later</span>
      <h2>A similar pattern appears again.</h2>
      <p>R12 begins showing reconnect failures after a new deployment.</p>
      <div className="callout">
        <b>{hasMemory ? "Similar previous case" : "Reusable case preview"}</b>
        <p>Use the previous case as operational precedent. It records what conditions were present, what the team did and what outcome followed.</p>
      </div>
      <div className="precedent-stack">
        <div>
          <span className="eyebrow">Previous conditions</span>
          <b>App 0.36 · Profile C17 · Module firmware 4.9</b>
        </div>
        <div>
          <span className="eyebrow">Previous action</span>
          <b>Remote restart · rollout held · field dispatch avoided</b>
        </div>
        <div>
          <span className="eyebrow">Observed outcome</span>
          <b>Connectivity restored · no field visit · R06 later showed same pattern</b>
        </div>
      </div>
      <div className="package-grid memory-grid">
        <PackageItem title="Evidence strength" value="precedent, not causal proof" />
        <PackageItem title="Previously successful" value="observed after action" />
        <PackageItem title="What to reuse" value="check prior conditions before field dispatch" />
        <PackageItem title="What to verify" value="whether the same evidence pattern holds now" />
      </div>
      <table className="table focus-table">
        <thead><tr><th>Action</th><th>Observed outcome</th><th>Attribution</th></tr></thead>
        <tbody>
          {(rows.length ? rows : [
            { action: "remote_fix", outcome: "record outcome first", attribution: "not observed" },
            { action: "dispatch", outcome: "not supported by current evidence", attribution: "not supported" },
          ]).map((row: any) => <tr key={row.action}><td>{row.action}</td><td>{row.outcome || row.status}</td><td>{row.attribution}</td></tr>)}
        </tbody>
      </table>
      <div className="callout">
        <b>Before dispatching a technician</b>
        <p>Compare the current machine against the previous exposed group and check whether the same application, firmware, profile and site network pattern is present.</p>
      </div>
      {!hasMemory && <button className="button primary" onClick={onOutcome}>Link outcome first</button>}
      <div className="ending">
        <b>The company is no longer solving the same problem from zero.</b>
        <span>A company should not solve the same machine problem twice.</span>
      </div>
    </article>
  );
}

function Signature({ pkg, verification }: { pkg: DecisionPackage; verification?: Verification | null }) {
  return <div className="panel good signature"><span className="eyebrow">Sealed Decision Package</span><p>Later evidence updates the current investigation. The decision-time package remains sealed.</p><p className="hash hash-large">SHA-256 {pkg.digest}</p><p className="signature-note">Hash recorded at 14:27:00 · tamper-evident · {pkg.package?._seal?.timestamp_authority || "timestamp authority"}</p><p className="hash">Ed25519 {pkg.signature?.slice(0, 48)}...</p><div className="verify-grid"><PackageItem title="Standalone verification" value={verification?.valid ? "valid digest + valid signature" : "ready with public key"} /><PackageItem title="Human owner" value={pkg.package?.human_decision?.owner || "Robotics Engineering"} /><PackageItem title="Decision scope" value={(pkg.package?.action_scope?.affected || []).join(" · ") || "affected machines"} /></div></div>;
}

function PackageItem({ title, value }: { title: string; value: string }) {
  return <div className="package-item"><span>{title}</span><b>{value || "—"}</b></div>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <article className="panel metric"><span>{label}</span><strong>{value || "—"}</strong><p>{note}</p></article>;
}

function summarizeObject(value: any) {
  if (!value || typeof value !== "object") return value || "—";
  return Object.entries(value).map(([key, item]) => `${key}: ${item}`).join(" · ");
}

function short(value?: string) {
  if (!value) return "—";
  return value.slice(11, 16);
}
