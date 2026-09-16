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
      setStage("overview");
      setNotice("Scenario reset. 2 robots are known affected at decision time.");
      await refresh();
    } catch (error) {
      reportError("Scenario reset", error);
    }
  }

  async function generatePackage() {
    try {
      const next = await postJson<DecisionPackage>(`/investigations/${investigationId}/decision-package`);
      setPkg(next);
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
        decision: "Pause v0.9 on robots with calibration C and gripper firmware 7.3",
        owner: "Robotics Engineering",
        rationale: "Policy v0.9 ran everywhere, while the affected runs share calibration C and gripper firmware 7.3 with one exposed robot to watch.",
        package_id: pkg.id,
      });
      const sealed = await postJson<DecisionPackage>(`/decision-packages/${pkg.id}/seal`);
      setPkg(sealed);
      setVerification(await getJson<Verification>(`/decision-packages/${sealed.id}/verify`));
      setNotice("Operational Case updated with team action and decision state.");
    } catch (error) {
      reportError("Decision Package sealing", error);
    }
  }

  async function lateEvidence() {
    try {
      await postJson("/demo/late-evidence");
      await refresh();
      setStage("outcome");
      setNotice("Delayed run evidence arrived with event_time before the engineer note.");
    } catch (error) {
      reportError("Delayed evidence injection", error);
    }
  }

  async function outcome() {
    try {
      await postJson(`/investigations/${investigationId}/outcome`, {
        outcome: "Calibration C and gripper firmware 7.3 held; affected robots recovered after targeted rollback",
        payload: { previous_action: "Pause v0.9 on robots with calibration C and firmware 7.3", recovery_minutes: 18, days_later: 12 },
      });
      setMemory(await getJson(`/memory/similar?investigation_id=${investigationId}`));
      setStage("history");
      setNotice("Outcome linked. This case can now be reused by the next similar review.");
    } catch (error) {
      reportError("Outcome recording", error);
    }
  }

  const affected = comparison?.same_signal ?? 2;
  const healthy = comparison?.no_signal ?? 4;
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
          <span className="eyebrow">Operational case · 6 machines · software release v0.9 · 2 affected</span>
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
                <b>6 updated</b>
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
            <p>R03 / R05 anomaly after release v0.9</p>
            <div className="case-pills">
              <b>{lateEvidenceVisible ? `${affected} current` : `${affected} affected`}</b>
              {lateEvidenceVisible && <b>2 decision-time</b>}
              <b>{healthy} healthy</b>
              <b>6 updated</b>
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
        {stage === "scope" && <CompareStage comparison={comparison} onPackage={generatePackage} />}
        {stage === "decision" && <PackageStage pkg={pkg} verification={verification} rec={rec} comparison={comparison} onGenerate={generatePackage} onSeal={seal} />}
        {stage === "action" && <ActionStage pkg={pkg} onGenerate={generatePackage} onSeal={seal} onOutcome={() => setStage("outcome")} />}
        {stage === "outcome" && <LateEvidenceStage pkg={pkg} verification={verification} comparison={comparison} onSeal={seal} onOutcome={outcome} />}
        {stage === "history" && <MemoryStage memory={memory} onOutcome={outcome} />}
      </main>
    </div>
  );
}

function LiveFailure({ rec, comparison, detectionLead, onChanges, onPackage }: any) {
  return (
    <>
      <div className="fleet-map" aria-label="fleet status">
        {Array.from({ length: 6 }).map((_, i) => {
          const affected = i < (comparison?.same_signal ?? 2);
          return <span key={i} className={affected ? "dot bad" : "dot good"} title={`R${String(i + 1).padStart(2, "0")}`} />;
        })}
      </div>
      <div className="grid four">
        <Metric label="14:02:11" value="Policy v0.9" note="test rollout started" />
        <Metric label="14:04:37" value="6/6" note="robots updated" />
        <Metric label="14:11:08" value="First known signal" note={rec?.first_abnormal_evidence?.payload?.signal || "grip pose drift"} />
        <Metric label="14:18:42" value="2 affected" note="pattern detected before review" />
        <Metric label="14:26:03" value="Engineer note" note="human discovery recorded" />
      </div>
      <article className="panel dramatic">
        <span className="eyebrow">Repeated real-world testing</span>
        <h2>Same policy. Same task. Different behavior.</h2>
        <p>Every robot ran policy v0.9. Only two began showing grip pose drift. The question is no longer whether something failed; it is what changed around the robots that diverged.</p>
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
        <PackageItem title="Software" value="policy v0.8 to v0.9" />
        <PackageItem title="Calibration" value="camera calibration B to C" />
        <PackageItem title="Firmware" value="gripper 7.2 to 7.3" />
        <PackageItem title="Machine state" value="grip pose drift" />
        <PackageItem title="Human context" value="engineer note at 14:26" />
        <PackageItem title="Recent service" value="hardware fault unconfirmed" />
      </div>
      <div className="callout">
        <b>Three relevant changes occurred before the first known failure.</b>
        <p>Cause is not yet established. The case now needs scope and comparison before the team acts.</p>
      </div>
      <button className="button primary" onClick={onScope}>Scope the issue</button>
    </article>
  );
}

function CompareStage({ comparison, onPackage }: any) {
  return (
    <article className="panel">
      <span className="eyebrow">Scope</span>
      <h2>Where else does this pattern appear?</h2>
      <div className="grid three">
        <Metric label="Same policy" value={`${comparison?.same_change ?? 6}`} note="robots on policy v0.9" />
        <Metric label="Same signal" value={`${comparison?.same_signal ?? 2}`} note="grip pose drift detected" />
        <Metric label="No signal" value={`${comparison?.no_signal ?? 4}`} note="updated but stable" />
      </div>
      <table className="table focus-table">
        <thead><tr><th>Context</th><th>Signal present</th><th>No signal</th></tr></thead>
        <tbody>{comparison?.table?.map((r: any) => <tr key={r.context}><td>{r.context}</td><td>{r.affected}</td><td>{r.unaffected}</td></tr>)}</tbody>
      </table>
      <div className="callout">
        <b>What the evidence narrows</b>
        <p>Policy v0.9 is shared across both groups. Calibration C and gripper firmware 7.3 are shared by both affected machines, while R06 has the same exposure without a known failure at decision time. The exposure is relevant. It is not sufficient to explain the failure.</p>
      </div>
      <button className="button primary" onClick={onPackage}>Review case</button>
    </article>
  );
}

function PackageStage({ pkg, verification, rec, comparison, onGenerate, onSeal }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">Operational case</span><h2>No case package generated yet.</h2><p>Open the case package to assemble trigger, last healthy state, changes, peer comparison, missing context, decision state and outcome follow-up.</p><button className="button primary" onClick={onGenerate}>Open case package</button></article>;
  }
  return (
    <article className="panel">
      <div className="package-head">
        <div><span className="eyebrow">Decision</span><h2>What did the team know at 14:27?</h2><p>This case preserves the evidence available at the decision point and the uncertainty that remained.</p></div>
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Sealed" : "Record decision"}</button>
      </div>
      <div className="package-grid">
        <PackageItem title="Trigger" value="grip pose drift after policy update" />
        <PackageItem title="Last known healthy" value={short(rec?.last_known_healthy?.event_time)} />
        <PackageItem title="Recent changes" value="policy v0.8 to v0.9 · calibration B to C · gripper firmware 7.2 to 7.3" />
        <PackageItem title="Machine state" value={rec?.current_state?.health || "degraded"} />
        <PackageItem title="Affected vs healthy" value={`${comparison?.same_signal ?? 2} / ${comparison?.same_change ?? 6}`} />
        <PackageItem title="Observability status" value="partial at decision time" />
        <PackageItem title="Observed" value="engineer note recorded at 14:26" />
        <PackageItem title="Inferred" value="calibration C and gripper firmware 7.3 co-occur across affected runs" />
        <PackageItem title="Human asserted" value="engineer suspects calibration mismatch after policy update" />
        <PackageItem title="Missing evidence" value={(pkg.package.missing_evidence || []).join(" · ")} />
        <PackageItem title="Approval policy" value={pkg.package.approval_policy?.name || "Physical system rollout review"} />
        <PackageItem title="Substantiation" value={pkg.package.decision_substantiation?.status || "incomplete"} />
        <PackageItem title="Human identity" value={pkg.package.human_decision?.identity || "pending named owner"} />
        <PackageItem title="Human action" value={pkg.package.human_decision?.decision || "pending"} />
        <PackageItem title="Outcome" value="pending" />
      </div>
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
        <p>Policy-wide issue: weak support in current peer comparison. Calibration and firmware interaction: plausible. Environment contribution: still unresolved. More low-light validation runs: current evidence supports collecting them.</p>
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
        <PackageItem title="Decision" value="pause rollout" />
        <PackageItem title="Executed" value="rollback R03 and R05" />
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
      <p>R03 and R05 recover after rollback. R06 later shows the same pattern.</p>
      <div className="callout warning">
        <b>New decision-relevant evidence arrived</b>
        <p>The original decision record remains unchanged. The current case view is updated and flagged for review.</p>
      </div>
      <p className="evidence-detail">Late evidence: R06 post-run telemetry was re-analyzed at 14:31; grip pose drift was present at 14:09:11.</p>
      <div className="time-rail">
        <div><b>14:09</b><span>event_time</span><small>post-run telemetry shows drift</small></div>
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

function MemoryStage({ memory, onOutcome }: any) {
  const hasMemory = memory?.similar_cases?.length > 0;
  return (
    <article className="panel final-stage">
      <span className="eyebrow">12 days later</span>
      <h2>A similar pattern appears again.</h2>
      <p>R12 begins showing grip pose drift after a new software test.</p>
      <div className="callout">
        <b>{hasMemory ? "Similar previous case" : "Reusable case preview"}</b>
        <p>Use the previous case as operational precedent. It records what conditions were present, what the team did and what outcome followed.</p>
      </div>
      <div className="precedent-stack">
        <div>
          <span className="eyebrow">Previous conditions</span>
          <b>v0.9 · Calibration C · Firmware 7.3</b>
        </div>
        <div>
          <span className="eyebrow">Previous action</span>
          <b>Rollout paused · R03/R05 rolled back</b>
        </div>
        <div>
          <span className="eyebrow">Observed outcome</span>
          <b>Both recovered · no field visit · R06 later showed same pattern</b>
        </div>
      </div>
      <div className="package-grid memory-grid">
        <PackageItem title="Evidence strength" value="precedent, not causal proof" />
        <PackageItem title="Previously successful" value="not yet validated as causal" />
        <PackageItem title="What to reuse" value="check prior conditions before field dispatch" />
        <PackageItem title="What to verify" value="whether the same evidence pattern holds now" />
      </div>
      <div className="callout">
        <b>Before dispatching a technician</b>
        <p>Compare the current machine against the previous exposed group and check whether the same software and calibration combination is present.</p>
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

function short(value?: string) {
  if (!value) return "—";
  return value.slice(11, 16);
}
