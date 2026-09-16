"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "../../../lib/api";

type Reconstruction = any;
type Comparison = any;
type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string; public_key?: string; package: any };
type Verification = { valid: boolean; digest_matches: boolean; signature_valid: boolean; trusted_timestamp: string; timestamp_authority: string; verification_mode: string };

const DEMO_ID = "inv-120-robots-bad-rollout";

export default function InvestigationClient({ id }: { id: string }) {
  const investigationId = id || DEMO_ID;
  const [rec, setRec] = useState<Reconstruction | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [pkg, setPkg] = useState<DecisionPackage | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [memory, setMemory] = useState<any>(null);
  const [stage, setStage] = useState("live");
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
      setStage("live");
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
      setStage("package");
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
      setStage("late");
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
      setStage("memory");
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

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <nav className="nav">
          <Link href="/">Overview</Link>
          <button className={stage === "live" ? "active" : ""} onClick={() => setStage("live")}>Signal</button>
          <button className={stage === "compare" ? "active" : ""} onClick={() => setStage("compare")}>Where else?</button>
          <button className={stage === "package" ? "active" : ""} onClick={() => setStage("package")}>Decision state</button>
          <button className={stage === "late" ? "active" : ""} onClick={() => setStage("late")}>Outcome update</button>
          <button className={stage === "memory" ? "active" : ""} onClick={() => setStage("memory")}>Reuse next time</button>
          <Link className="nav-link-strong" href="/demo-control">Demo Control</Link>
        </nav>
        <div className="boundary">Machine evidence becomes review context.</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <span className="eyebrow">Operational Case · 6 machines · one software update</span>
          <div className="demo-controls">
            <Link className="button" href="/cinematic">Cinematic story</Link>
            <button className="button" onClick={refresh}>Refresh</button>
            <button className="button" onClick={resetScenario}>Reset scenario</button>
            <button className="button" onClick={lateEvidence}>Inject delayed evidence</button>
          </div>
        </div>

        <section className="hero demo-hero">
          <span className="eyebrow">Real-world test run</span>
          <h1>From issue to action to outcome.</h1>
          <p>Six machines run the same software update. Two start behaving differently.</p>
          <div className="hero-actions">
            <button className="button primary" onClick={() => setStage("live")}>Start walkthrough</button>
            <button className="button lime" onClick={generatePackage}>Generate Operational Case</button>
          </div>
        </section>

        <div className="status-strip">
          <span>{notice}</span>
          <b>{lateEvidenceVisible ? `${affected} current` : `${affected} affected`}</b>
          {lateEvidenceVisible && <b>2 decision-time</b>}
          <b>{healthy} healthy</b>
          <b>6 updated</b>
        </div>

        <section className="backend-strip">
          <span>Backend v0 live</span>
          <b>Source evidence ingestion</b>
          <b>What changed</b>
          <b>Where else</b>
          <b>Peer comparison</b>
          <b>Decision state</b>
          <b>Action record</b>
          <b>Outcome memory</b>
        </section>

        {stage === "live" && <LiveFailure rec={rec} comparison={comparison} detectionLead={detectionLead} onCompare={() => setStage("compare")} onPackage={generatePackage} />}
        {stage === "compare" && <CompareStage comparison={comparison} onPackage={generatePackage} />}
        {stage === "package" && <PackageStage pkg={pkg} verification={verification} rec={rec} comparison={comparison} onGenerate={generatePackage} onSeal={seal} />}
        {stage === "late" && <LateEvidenceStage pkg={pkg} verification={verification} comparison={comparison} onSeal={seal} onOutcome={outcome} />}
        {stage === "memory" && <MemoryStage memory={memory} onOutcome={outcome} />}
      </main>
    </div>
  );
}

function LiveFailure({ rec, comparison, detectionLead, onCompare, onPackage }: any) {
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
        <div className="hero-actions"><button className="button primary" onClick={onCompare}>Where else?</button><button className="button" onClick={onPackage}>Build Operational Case</button></div>
      </article>
    </>
  );
}

function CompareStage({ comparison, onPackage }: any) {
  return (
    <article className="panel">
      <span className="eyebrow">Where else?</span>
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
        <p>Policy v0.9 is shared across both groups. Calibration C and gripper firmware 7.3 co-occur across the affected runs. R06 shares the same combination and appeared stable at decision time. Low-light bin was ambient in the decision-time comparison. This narrows the investigation; root cause remains open.</p>
      </div>
      <button className="button primary" onClick={onPackage}>Build Operational Case</button>
    </article>
  );
}

function PackageStage({ pkg, verification, rec, comparison, onGenerate, onSeal }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">Operational Case</span><h2>No case package generated yet.</h2><p>Generate the case to assemble trigger, last healthy state, changes, peer comparison, missing context, decision state and outcome follow-up.</p><button className="button primary" onClick={onGenerate}>Build Operational Case</button></article>;
  }
  return (
    <article className="panel">
      <div className="package-head">
        <div><span className="eyebrow">Operational Case</span><h2>Decision state</h2><p>This case preserves what the team knew, what they chose to do, and what must be checked afterward.</p></div>
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Sealed" : "Record decision + seal"}</button>
      </div>
      <div className="package-grid">
        <PackageItem title="Trigger" value="grip pose drift after policy update" />
        <PackageItem title="Last known healthy" value={short(rec?.last_known_healthy?.event_time)} />
        <PackageItem title="Recent changes" value="policy v0.8 → v0.9 · calibration B → C · gripper firmware 7.2 → 7.3" />
        <PackageItem title="Machine state" value={rec?.current_state?.health || "degraded"} />
        <PackageItem title="Affected vs healthy" value={`${comparison?.same_signal ?? 2} / ${comparison?.same_change ?? 6}`} />
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
      <div className="callout warning">
        <b>{pkg.package.decision_substantiation?.question || "Is the team ready to act on this case?"}</b>
        <p>{pkg.package.decision_substantiation?.summary || "Decision package incomplete."}</p>
        <ul className="check-list">
          {(pkg.package.decision_substantiation?.checks || []).map((check: any) => <li key={check.name}><span>{check.status}</span><b>{check.name}</b><small>{check.evidence}</small></li>)}
        </ul>
      </div>
      <div className="callout">
        <b>What this package rules in / rules out</b>
        <p>Policy-wide issue: weak support in current peer comparison. Calibration/firmware interaction: plausible. Environment contribution: still unresolved. More low-light validation runs: current evidence supports collecting them.</p>
      </div>
      {pkg.sealed && <Signature pkg={pkg} verification={verification} />}
    </article>
  );
}

function LateEvidenceStage({ pkg, verification, comparison, onSeal, onOutcome }: any) {
  return (
    <article className="panel dramatic">
      <span className="eyebrow">Outcome update</span>
      <h2>The case keeps updating after the team acts.</h2>
      <p>R03 and R05 recover after rollback. R06 later shows the same pattern.</p>
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
        <p>Under similar conditions, rollout was paused, rollback recovered the affected machines, field dispatch was avoided, and one additional affected machine appeared later.</p>
      </div>
      <div className="package-grid memory-grid">
        <PackageItem title="What matched" value="software update + config profile + grip pose drift" />
        <PackageItem title="What worked before" value="pause rollout and roll back affected machines" />
        <PackageItem title="What was missed" value="one exposed machine became affected later" />
        <PackageItem title="Suggested next step" value="check configuration profile before dispatching a technician" />
      </div>
      {!hasMemory && <button className="button primary" onClick={onOutcome}>Link outcome first</button>}
      <div className="ending">
        <b>The company is no longer solving the same problem from zero.</b>
        <span>Every operational case should make the next one smarter.</span>
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
