"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "../../../lib/api";

type Reconstruction = any;
type Comparison = any;
type DecisionPackage = { id: string; sealed: boolean; digest?: string; signature?: string; public_key?: string; package: any };

const DEMO_ID = "inv-120-robots-bad-rollout";

export default function InvestigationClient({ id }: { id: string }) {
  const investigationId = id || DEMO_ID;
  const [rec, setRec] = useState<Reconstruction | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [pkg, setPkg] = useState<DecisionPackage | null>(null);
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
        decision: "Pause policy v0.9 on robots with calibration C and gripper firmware 7.3",
        owner: "Robotics Engineering",
        rationale: "Policy v0.9 ran everywhere, while the affected runs share calibration C and gripper firmware 7.3 with one exposed robot to watch.",
        package_id: pkg.id,
      });
      const sealed = await postJson<DecisionPackage>(`/decision-packages/${pkg.id}/seal`);
      setPkg(sealed);
      setNotice("Decision Package sealed. Later evidence cannot rewrite this snapshot.");
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
        outcome: "Calibration C plus gripper firmware 7.3 held; affected robots recovered after targeted rollback",
        payload: { previous_action: "Pause policy v0.9 on calibration C + firmware 7.3 robots", recovery_minutes: 18, days_later: 12 },
      });
      setMemory(await getJson(`/memory/similar?investigation_id=${investigationId}`));
      setStage("memory");
      setNotice("Outcome recorded into operational memory.");
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
          <button className={stage === "live" ? "active" : ""} onClick={() => setStage("live")}>Live divergence</button>
          <button className={stage === "compare" ? "active" : ""} onClick={() => setStage("compare")}>Compare</button>
          <button className={stage === "package" ? "active" : ""} onClick={() => setStage("package")}>Decision Package</button>
          <button className={stage === "late" ? "active" : ""} onClick={() => setStage("late")}>Delayed evidence</button>
          <button className={stage === "memory" ? "active" : ""} onClick={() => setStage("memory")}>12 days later</button>
          <Link href="/demo-control">Demo Control</Link>
        </nav>
        <div className="boundary">Read-only. No robot, code or stack control path.</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <span className="eyebrow">6 Robots · One Policy Update · 90 Seconds</span>
          <div className="demo-controls">
            <Link className="button" href="/cinematic">Cinematic story</Link>
            <button className="button" onClick={refresh}>Refresh</button>
            <button className="button" onClick={resetScenario}>Reset scenario</button>
            <button className="button" onClick={lateEvidence}>Inject delayed evidence</button>
          </div>
        </div>

        <section className="hero demo-hero">
          <span className="eyebrow">Real-world test run</span>
          <h1>Same model. Same task. Different behavior.</h1>
          <p>Six robots run the same manipulation policy. Two start behaving differently.</p>
          <div className="hero-actions">
            <button className="button primary" onClick={() => setStage("live")}>Start walkthrough</button>
            <button className="button lime" onClick={generatePackage}>Generate Decision Package</button>
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
          <b>Decision Package</b>
          <b>Sealed snapshot</b>
          <b>Operational Memory</b>
        </section>

        {stage === "live" && <LiveFailure rec={rec} comparison={comparison} detectionLead={detectionLead} onCompare={() => setStage("compare")} onPackage={generatePackage} />}
        {stage === "compare" && <CompareStage comparison={comparison} onPackage={generatePackage} />}
        {stage === "package" && <PackageStage pkg={pkg} rec={rec} comparison={comparison} onGenerate={generatePackage} onSeal={seal} />}
        {stage === "late" && <LateEvidenceStage pkg={pkg} comparison={comparison} onSeal={seal} onOutcome={outcome} />}
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
        <Metric label="14:11:08" value="First signal" note={rec?.first_abnormal_evidence?.payload?.signal || "grip pose drift"} />
        <Metric label="14:18:42" value="2 affected" note="pattern detected before review" />
        <Metric label="14:26:03" value="Engineer note" note="human discovery recorded" />
      </div>
      <article className="panel dramatic">
        <span className="eyebrow">Repeated real-world testing</span>
        <h2>Same policy. Same task. Different behavior.</h2>
        <p>Every robot ran policy v0.9. Only two began showing grip pose drift. The question is no longer whether something failed; it is what changed around the robots that diverged.</p>
        <div className="hero-actions"><button className="button primary" onClick={onCompare}>Compare affected vs healthy</button><button className="button" onClick={onPackage}>Generate Decision Package</button></div>
      </article>
    </>
  );
}

function CompareStage({ comparison, onPackage }: any) {
  return (
    <article className="panel">
      <span className="eyebrow">Compare</span>
      <h2>Same model. Same task. Why do only two robots diverge?</h2>
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
        <p>Policy v0.9 is shared across both groups. Calibration C and gripper firmware 7.3 concentrate in the affected runs, with one exposed robot to watch. This narrows the investigation; it does not establish cause.</p>
      </div>
      <button className="button primary" onClick={onPackage}>Generate Decision Package</button>
    </article>
  );
}

function PackageStage({ pkg, rec, comparison, onGenerate, onSeal }: any) {
  if (!pkg) {
    return <article className="panel"><span className="eyebrow">Decision Package</span><h2>No package generated yet.</h2><p>Generate the package to assemble trigger, last healthy state, changes, peer comparison, missing context and decision snapshot.</p><button className="button primary" onClick={onGenerate}>Generate Decision Package</button></article>;
  }
  return (
    <article className="panel">
      <div className="package-head">
        <div><span className="eyebrow">Decision Package</span><h2>Evidence snapshot for human action</h2><p>A decision made on Monday should not be rewritable on Tuesday. This package locks what the team knew at the moment they knew it.</p></div>
        <button className="button lime" onClick={onSeal}>{pkg.sealed ? "Sealed" : "Record decision + seal"}</button>
      </div>
      <div className="package-grid">
        <PackageItem title="Trigger" value="grip pose drift after policy update" />
        <PackageItem title="Last known healthy" value={short(rec?.last_known_healthy?.event_time)} />
        <PackageItem title="Recent changes" value="policy v0.8 → v0.9 · calibration B → C · gripper firmware 7.2 → 7.3" />
        <PackageItem title="Machine state" value={rec?.current_state?.health || "degraded"} />
        <PackageItem title="Affected vs healthy" value={`${comparison?.same_signal ?? 2} / ${comparison?.same_change ?? 6}`} />
        <PackageItem title="Observed" value="engineer note recorded at 14:26" />
        <PackageItem title="Inferred" value="calibration C + gripper firmware 7.3 is the highest-priority lead" />
        <PackageItem title="Human asserted" value="engineer suspects calibration mismatch after policy update" />
        <PackageItem title="Missing evidence" value={(pkg.package.missing_evidence || []).join(" · ")} />
        <PackageItem title="Human action" value={pkg.sealed ? "Pause policy v0.9 on calibration C + firmware 7.3 robots" : "not recorded yet"} />
        <PackageItem title="Outcome" value="pending" />
      </div>
      <div className="callout">
        <b>What this package rules in / rules out</b>
        <p>Policy-wide issue: not supported by current peer comparison. Calibration/firmware interaction: plausible. Environment contribution: still unresolved. More low-light demonstrations: current evidence supports collecting them.</p>
      </div>
      {pkg.sealed && <Signature pkg={pkg} />}
    </article>
  );
}

function LateEvidenceStage({ pkg, comparison, onSeal, onOutcome }: any) {
  return (
    <article className="panel dramatic">
      <span className="eyebrow">Delayed evidence</span>
      <h2>Late evidence reveals R06 had already shown the signal at 14:09.</h2>
      <p>This happened before the decision. The team learned about it after.</p>
      <div className="time-rail">
        <div><b>14:09</b><span>event_time</span><small>happened in runtime</small></div>
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
        <Metric label="Current view" value={`${comparison?.same_signal ?? 3}`} note="affected after delayed evidence" />
        <Metric label="Sealed package" value="Unchanged" note="new evidence cannot rewrite old context" />
      </div>
      {pkg?.sealed ? <Signature pkg={pkg} /> : <button className="button lime" onClick={onSeal}>Seal package first</button>}
      <button className="button primary" onClick={onOutcome}>Record outcome</button>
    </article>
  );
}

function MemoryStage({ memory, onOutcome }: any) {
  const hasMemory = memory?.similar_cases?.length > 0;
  return (
    <article className="panel final-stage">
      <span className="eyebrow">12 days later</span>
      <h2>{hasMemory ? "Similar operational pattern found." : "No prior outcome recorded yet."}</h2>
      {hasMemory ? (
        <>
          <p>R04 begins showing grip pose drift after a new policy test.</p>
          <div className="callout">
            <b>Similar operational pattern found</b>
            <p>Previous case: 12 days ago. Shared pattern: policy update + calibration C + grip pose drift. Previous human action: pause v0.9 on calibration C plus gripper firmware 7.3 robots. Outcome: recovered after targeted rollback. Different this time: low-light bin appears on the affected run.</p>
          </div>
          <div className="ending">
            <b>The first run took 42 minutes to understand.</b>
            <b>The next run took 42 seconds.</b>
            <span>Every real-world run should make the next one smarter.</span>
          </div>
        </>
      ) : (
        <><p>Record the outcome to turn the sealed package into reusable operational memory.</p><button className="button primary" onClick={onOutcome}>Record outcome</button></>
      )}
    </article>
  );
}

function Signature({ pkg }: { pkg: DecisionPackage }) {
  return <div className="panel good signature"><span className="eyebrow">Sealed Decision Package</span><p>Later evidence updates the current investigation. The decision-time package remains sealed.</p><p className="hash">SHA-256 {pkg.digest}</p><p className="hash">Ed25519 {pkg.signature?.slice(0, 48)}...</p></div>;
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
