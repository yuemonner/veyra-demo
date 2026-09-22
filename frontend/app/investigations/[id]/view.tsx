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
      setNotice("Scenario reset. 3 machines are known affected at decision time.");
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
      setNotice("Decision options ready.");
    } catch (error) {
      reportError("Decision options", error);
    }
  }

  async function seal() {
    if (!pkg) return;
    try {
      await postJson(`/investigations/${investigationId}/decision`, {
        decision: "Remote recovery on affected machines and hold field dispatch",
        owner: "Robotics Engineering",
        rationale: "Autonomy 2.7 ran everywhere, while affected machines share localization profile L4 and loading zone B exposure with one exposed machine to watch.",
        package_id: pkg.id,
      });
      const sealed = await postJson<DecisionPackage>(`/decision-packages/${pkg.id}/seal`);
      setPkg(sealed);
      setVerification(await getJson<Verification>(`/decision-packages/${sealed.id}/verify`));
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
      setNotice("Operational Case updated with team action and decision state.");
    } catch (error) {
      reportError("Decision record", error);
    }
  }

  async function lateEvidence() {
    try {
      await postJson("/demo/late-evidence");
      await refresh();
      setDecisionContext(await getJson(`/investigations/${investigationId}/decision-context`));
      setStage("outcome");
      setNotice("Delayed runtime evidence arrived with event_time before the operator review.");
    } catch (error) {
      reportError("Delayed evidence injection", error);
    }
  }

  async function outcome() {
    try {
      await postJson(`/investigations/${investigationId}/outcome`, {
        outcome: "Remote recovery returned machines to service; field visit avoided; rollout held pending review",
        payload: { previous_action: "Remote recovery on EX03, EX05 and EX08; hold field dispatch; monitor EX11", recovery_minutes: 18, days_later: 12, field_visit: false, engineering_hours_saved: 2, attribution_level: "observed", attribution_rationale: "Return-to-service was observed after remote recovery. Remote recovery is not treated as proven causal." },
      });
      setMemory(await getJson(`/memory/similar?investigation_id=${investigationId}`));
      setPrecedent(await getJson(`/precedents/compare?investigation_id=${investigationId}`));
      setStage("history");
      setNotice("Outcome linked. This case can now be reused by the next similar review.");
    } catch (error) {
      reportError("Outcome recording", error);
    }
  }

  const affected = comparison?.same_signal ?? 3;
  const stageImpliesLateEvidence = stage === "outcome" || stage === "history";
  const lateEvidenceVisible = affected > 3 || stageImpliesLateEvidence;
  const displayAffected = lateEvidenceVisible ? Math.max(affected, 4) : affected;
  const displayHealthy = lateEvidenceVisible ? 8 : (comparison?.no_signal ?? 9);
  const actionRecorded = Boolean(pkg?.sealed || pkg?.package?.human_decision || stage === "action" || stage === "outcome" || stage === "history");
  const outcomeRecorded = Boolean(stage === "outcome" || stage === "history" || memory?.similar_cases?.length);
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
    action: "Action",
    outcome: "Outcome",
    history: "History",
  };

  return (
    <div className="product-shell">
      <header className="workspace-header">
        <div>
          <Link className="workspace-brand" href="/"><span className="mark">V</span> Veyra</Link>
          <span className="eyebrow">Case workspace</span>
          <h1>EX03 / EX05 / EX08 safe-stop after autonomy 2.7</h1>
        </div>
        <div className="workspace-header-actions">
          <div className="case-pills">
            <b className="pill-alert">{lateEvidenceVisible ? `${displayAffected} current` : `${displayAffected} affected`}</b>
            {lateEvidenceVisible && <b>3 decision-time</b>}
            <b className="pill-blue">{displayHealthy} healthy</b>
            <b>12 updated</b>
            <b>{actionRecorded ? "remote recovery" : "action pending"}</b>
            {outcomeRecorded && <b>outcome observed</b>}
          </div>
          {presenter && <div className="demo-controls compact-controls">
            <Link className="button" href="/cinematic">Case replay</Link>
            <button className="button" onClick={refresh}>Refresh</button>
            <button className="button" onClick={resetScenario}>Reset</button>
            <button className="button" onClick={lateEvidence}>Late evidence</button>
          </div>}
        </div>
      </header>

      <div className="workspace-grid">
        <MachineRail stage={stage} affected={displayAffected} healthy={displayHealthy} lateEvidenceVisible={lateEvidenceVisible} actionRecorded={actionRecorded} outcomeRecorded={outcomeRecorded} />

        <main className="workspace-main">
          <StageTabs stage={stage} setStage={setStage} />
          <div className="status-strip workspace-status"><span>{notice}</span></div>

          {presenter && <section className="backend-strip compact-backend">
            <span>Backend-backed demo</span>
            <b>Evidence</b>
            <b>Changes</b>
            <b>Fleet</b>
            <b>Options</b>
            <b>Action</b>
            <b>Outcome</b>
          </section>}

          <section className="workspace-stage">
            {stage === "overview" && <LiveFailure rec={rec} comparison={comparison} detectionLead={detectionLead} onChanges={() => setStage("changes")} onPackage={generatePackage} />}
            {stage === "changes" && <ChangesStage onScope={() => setStage("scope")} />}
            {stage === "scope" && <CompareStage comparison={comparison} precedent={precedent} onPackage={generatePackage} />}
            {stage === "decision" && <PackageStage pkg={pkg} verification={verification} rec={rec} comparison={comparison} decisionContext={decisionContext} onGenerate={generatePackage} onSeal={seal} onFetchEvidence={lateEvidence} />}
            {stage === "action" && <ActionStage pkg={pkg} onGenerate={generatePackage} onSeal={seal} onOutcome={() => setStage("outcome")} />}
            {stage === "outcome" && <LateEvidenceStage pkg={pkg} verification={verification} comparison={comparison} onSeal={seal} onOutcome={outcome} />}
            {stage === "history" && <MemoryStage memory={memory} precedent={precedent} onOutcome={outcome} />}
          </section>
        </main>

        <WorkspaceRightRail
          stage={stage}
          pkg={pkg}
          verification={verification}
          memory={memory}
          precedent={precedent}
          affected={displayAffected}
          healthy={displayHealthy}
          actionRecorded={actionRecorded}
          outcomeRecorded={outcomeRecorded}
          onPackage={generatePackage}
          onSeal={seal}
          onOutcome={outcome}
        />
      </div>
    </div>
  );
}

function StageTabs({ stage, setStage }: { stage: string; setStage: (stage: string) => void }) {
  const tabs = [
    ["overview", "Overview"],
    ["changes", "Changes"],
    ["scope", "Fleet"],
    ["decision", "Decision"],
    ["action", "Action"],
    ["outcome", "Outcome"],
    ["history", "Precedent"],
  ];
  return (
    <nav className="stage-tabs" aria-label="case sections">
      {tabs.map(([id, label], index) => (
        <button key={id} className={stage === id ? "active" : ""} onClick={() => setStage(id)}>
          <span>{String(index + 1).padStart(2, "0")}</span>{label}
        </button>
      ))}
    </nav>
  );
}

function MachineRail({ stage, affected, healthy, lateEvidenceVisible, actionRecorded, outcomeRecorded }: { stage: string; affected: number; healthy: number; lateEvidenceVisible: boolean; actionRecorded: boolean; outcomeRecorded: boolean }) {
  const affectedSet = new Set(lateEvidenceVisible ? [3, 5, 8, 11] : [3, 5, 8]);
  const watchSet = new Set([11]);
  const railCopy: Record<string, { title: string; question: string; rows: Array<[string, string]> }> = {
    overview: {
      title: "Case scope",
      question: "What changed, and which machines need attention first?",
      rows: [["affected", "EX03, EX05, EX08"], ["healthy", "9 machines"], ["updated", "12 machines"], ["review", "opened"]],
    },
    changes: {
      title: "Changed machines",
      question: "Which machine state changed before the first known safe-stop?",
      rows: [["release", "2.7"], ["localization", "L4"], ["map", "M19"], ["zone", "B"]],
    },
    scope: {
      title: "Fleet split",
      question: "Which machines share exposure, and which stayed healthy?",
      rows: [["affected", "3"], ["healthy", "9"], ["exposed watch", "EX11"], ["shared condition", "L4 + zone B"]],
    },
    decision: {
      title: "Decision inputs",
      question: "Which option has enough evidence to act without sending someone onsite?",
      rows: [["known", "3 affected"], ["unknown", "EX11"], ["observability", "partial"], ["owner", "pending"]],
    },
    action: {
      title: "Chosen response",
      question: "What did the team actually do after comparing the options?",
      rows: [["action", actionRecorded ? "remote recovery" : "pending"], ["rollout", "held"], ["dispatch", "held"], ["watch", "EX11"]],
    },
    outcome: {
      title: "Current state",
      question: "What changed after the action, and what arrived late?",
      rows: [["decision-time", "3 affected"], ["current", `${affected} affected`], ["outcome", outcomeRecorded ? "observed" : "pending"], ["late evidence", "EX11"]],
    },
    history: {
      title: "Reusable precedent",
      question: "What should the next similar case inherit from this one?",
      rows: [["previous action", "remote recovery"], ["result", "returned to service"], ["dispatch", "avoided"], ["boundary", "not causal proof"]],
    },
  };
  const copy = railCopy[stage] || railCopy.overview;
  return (
    <aside className="machine-rail">
      <div className="rail-title"><span className="eyebrow">{copy.title}</span><b>12</b></div>
      <div className="machine-grid" aria-label="machine status">
        {Array.from({ length: 12 }).map((_, i) => {
          const id = i + 1;
          const cls = affectedSet.has(id) ? "affected" : watchSet.has(id) ? "watch" : "healthy";
          return <span key={id} className={cls} title={`EX${String(id).padStart(2, "0")}`} />;
        })}
      </div>
      <div className="machine-legend">
        <span><i className="affected" /> affected</span>
        <span><i className="watch" /> exposed watch</span>
        <span><i /> healthy</span>
      </div>
      <dl className="case-facts">
        {copy.rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <div className="rail-card">
        <span className="eyebrow">Current question</span>
        <p>{copy.question}</p>
      </div>
    </aside>
  );
}

function WorkspaceRightRail({ stage, pkg, verification, memory, precedent, affected, healthy, actionRecorded, outcomeRecorded, onPackage, onSeal, onOutcome }: any) {
  const hasMemory = memory?.similar_cases?.length > 0 || stage === "history";
  const validation = precedent?.outcome_validation || memory?.precedent_comparison?.outcome_validation;
  const stageHelp: Record<string, { label: string; title: string; body: string; cta: string }> = {
    overview: { label: "Purpose", title: "Open the case", body: "Start with the machine group, the affected machines and the operational question.", cta: "What changed?" },
    changes: { label: "Purpose", title: "Reconstruct changes", body: "Pull together release, localization, firmware, map and operator context before the team acts.", cta: "Scope the issue" },
    scope: { label: "Purpose", title: "Find where else", body: "Separate affected machines from healthy machines that share the same deployment or exposure.", cta: "Compare options" },
    decision: { label: "Purpose", title: "Choose next action", body: "Compare monitor, remote recovery, rollback and dispatch using evidence, risk, prior outcome and cost.", cta: "Record decision" },
    action: { label: "Purpose", title: "Record what happened", body: "Keep the chosen action, owner, scope and decision-time evidence together.", cta: "Track outcome" },
    outcome: { label: "Purpose", title: "Measure the result", body: "Show observed recovery, field visit avoided and late evidence that changes the current case.", cta: "Link outcome" },
    history: { label: "Purpose", title: "Reuse the precedent", body: "Bring back prior action, observed outcome and uncertainty boundary for the next similar case.", cta: "Review precedent" },
  };
  const help = stageHelp[stage] || stageHelp.overview;
  const historyText = hasMemory
    ? "1 linked precedent available. Last time, remote recovery returned machines to service, avoided a field visit and EX11 later showed the same pattern."
    : stage === "outcome"
      ? "Remote recovery returned 3 machines to service. Link the outcome to make it reusable."
      : "No prior outcome recorded yet. Record the outcome to create reusable precedent.";
  return (
    <aside className="workspace-rail">
      <div className="rail-card">
        <span className="eyebrow">{help.label}</span>
        <h3>{help.title}</h3>
        <p>{help.body}</p>
      </div>
      <div className="rail-card relevant-history">
        <span className="eyebrow">Relevant history</span>
        <p>{historyText}</p>
        {stage === "history" ? <button className="button">Precedent loaded</button> : <button className="button" onClick={onOutcome}>{hasMemory || stage === "outcome" ? "Link outcome" : "Record outcome"}</button>}
      </div>
      <div className="rail-card">
        <span className="eyebrow">Case state</span>
        <dl className="case-facts compact">
          <div><dt>known then</dt><dd>3 affected</dd></div>
          <div><dt>current</dt><dd>{affected} affected</dd></div>
          <div><dt>watch</dt><dd>EX11 exposed</dd></div>
          <div><dt>action</dt><dd>{actionRecorded ? "remote recovery" : "pending"}</dd></div>
          <div><dt>outcome</dt><dd>{outcomeRecorded || validation?.status ? "observed" : "pending"}</dd></div>
        </dl>
      </div>
      <div className="rail-actions">
        <button className="button primary" onClick={onPackage}>Compare options</button>
        <button className="button lime" onClick={onSeal}>{actionRecorded ? "Decision recorded" : "Record decision"}</button>
      </div>
      {pkg?.sealed && <div className="rail-card">
        <span className="eyebrow">Snapshot</span>
        <p className="hash">SHA-256 {pkg.digest?.slice(0, 18)}...</p>
        <small>{verification?.valid ? "verified" : "sealed"}</small>
      </div>}
    </aside>
  );
}

function LiveFailure({ rec, comparison, detectionLead, onChanges, onPackage }: any) {
  return (
    <>
      <div className="fleet-map" aria-label="fleet status">
        {Array.from({ length: 12 }).map((_, i) => {
          const affected = i < (comparison?.same_signal ?? 3);
          return <span key={i} className={affected ? "dot bad" : "dot good"} title={`EX${String(i + 1).padStart(2, "0")}`} />;
        })}
      </div>
      <div className="grid four">
        <Metric label="14:02:11" value="Autonomy 2.7" note="deployment started" />
        <Metric label="14:04:37" value="12/12" note="machines updated" />
        <Metric label="14:11:08" value="First safe-stop" note={rec?.first_abnormal_evidence?.payload?.signal || "safe-stop near loading zone"} />
        <Metric label="14:26:03" value="3 affected" note="pattern detected before review" />
        <Metric label="14:31:00" value="Operator review" note="human review opened" />
      </div>
      <article className="panel dramatic">
        <span className="eyebrow">The team has four choices</span>
        <h2>Same deployment. Different physical behavior.</h2>
        <p>Every machine received autonomy 2.7. Three entered safe-stop near loading zone B. The team needs to decide whether this is release-wide, localization-specific, map-related, site-specific or machine-specific before sending someone onsite.</p>
        <div className="choice-row">
          <b>Monitor</b>
          <b>Remote recovery</b>
          <b>Rollback release</b>
          <b>Dispatch</b>
        </div>
        <div className="hero-actions"><button className="button primary" onClick={onChanges}>What changed?</button><button className="button" onClick={onPackage}>Compare options</button></div>
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
        <PackageItem title="Autonomy stack" value="2.6 to 2.7" />
        <PackageItem title="Localization config" value="L3 to L4" />
        <PackageItem title="LiDAR firmware" value="5.2 to 5.3" />
        <PackageItem title="Map version" value="M18 to M19" />
        <PackageItem title="Machine state" value="safe-stop near loading zone B" />
        <PackageItem title="Human context" value="operator review at 14:31" />
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
        <Metric label="Same release" value={`${comparison?.same_change ?? 12}`} note="machines on autonomy 2.7" />
        <Metric label="Same behavior" value={`${comparison?.same_signal ?? 3}`} note="safe-stop detected" />
        <Metric label="No signal" value={`${comparison?.no_signal ?? 9}`} note="updated but stable" />
      </div>
      <table className="table focus-table">
        <thead><tr><th>Context</th><th>Signal present</th><th>No signal</th></tr></thead>
        <tbody>{comparison?.table?.map((r: any) => <tr key={r.context}><td>{r.context}</td><td>{r.affected}</td><td>{r.unaffected}</td></tr>)}</tbody>
      </table>
      <div className="callout">
        <b>What the evidence narrows</b>
        <p>Autonomy 2.7 is shared across both groups. Localization profile L4 and loading zone B exposure are shared by the affected machines, while EX11 has the same exposure without a known issue at decision time. The exposure is relevant. It is not sufficient to explain the failure.</p>
      </div>
      <div className="callout">
        <b>What happened when we took these actions before?</b>
        <p>Similar conditions are compared by prior response, observed outcome and attribution boundary.</p>
      </div>
      <table className="table focus-table">
        <thead><tr><th>Option</th><th>Prior outcome</th><th>Attribution</th></tr></thead>
        <tbody>
          {(outcomeRows.length ? outcomeRows : [
            { action: "remote_fix", outcome: "observed recovery after action", attribution: "precedent, not causal proof" },
            { action: "monitor", outcome: "not yet observed", attribution: "not observed" },
            { action: "dispatch", outcome: "not supported by current evidence", attribution: "not supported" },
          ]).map((row: any) => <tr key={row.action}><td>{row.action}</td><td>{row.outcome || row.status}</td><td>{row.attribution}</td></tr>)}
        </tbody>
      </table>
      <button className="button primary" onClick={onPackage}>Compare options</button>
    </article>
  );
}

function DecisionOptions({ options }: { options: any[] }) {
  const fallback = [
    {
      id: "monitor",
      label: "Monitor",
      evidenceFor: "EX11 has no known issue at 14:27",
      evidenceAgainst: "EX03, EX05 and EX08 are already in safe-stop",
      priorOutcome: "not yet observed",
      cost: "low cost, higher operational risk",
    },
    {
      id: "remote_restart",
      label: "Remote recovery",
      evidenceFor: "affected machines entered safe-stop after the release",
      evidenceAgainst: "planner fallback cause remains unresolved",
      priorOutcome: "observed recovery after action",
      cost: "low cost, no field visit",
    },
    {
      id: "rollback",
      label: "Rollback release",
      evidenceFor: "issue follows deployment window",
      evidenceAgainst: "autonomy 2.7 runs on healthy machines too",
      priorOutcome: "not established",
      cost: "rollout delay",
    },
    {
      id: "dispatch",
      label: "Dispatch technician",
      evidenceFor: "local site and sensor state are missing",
      evidenceAgainst: "current evidence does not support immediate visit",
      priorOutcome: "not supported by current evidence",
      cost: "high cost",
    },
  ];
  const normalized = options.length ? options.map((option: any) => ({
    id: option.id || option.option_type,
    label: option.option_type === "remote_fix" ? "Remote recovery" : option.option_type === "pause_rollout" ? "Rollback release" : option.option_type === "dispatch" ? "Dispatch technician" : option.option_type === "monitor" ? "Monitor" : option.label,
    evidenceFor: option.historical_support?.summary || option.label,
    evidenceAgainst: option.expected_risk?.reason || "uncertainty remains",
    priorOutcome: option.historical_support?.status === "none_yet" ? "no prior outcome in this workspace" : option.historical_support?.summary || "pending",
    cost: summarizeObject(option.expected_cost),
    selected: option.selected,
  })) : fallback;
  return (
    <div className="option-grid">
      {normalized.map((option: any) => (
        <article className={option.selected ? "option-card selected" : "option-card"} key={option.id}>
          <header><span>{option.selected ? "chosen" : "option"}</span><b>{option.label}</b></header>
          <dl>
            <dt>Evidence</dt><dd>{option.evidenceFor}</dd>
            <dt>Risk</dt><dd>{option.evidenceAgainst}</dd>
            <dt>Prior outcome</dt><dd>{option.priorOutcome}</dd>
            <dt>Cost</dt><dd>{option.cost}</dd>
          </dl>
        </article>
      ))}
    </div>
  );
}

function PackageStage({ pkg, verification, rec, comparison, decisionContext, onGenerate, onSeal, onFetchEvidence }: any) {
  if (!pkg) {
    return (
      <article className="panel decision-panel">
        <div className="package-head">
          <div>
            <span className="eyebrow">Decision</span>
            <h2>What should the team do now?</h2>
            <p>Compare the next actions using current evidence, known gaps, cost and previous outcomes.</p>
          </div>
          <button className="button primary" onClick={onGenerate}>Compare options</button>
        </div>
        <DecisionAgentCard ready={false} onGenerate={onGenerate} onSeal={onSeal} onFetchEvidence={onFetchEvidence} />
        <DecisionOptions options={[]} />
        <DecisionContextMini />
      </article>
    );
  }
  const record = decisionContext?.decision_records?.[0];
  const options = decisionContext?.options_considered || pkg.package.options_considered || [];
  const observability = decisionContext?.observability_state || pkg.package.observability_state;
  const hypotheses = pkg.package.hypotheses || record?.hypotheses || [];
  const primaryHypothesis = pkg.package.primary_hypothesis || hypotheses[0];
  const unknowns = pkg.package.what_was_unknown || pkg.package.missing_evidence || [];
  return (
    <article className="panel decision-panel">
      <div className="package-head">
        <div><span className="eyebrow">Decision</span><h2>What should the team do now?</h2><p>Compare the next actions using current evidence, known gaps, cost and previous outcomes.</p></div>
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Decision recorded" : "Record decision"}</button>
      </div>
      <DecisionAgentCard ready pkg={pkg} onGenerate={onGenerate} onSeal={onSeal} onFetchEvidence={onFetchEvidence} />
      <DecisionOptions options={options} />
      <DecisionContextMini pkg={pkg} record={record} observability={observability} />
      <details className="snapshot-details">
        <summary>View decision-time snapshot</summary>
        <div className="snapshot-content">
          <div className="split-count">
            <div><strong>What the team knew at 14:27</strong><span>EX03, EX05 and EX08 affected. No known issue on EX11.</span></div>
            <div><strong>What can change later</strong><span>Late evidence can update the current case without rewriting this decision record.</span></div>
          </div>
          <div className="package-grid">
            <PackageItem title="Trigger" value="safe-stop after deployment" />
            <PackageItem title="Last known healthy" value={short(rec?.last_known_healthy?.event_time)} />
            <PackageItem title="Affected vs healthy" value={`${comparison?.same_signal ?? 3} / ${comparison?.same_change ?? 12}`} />
            <PackageItem title="Observability" value={`${observability?.status || "partial"} at decision time`} />
            <PackageItem title="Primary hypothesis" value={primaryHypothesis?.hypothesis || "pending"} />
            <PackageItem title="Confidence" value={primaryHypothesis ? `${Math.round((primaryHypothesis.confidence || 0) * 100)}% · ${primaryHypothesis.status}` : "pending"} />
            <PackageItem title="Owner" value={record?.owner || pkg.package.human_decision?.owner || "pending"} />
            <PackageItem title="Chosen action" value={record?.chosen_option?.label || pkg.package.human_decision?.decision || "pending"} />
          </div>
          <div className="evidence-columns">
            <div className="evidence-card">
              <span className="eyebrow">Evidence available then</span>
              <ul>
                <li>EX03, EX05 and EX08 affected</li>
                <li>EX11 no known issue at 14:27</li>
                <li>Telemetry completeness partial</li>
              </ul>
            </div>
            <div className="evidence-card">
              <span className="eyebrow">Still unknown then</span>
              <ul>
                {unknowns.slice(0, 4).map((item: string) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          </div>
          <div className="callout">
            <b>Clock definitions</b>
            <p>event_time is when the machine event happened. known_at is when the evidence became available to reconstruction. ingested_at is when Veyra received it.</p>
          </div>
          <div className="callout warning">
            <b>{pkg.package.decision_substantiation?.question || "Is the team ready to act on this case?"}</b>
            <p>{pkg.package.decision_substantiation?.summary || "Decision package incomplete."}</p>
          </div>
          {pkg.sealed && <Signature pkg={pkg} verification={verification} />}
        </div>
      </details>
    </article>
  );
}

function DecisionAgentCard({ ready, pkg, onGenerate, onSeal, onFetchEvidence }: { ready: boolean; pkg?: DecisionPackage | null; onGenerate: () => void; onSeal: () => void; onFetchEvidence: () => void }) {
  return (
    <section className="agent-card decision-agent">
      <div>
        <span className="eyebrow">Case agent</span>
        <h3>{ready ? "Recommended action: remote recovery + hold rollout" : "Generate the decision context first"}</h3>
        <p>{ready ? "Confidence: medium. The agent is narrowing an operational action from current evidence, not declaring root cause." : "Veyra will assemble options, evidence boundaries and missing context before the team acts."}</p>
      </div>
      {ready ? (
        <>
          <div className="agent-columns">
            <div>
              <b>Why this action</b>
              <ul>
                <li>3 affected machines share L4 and loading zone B exposure.</li>
                <li>9 machines on autonomy 2.7 remain healthy.</li>
                <li>Current evidence does not justify dispatch.</li>
                <li>Remote recovery has the lowest irreversible cost.</li>
              </ul>
            </div>
            <div>
              <b>What could change it</b>
              <ul>
                <li>EX11 runtime state.</li>
                <li>Planner fallback evidence.</li>
                <li>Site-local sensor health.</li>
              </ul>
            </div>
          </div>
          <div className="agent-actions">
            <button className="button primary" onClick={onFetchEvidence}>Fetch next evidence</button>
            <button className="button lime" onClick={onSeal}>{pkg?.sealed ? "Recommendation approved" : "Approve recommendation"}</button>
            <button className="button">Choose another action</button>
          </div>
        </>
      ) : (
        <div className="agent-actions">
          <button className="button primary" onClick={onGenerate}>Compare options</button>
        </div>
      )}
    </section>
  );
}

function DecisionContextMini({ pkg, record, observability }: { pkg?: DecisionPackage | null; record?: any; observability?: any }) {
  return (
    <section className="decision-context-mini">
      <div><span>Known now</span><b>3 affected machines</b></div>
      <div><span>Still unknown</span><b>EX11 and planner fallback</b></div>
      <div><span>Observability</span><b>{observability?.status || "partial"}</b></div>
      <div><span>Snapshot</span><b>{pkg?.sealed ? "sealed" : record ? "recorded" : "pending"}</b></div>
    </section>
  );
}

function ActionStage({ pkg, onGenerate, onSeal, onOutcome }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">What we chose</span><h2>Choose the next action first.</h2><p>Compare the options so the chosen action is recorded with the evidence available at the time.</p><button className="button primary" onClick={onGenerate}>Compare options</button></article>;
  }
  const planned = pkg.package.planned_action;
  const actual = pkg.package.actual_action;
  return (
    <article className="panel">
      <span className="eyebrow">What we chose</span>
      <h2>Decision: Remote recovery + hold rollout.</h2>
      <p>The decision, the executed action and the evidence available at the time stay together.</p>
      <div className="package-grid">
        <PackageItem title="Why this action" value="remote recovery has lower cost than dispatch while evidence remains incomplete" />
        <PackageItem title="Known at the time" value="EX03, EX05 and EX08 affected, EX11 no known issue" />
        <PackageItem title="Still unknown" value="planner fallback reason and local perception trace" />
        <PackageItem title="Planned action" value={planned?.label || "remote restart affected machines"} />
        <PackageItem title="Actual action" value={actual?.scope?.remote_recovery ? `remote recovery ${actual.scope.remote_recovery.join(" and ")}` : "remote recovery EX03, EX05 and EX08"} />
        <PackageItem title="Watch" value="monitor EX11" />
        <PackageItem title="Customer" value="notify support" />
        <PackageItem title="Field" value="hold dispatch" />
        <PackageItem title="Owner" value="Operations Lead · 14:31" />
      </div>
      <div className="hero-actions">
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Decision recorded" : "Record decision"}</button>
        <button className="button primary" onClick={onOutcome}>Track outcome</button>
      </div>
    </article>
  );
}

function LateEvidenceStage({ pkg, verification, comparison, onSeal, onOutcome }: any) {
  const validation = pkg?.package?.outcome_validation;
  return (
    <article className="panel outcome-page">
      <div className="package-head">
        <div>
          <span className="eyebrow">Outcome</span>
          <h2>What happened after the action?</h2>
          <p>EX03, EX05 and EX08 returned to service after remote recovery. That is observed recovery, not causal proof. EX11 later shows the same pattern through delayed runtime evidence.</p>
        </div>
        <div className={pkg?.sealed ? "sealed-mini good" : "sealed-mini"}>
          <span>{pkg?.sealed ? "Sealed" : "Unsealed"}</span>
          <b>{pkg?.sealed ? "Decision record intact" : "Seal decision first"}</b>
        </div>
      </div>

      <section className="outcome-summary">
        <div className="outcome-status">
          <span className="eyebrow">Outcome validation</span>
          <strong>{validation?.status === "pending" ? "Pending" : "Observed recovery"}</strong>
          <p>{validation?.status === "pending" ? "Record the outcome to validate the action." : "Return-to-service is observed after remote recovery. Causality is not treated as proven."}</p>
        </div>
        <div className="outcome-status">
          <span className="eyebrow">Late evidence</span>
          <strong>New decision-relevant evidence arrived</strong>
          <p>The original decision record remains unchanged. The current fleet view is updated and flagged for review.</p>
        </div>
      </section>

      <section className="mini-timeline">
        <div><b>14:09</b><span>event_time</span><small>EX11 safe-stop behavior existed</small></div>
        <div><b>14:27</b><span>decision snapshot</span><small>3 affected known</small></div>
        <div><b>14:31</b><span>known_at</span><small>late evidence became available</small></div>
        <div><b>14:31:04</b><span>ingested_at</span><small>received by Veyra</small></div>
      </section>

      <section className="outcome-ledger">
        <PackageItem title="Decision-time view" value="3 affected" />
        <PackageItem title="Current view" value={`${Math.max(comparison?.same_signal ?? 3, 4)} affected`} />
        <PackageItem title="Field dispatch" value="avoided by decision path" />
        <PackageItem title="Rollout" value="paused, then resumed" />
        <PackageItem title="Engineering time" value="estimated reduction" />
        <PackageItem title="Customer support" value="informed before escalation" />
      </section>

      <section className="agent-card outcome-agent">
        <span className="eyebrow">Outcome agent</span>
        <h3>Draft observed outcome</h3>
        <p>Remote recovery was followed by return to service on EX03, EX05 and EX08. This is an observed association, not causal proof.</p>
        <div className="agent-columns">
          <div>
            <b>Operational value</b>
            <ul>
              <li>Field dispatch avoided.</li>
              <li>Rollout remained held until EX11 evidence was reviewed.</li>
              <li>Customer support informed before escalation.</li>
            </ul>
          </div>
          <div>
            <b>Record boundary</b>
            <ul>
              <li>Original decision snapshot remains unchanged.</li>
              <li>Current case view updates to 4 affected.</li>
              <li>Outcome can become precedent after confirmation.</li>
            </ul>
          </div>
        </div>
      </section>

      <div className="outcome-actions">
        {!pkg?.sealed && <button className="button lime" onClick={onSeal}>Seal decision first</button>}
        <button className="button primary" onClick={onOutcome}>Confirm outcome</button>
      </div>
    </article>
  );
}

function MemoryStage({ memory, precedent, onOutcome }: any) {
  const validation = precedent?.outcome_validation || memory?.precedent_comparison?.outcome_validation;
  const rows = [
    { action: "Remote recovery", outcome: "returned to service", attribution: "observed after action" },
    { action: "Pause rollout", outcome: "rollout later resumed", attribution: "observed" },
    { action: "Dispatch technician", outcome: "avoided", attribution: "not executed" },
  ];
  return (
    <article className="panel final-stage">
      <span className="eyebrow">12 days later</span>
      <h2>A similar pattern appears again.</h2>
      <p>EX21 enters safe-stop after autonomy release 2.8.</p>
      <div className="callout">
        <b>Last time, remote recovery returned the affected machines to service without a field visit. The issue later appeared on a fourth machine.</b>
        <p>Does the same precedent apply here?</p>
      </div>
      <div className="precedent-stack">
        <div>
          <span className="eyebrow">Previous conditions</span>
          <b>Autonomy 2.7 · Localization L4 · Loading zone B</b>
        </div>
        <div>
          <span className="eyebrow">Previous action</span>
          <b>Remote recovery · rollout held · field dispatch held</b>
        </div>
        <div>
          <span className="eyebrow">Observed outcome</span>
          <b>Returned to service · no field visit · EX11 later showed same pattern</b>
        </div>
      </div>
      <div className="package-grid memory-grid">
        <PackageItem title="Boundary" value="precedent, not causal proof" />
        <PackageItem title="Previous result" value="observed after action" />
        <PackageItem title="Outcome validation" value={validation?.status || "record outcome first"} />
        <PackageItem title="What to reuse" value="check localization, map and zone exposure before field dispatch" />
        <PackageItem title="What to verify" value="whether the same evidence pattern holds now" />
      </div>
      <section className="agent-card precedent-agent">
        <span className="eyebrow">Precedent agent</span>
        <h3>1 relevant precedent found</h3>
        <p>The agent separates reusable operating context from evidence that must be checked again.</p>
        <div className="agent-columns">
          <div>
            <b>Reusable from last time</b>
            <ul>
              <li>Post-release safe-stop near loading zone B.</li>
              <li>Localization L4 was present in the affected group.</li>
              <li>Remote recovery returned machines to service.</li>
            </ul>
          </div>
          <div>
            <b>Different this time</b>
            <ul>
              <li>Autonomy release 2.8 instead of 2.7.</li>
              <li>LiDAR firmware and map state must be rechecked.</li>
              <li>Prior outcome is precedent, not causal proof.</li>
            </ul>
          </div>
        </div>
        <div className="agent-actions">
          <button className="button primary">Use previous outcome</button>
          <button className="button">Review differences</button>
        </div>
      </section>
      <table className="table focus-table">
        <thead><tr><th>Action</th><th>Observed outcome</th><th>Attribution</th></tr></thead>
        <tbody>
          {rows.map((row: any) => <tr key={row.action}><td>{row.action}</td><td>{row.outcome}</td><td>{row.attribution}</td></tr>)}
        </tbody>
      </table>
      <div className="callout">
        <b>Before dispatching a technician</b>
        <p>Compare the current machine against the previous exposed group and check whether the same autonomy release, localization profile, map and loading-zone pattern is present.</p>
      </div>
      <button className="button primary" onClick={onOutcome}>Refresh precedent from outcome</button>
      <div className="ending">
        <b>The next case does not start from zero.</b>
        <span>A company should not start from zero when a similar machine problem appears again.</span>
      </div>
    </article>
  );
}

function Signature({ pkg, verification }: { pkg: DecisionPackage; verification?: Verification | null }) {
  return <div className="panel good signature"><span className="eyebrow">Sealed Decision Package</span><p>Later evidence updates the current investigation. The 14:27 decision-time snapshot remains intact.</p><p className="hash hash-large">SHA-256 {pkg.digest}</p><p className="signature-note">Snapshot covers 14:27 decision context · sealed after owner and action were recorded · tamper-evident · {pkg.package?._seal?.timestamp_authority || "timestamp authority"}</p><p className="hash">Ed25519 {pkg.signature?.slice(0, 48)}...</p><div className="verify-grid"><PackageItem title="Standalone verification" value={verification?.valid ? "valid digest + valid signature" : "ready with public key"} /><PackageItem title="Human owner" value={pkg.package?.human_decision?.owner || "Robotics Engineering"} /><PackageItem title="Decision scope" value={(pkg.package?.action_scope?.affected || []).join(" · ") || "affected machines"} /></div></div>;
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
