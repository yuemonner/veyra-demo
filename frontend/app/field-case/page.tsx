"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import s from "./field-case.module.css";
import { Lessons, RecordingRead, SignalChain } from "./SignalChain";
import Investigation from "./Investigation";
import { plotFor, Waveform } from "./Waveform";

const API = process.env.NEXT_PUBLIC_API_URL ?? "/local-api";
const STORY_TITLE = "EX1 boom kept moving after joystick release (SYNTHETIC)";
const ACTOR = "Field engineer (synthetic demo)";
const ENGINEER = "Engineer";

type Any = any; // engine records are versioned JSON; the page reads them defensively

// Names come from the run's machine profile (the machine type decides them); these are the fallback for older runs.
let PROFILE_LABEL: Record<string, string> = {};
const dl = (d: string): string => PROFILE_LABEL[d] ?? DOMAIN_LABEL[d] ?? d;
const DOMAIN_LABEL: Record<string, string> = {
  network: "Network", control_suite: "Remote-control software", vehicle_interface: "Vehicle interface", vehicle_body: "Machine body & hydraulics",
};
const ENGINE_STATUS: Record<string, string> = {
  delay_observed: "Delay measured", anomaly_observed: "Something off", no_delay_observed_in_measured_part: "No delay where measured", not_observed: "No data",
};
const KIND_LABEL: Record<string, string> = {
  support_milestone: "Milestone", intervention_report: "Operator intervention reported", triage: "Triage", investigation_run: "Investigation run",
  reference_declared: "Known-good reference declared", check_review: "Check reviewed", check_execution: "Check executed",
  reproduction_attempt: "Reproduction attempt", hypothesis_status: "Hypothesis updated", intervention_review: "Intervention reviewed",
  decision: "Decision", dispatch_preparation: "Dispatch preparation", action: "Action", outcome: "Outcome", outcome_review: "Outcome review",
  recurrence_check: "Recurrence check", case_closure: "Case closed", next_action: "Next action", normal_proposal: "Proposed as normal", normal_review: "Normal proposal reviewed",
  request_sent: "Request sent (by a person)", agent_report: "AI agent report", decision_proposal: "Decision proposed", decision_check: "Decision checked",
};
// Veyra's own attempts and self-checks: kept for audit, folded away so they don't read as engineering decisions.
// Must match POLICY in backend/app/engine/next_action.py (the autopilot refuses plans made under other rules).
const PLAN_POLICY = "next-action-v2";
const isInternal = (e: Any) => (e.kind === "next_action" && e.payload.stage === "self_check") || e.kind === "decision_check"
  || (e.kind === "decision_proposal" && e.payload.proposed_by === "veyra");
const AVAILABILITY: Record<string, string> = {
  ready_automatic: "Can run now", ready_remote: "Remote, by a person", needs_context: "Needs context", needs_data: "Needs data",
  needs_authorization: "Needs authorization", onsite_required: "On site", answered_in_run: "Answered", already_in_case: "Already in this case", not_applicable: "Not applicable",
};

const time = (iso?: string | null, withDate = false) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const t = d.toISOString();
  return withDate ? `${t.slice(0, 10)} ${t.slice(11, 23).replace(/\.000$/, "")} UTC` : t.slice(11, 23).replace(/\.000$/, "");
};
const num = (v: number | null | undefined, digits = 3) => (v == null ? "—" : Number(v.toPrecision(digits)).toString());
const human = (v?: string | null) => (v ? v.replace(/_/g, " ") : "—");

// Hosted demo: writes need an access code. It lives in this tab's session only and is sent as a header, never in a URL.
const ACCESS_KEY = "veyra-access-code";
let memoryCode = "";
let askForCode: ((detail: string) => Promise<string | null>) | null = null;
function storedCode(): string {
  try { return window.sessionStorage.getItem(ACCESS_KEY) || memoryCode; } catch { return memoryCode; }
}
function storeCode(code: string) {
  memoryCode = code;
  try { if (code) window.sessionStorage.setItem(ACCESS_KEY, code); else window.sessionStorage.removeItem(ACCESS_KEY); } catch { /* private mode: memory only */ }
}

// The engine decides eligibility once per check (next-check-policy-v5); older runs fall back to the same rule.
const NOT_ELIGIBLE = ["answered_in_run", "already_in_case", "not_applicable", "needs_data", "needs_context"];
const isEligible = (c: Any) => (c.eligibility ? c.eligibility.eligible : !NOT_ELIGIBLE.includes(c.availability?.state) && !c.previously_performed);
const CHECK_NAME: Record<string, string> = { "CHK-NETWORK-LOGS": "the router logs", "CHK-CONTROLLER-LOGS": "the controller logs", "CHK-PROBE-PATH": "the probe path", "CHK-IO-BASELINE": "the machine's response" };
const plainEngine = (t: string) => t
  .replace(/Delay in command\.sent->command\.received rose by (\d+(?:\.\d+)?) ms for (\d+) commands? (?:inside the window|during the problem)/, "Commands took $1 ms longer to reach the machine ($2 commands during the problem)")
  .replace(/, assuming a stable clock offset \(absolute latency unavailable\)/, "; clocks aren't synced, so this is a change, not an absolute delay")
  .replace(/Heartbeat gap in \S+ \(gap > [^()]*\([^)]*\)\)/, "The controller's heartbeat went silent")
  .replace(/^Machine context for dispatch: (.*)$/, (_m: string, k: string) => "Not recorded yet: " + k.replace(/_/g, " "))
  .replace(/Authorization needed for (CHK-[A-Z-]+)/, (_m: string, c: string) => `Permission needed for ${CHECK_NAME[c] ?? c}`)
  .replace(/Site result for (CHK-[A-Z-]+):/, (_m: string, c: string) => `Site result (${CHECK_NAME[c] ?? c}):`);
// Engine text, made easier to scan: clock times instead of full ISO stamps, rule details left to the "How" toggles.
const plain = (t?: string | null) => plainEngine(t || "").replace(/\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z/g, "$1")
  .replace(/\s*\(baseline: [^;)]*(?:\([^)]*\)[^;)]*)*; (peak [^)]*)\)/g, " ($1)")
  .replace(/\s*\((?:baseline|path|rule|basis|the cause)[^)]*\)/gi, "").replace(/95th-percentile/g, "normal")
  .replace(/(inside|within|in) the symptom window/gi, "during the problem").replace(/symptom window/gi, "problem window")
  .replace(/known-good/gi, (m: string) => (m[0] === "K" ? "Normal" : "normal")).replace(/ than reference/g, " than normal").replace(/screening rule/g, "usual limit")
  .replace(/^Unconfirmed context blocks comparisons: /, "Not confirmed yet: ").replace(/^Records exist during the problem$/, "The raw data covers the problem")
  .replace(/control_version/g, "control software version").replace(/\b(firmware_version|kit_version|harness_revision|vehicle_interface|operating_conditions|measurement_endpoints|network_path|clock_sync_method)\b/g, (k: string) => k.replace(/_/g, " ")).replace(/(?<!(?:robot|airframe|aircraft) )\bmodel\b(?=,|$)/g, "robot model");
// Case titles in the picker: the "Synthetic / Real data" prefix already says what the title repeats; cut at a word.
const caseLabel = (title: string, max = 72) => {
  const t = title.replace(/\s*\(SYNTHETIC[^)]*\)\s*$/, "").replace(/\s*[—-]\s*SYNTHETIC\b[^—]*$/i, "").replace(/\s*\(real data[^)]*\)/i, "")
    .replace(/\s*[—-]\s*real [A-Z0-9]+ flight$/i, "").trim();
  return t.length <= max ? t : t.slice(0, t.lastIndexOf(" ", max - 1) > max * 0.6 ? t.lastIndexOf(" ", max - 1) : max - 1).replace(/[\s—,:-]+$/, "") + "…";
};
const firstSentence = (t: string) => t.split(/(?<=[.!?])\s+(?=[A-Z])/)[0];
function How({ children }: { children: ReactNode }) {
  return <details className={s.how}><summary>How this was measured</summary><div>{children}</div></details>;
}

// A declared normal period whose stated basis is an assumption (e.g. the upload default) is shown as assumed, never as confirmed.
const assumedBasis = (ref: Any) => /assum/i.test(ref?.basis || "");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function call(path: string, init?: RequestInit, retried = false): Promise<Any> {
  const code = typeof window === "undefined" ? "" : storedCode();
  // credentials: the private instance signs people in with an HttpOnly cookie. x-veyra-request marks the call as
  // coming from this page (a cross-site form cannot send it).
  const request = () => fetch(`${API}${path}`, { ...init, credentials: "include",
    headers: { "content-type": "application/json", "x-veyra-request": "1", ...(code ? { "x-veyra-access": code } : {}), ...(init?.headers || {}) } });
  // A hosted engine that was idle (or is restarting) answers within about a minute; reads wait for it.
  const reading = !init?.method || init.method === "GET";
  let response: Response | null = null;
  for (let attempt = 0; ; attempt++) {
    try {
      response = await request();
      if (!reading || ![502, 503, 504].includes(response.status) || attempt >= 17) break;
    } catch {
      if (!reading || attempt >= 17) throw new Error("The investigation engine did not answer.");
    }
    await sleep(5000);
  }
  if (!response) throw new Error("The investigation engine did not answer.");
  const body = await response.json().catch(() => null);
  if (response.status === 401 && body?.code === "login_required" && typeof window !== "undefined") {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    throw new Error("Sign in to continue.");
  }
  if (response.status === 401 && body?.code === "access_required") {
    storeCode("");
    if (retried) throw new Error("That access code didn't work. Check it and try again.");
    const entered = askForCode ? await askForCode(String(body.detail)) : null;
    if (!entered) throw new Error("Viewing is open; uploading and recording need an access code.");
    storeCode(entered.trim());
    return call(path, init, true);
  }
  if (!response.ok) {
    const detail = Array.isArray(body?.detail) ? body.detail.map((d: Any) => `${(d.loc || []).slice(-1)[0] ?? ""}: ${d.msg}`).join("; ") : body?.detail;
    throw new Error(detail ? String(detail) : `${response.status} ${response.statusText}`);
  }
  return body;
}

function AccessDialog({ detail, onDone }: { detail: string; onDone: (code: string | null) => void }) {
  const [code, setCode] = useState("");
  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-labelledby="access-title">
      <form className={s.dialog} style={{ maxWidth: 420 }} onSubmit={(e) => { e.preventDefault(); onDone(code || null); }}>
        <h2 id="access-title" style={{ margin: "0 0 6px" }}>Enter your access code</h2>
        <p className={s.note} style={{ marginTop: 0 }}>{detail} Ask the workspace owner for the access code.</p>
        <label className={s.field}>Access code
          <input type="password" autoComplete="off" autoFocus value={code} onChange={(e) => setCode(e.target.value)} />
        </label>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
          <button type="button" className={s.quiet} onClick={() => onDone(null)}>Cancel</button>
          <button type="submit" className={s.button} disabled={!code.trim()}>Continue</button>
        </div>
      </form>
    </div>
  );
}

export default function FieldCasePage() {
  const [accessAsk, setAccessAsk] = useState<{ detail: string; resolve: (code: string | null) => void } | null>(null);
  useEffect(() => {
    askForCode = (detail) => new Promise((resolve) => setAccessAsk({ detail, resolve }));
    return () => { askForCode = null; };
  }, []);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [data, setData] = useState<Any>(null);
  const [verification, setVerification] = useState<Any>(null);
  const [state, setState] = useState<"loading" | "empty" | "ready" | "error" | "busy">("loading");
  const [error, setError] = useState("");
  const [agentInfo, setAgentInfo] = useState<Any>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentError, setAgentError] = useState("");
  const [tab, setTab] = useState<"case" | "brief" | "next" | "evidence" | "audit">("case");
  const [jobId, setJobId] = useState<string | null>(null);
  const [cases, setCases] = useState<Any[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [tabularFiles, setTabularFiles] = useState<File[]>([]);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [me, setMe] = useState<Any>(null);
  const [acceptBusy, setAcceptBusy] = useState(false);
  const isPrivate = !!me?.private;

  const go = (section: string) => {
    if (section === "next") { setTab("next"); return; }
    setTab(["triage", "sources", "decisions", "closure", "record"].includes(section) ? "audit" : "evidence");
    setTimeout(() => document.getElementById(section)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };

  async function acceptCheck(check: Any) {
    if (!caseId || !data?.latest_run) return;
    setAcceptBusy(true);
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "check_review", actor: data?.case?.synthetic ? ACTOR : ENGINEER, source: "case brief",
        payload: { run_id: data.latest_run.id, check_id: check.check_id, verdict: "accepted", reason: "Accepted from the case brief" } }) });
      if (check.availability.state === "ready_automatic") {
        await call(`/engine/cases/${caseId}/checks/${check.check_id}/execute`, { method: "POST", body: JSON.stringify({ actor: data?.case?.synthetic ? ACTOR : ENGINEER }) });
      }
      await load(caseId);
    } catch (e: Any) {
      setAgentError(e.message);
    } finally {
      setAcceptBusy(false);
    }
  }

  const load = useCallback(async (id: string) => {
    const [detail, ledger] = await Promise.all([call(`/engine/cases/${id}`), call(`/engine/cases/${id}/ledger`)]);
    PROFILE_LABEL = Object.fromEntries(Object.entries(detail.latest_run?.machine_profile?.domains || {}).map(([k, v]: [string, Any]) => [k, v.label]));
    setData(detail);
    setVerification(ledger.verification);
    setCaseId(id);
    setState("ready");
    try { window.history.replaceState(null, "", `?case=${id}`); } catch { /* preview frames may block history */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const who = await fetch(`${API}/auth/me`, { credentials: "include" }).then((r) => r.json()).catch(() => null);
        if (who?.private && !who.user) { window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`; return; }
        setMe(who);
        call("/engine/connectors").then((c) => setAgentInfo(c.agent)).catch(() => setAgentInfo(null));
        const wanted = new URLSearchParams(window.location.search).get("case");
        const all: Any[] = (await call("/engine/cases")).filter((c: Any) => c.fault_type !== "known_good_run");
        setCases(all);
        // Showcase first: the earliest real case (the seeded demo case on a hosted engine), so a visitor's
        // upload never becomes what everyone else sees. Uploaders are taken to their own case directly.
        const real = all.find((c) => !c.synthetic);
        const story = (wanted && all.find((c) => c.id === wanted)) || real || all.filter((c) => c.title === STORY_TITLE).pop() || all[all.length - 1];
        if (story) await load(story.id);
        else setState("empty");
      } catch (e: Any) {
        setError(e.message);
        setState("error");
      }
    })();
  }, [load]);

  async function createStory() {
    setState("busy");
    try {
      const out = await call("/engine/demo/field-case", { method: "POST", body: JSON.stringify({ actor: ACTOR }) });
      await load(out.case_id);
    } catch (e: Any) {
      setError(e.message);
      setState("error");
    }
  }

  async function liveEvent() {
    setState("busy");
    try {
      const out = await call("/engine/demo/live-event", { method: "POST" });
      setCases((await call("/engine/cases")).filter((c: Any) => c.fault_type !== "known_good_run"));
      await load(out.case_id);
    } catch (e: Any) {
      setError(e.message);
      setState("error");
    }
  }

  async function runAgent(question?: string) {
    if (!caseId) return;
    setAgentBusy(true);
    setAgentError("");
    try {
      await call(`/engine/cases/${caseId}/agent`, { method: "POST", body: JSON.stringify({ actor: "Veyra agent", ...(question ? { question } : {}) }) });
      await load(caseId);
    } catch (e: Any) {
      setAgentError(e.message);
    } finally {
      setAgentBusy(false);
    }
  }

  async function rerun() {
    if (!caseId) return;
    setState("busy");
    try {
      await call(`/engine/cases/${caseId}/runs`, { method: "POST", body: JSON.stringify({ actor: data?.case?.synthetic ? ACTOR : ENGINEER }) });
      await load(caseId);
    } catch (e: Any) {
      setError(e.message);
      setState("error");
    }
  }

  const run = data?.latest_run;
  return (
    <div className={s.page}>
      {accessAsk && <AccessDialog detail={accessAsk.detail} onDone={(code) => { accessAsk.resolve(code); setAccessAsk(null); }} />}
      <header className={s.topbar}>
        <Link href="/field-case" className={s.brand}><span className={s.mark}>V</span>Veyra</Link>
        {cases.length > 1 ? (
          <label className={s.casePick}>
            <span className={s.visuallyHidden}>Case</span>
            <select value={caseId ?? ""} onChange={(e) => { setTab("case"); load(e.target.value); }}>
              {[...cases].reverse().map((c) => (
                <option key={c.id} value={c.id}>{c.synthetic ? "Synthetic · " : "Real data · "}{caseLabel(c.title)}</option>))}
            </select>
          </label>
        ) : <span className={s.crumb}>Field case</span>}
        <span className={s.spacer} />
        <div className={s.actions}>
          <button className={s.button} onClick={() => (isPrivate ? setRecordingOpen(true) : setUploadOpen(true))}>{isPrivate ? "Upload data" : "Upload logs"}</button>
          {!isPrivate && <button className={s.quiet} onClick={liveEvent} disabled={state === "busy"}>Simulate live takeover</button>}
          {caseId && <a className={s.quiet} href={`${API}/engine/cases/${caseId}/report`} target="_blank" rel="noreferrer">Open report</a>}
          {caseId && isPrivate && <button className={s.quiet} onClick={() => setShareOpen(true)}>Share</button>}
          {isPrivate && me?.user && <span className={s.who} title={me.user.email}>{me.user.name} · {me.user.workspace_name}
            {!me.signin_off && <button className={s.link} onClick={async () => { await fetch(`${API}/auth/logout`, { method: "POST", credentials: "include", headers: { "x-veyra-request": "1" } }); window.location.href = "/login"; }}>Sign out</button>}</span>}
          {caseId && data?.rerun_allowed !== false && <button className={s.quiet} onClick={rerun} disabled={state === "busy"}>{state === "busy" ? "Investigating…" : "Investigate again"}</button>}
        </div>
      </header>

      {state === "loading" && <div className={s.center}><p>Loading the field case…</p><p className={s.note}>After a quiet period the first visit can take up to a minute.</p></div>}
      {state === "error" && <div className={s.center}><p className={s.error}>{error}</p><p className={s.note}>The investigation engine is starting up. Reload this page in a minute.</p></div>}
      {(state === "empty" || (state === "busy" && !run)) && (
        <div className={s.center}>
          <div>
            <h1 style={{ fontSize: 26, letterSpacing: "-0.02em", margin: "0 0 8px" }}>{isPrivate ? `No cases in ${me?.user?.workspace_name || "this workspace"} yet` : "No field case loaded yet"}</h1>
            {isPrivate ? <>
              <p className={s.lede}>Upload a robot recording or exported logs. Veyra reads it and writes a first debrief.</p>
              <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
                <button className={s.button} onClick={() => setRecordingOpen(true)}>Upload data</button>
              </div>
            </> : <>
            <p className={s.lede}>Try a synthetic example: an operator takes over an excavator.</p>
            <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
              <button className={s.button} onClick={createStory} disabled={state === "busy"}>{state === "busy" ? "Building the case…" : "Load the synthetic field case"}</button>
              <button className={s.quiet} onClick={liveEvent} disabled={state === "busy"}>Simulate live takeover</button>
              <button className={s.quiet} onClick={() => setUploadOpen(true)}>Upload your own logs</button>
            </div>
            <p className={s.note}>Synthetic records only. Not field evidence and not private data.</p>
            </>}
          </div>
        </div>
      )}

      {run && data && (
        <main className={s.body}>
          <nav className={s.views} aria-label="Views of this case">
            {([
              ["case", "Investigation", "What happened, what Veyra checked, what to do next", ""],
              ["evidence", "Evidence", "Findings and their sources", String(Object.values(run.domains || {}).reduce((n: number, d: Any) => n + (d.observations || []).filter((o: Any) => o.kind === "anomaly" || o.kind === "delay").length, 0))],
              ["audit", "Record", "Owners, decisions and the case record", String((data.ledger || []).length)],
            ] as const).map(([id, label, hint, count]) => (
              <button key={id} className={tab === id ? s.viewOn : s.view} onClick={() => setTab(id)} aria-pressed={tab === id} title={hint}>
                <span className={s.viewLabel}>{label}</span>
                {count && <span className={s.viewCount}>{count}</span>}
              </button>))}
          </nav>
          {tab === "next" && <NextAction caseId={caseId!} data={data} onChanged={() => load(caseId!)} me={me?.user?.name} onEvidence={() => go("findings")} />}
          {tab === "case" && <Investigation key={caseId!} caseId={caseId!} call={call} actor={me?.user?.name || ENGINEER} jobId={jobId} onChanged={() => load(caseId!)} />}
          {tab === "brief" && <Brief data={data} run={run} verification={verification} go={go} onAccept={acceptCheck} acceptBusy={acceptBusy} error={agentError} />}
          {tab === "evidence" && <>
            <AgentPanel ledger={data.ledger} run={run} info={agentInfo} busy={agentBusy} error={agentError} onRun={runAgent} />
            <Hypotheses run={run} ledger={data.ledger} />
            <Findings run={run} ledger={data.ledger} caseId={caseId!} onSaved={() => load(caseId!)} />
            <Reference run={run} />
            {((run.findings.actuator_outputs?.findings || []).length > 0 || (run.findings.actuator_outputs?.machine_baseline?.suppressed_as_normal || []).length > 0) && <Outputs run={run} />}
            {run.findings.tracking?.status === "computed" && <Tracking run={run} />}
            {(run.findings.transients?.events_total || 0) > 0 && <BriefEvents run={run} />}
            {(run.interventions || []).length > 0 && <Intervention run={run} ledger={data.ledger} />}
            {(run.machine_history?.prior_cases || []).length > 0 && <History run={run} />}
            <Checks run={run} ledger={data.ledger} />
          </>}
          {tab === "audit" && <>
            <Triage run={run} />
            <Sources ledger={data.ledger} imports={data.imports} />
            <Decisions ledger={data.ledger} run={run} caseId={caseId!} onSaved={() => load(caseId!)} />
            <Closure ledger={data.ledger} run={run} caseId={caseId!} onSaved={() => load(caseId!)} />
            <NormalReviews ledger={data.ledger} caseId={caseId!} onSaved={() => load(caseId!)} />
            <Record ledger={data.ledger} verification={verification} caseId={caseId!} />
          </>}
        </main>
      )}
      {uploadOpen && <UploadDialog initialFiles={tabularFiles} onClose={() => { setUploadOpen(false); setTabularFiles([]); }} onDone={async (id: string) => {
        setUploadOpen(false); setCases((await call("/engine/cases")).filter((c: Any) => c.fault_type !== "known_good_run")); setTab("case"); await load(id); }} />}
      {recordingOpen && <RecordingDialog onTabular={(files: File[]) => { setRecordingOpen(false); setTabularFiles(files); setUploadOpen(true); }} onClose={() => setRecordingOpen(false)} onDone={async (id: string, job?: string) => {
        setRecordingOpen(false); setJobId(job || null); setCases((await call("/engine/cases")).filter((c: Any) => c.fault_type !== "known_good_run")); setTab("case"); await load(id); }} />}
      {shareOpen && caseId && <ShareDialog caseId={caseId} title={data?.case?.title || ""} onClose={() => setShareOpen(false)} />}
      {!isPrivate && <p className={s.legacy}>Looking for the earlier workspace? <a href="/product-demo?legacy=1">Open it</a> (manual analysis, older engine).</p>}
    </div>
  );
}

function Triage({ run }: { run: Any }) {
  const t = run.triage;
  if (!t) return null;
  return (
    <section id="triage" className={s.panel}>
      <h2>Triage and ownership</h2>
      <p className={s.lede}>Suggested from the evidence. A person confirms.</p>
      <dl className={s.kv}>
        <dt>Severity</dt>
        <dd title={`${t.proposed.severity.basis} (${t.proposed.severity.rule})`}><span className={`${s.pill} ${t.proposed.severity.level === "high" ? s.anomaly : s.thread}`}>{t.proposed.severity.level}</span></dd>
        <dt>Owner</dt>
        <dd title={t.proposed.owner.basis}><b>{t.proposed.owner.team}</b></dd>
        {t.proposed.also_open.length > 0 && <><dt>Also open</dt><dd>{t.proposed.also_open.map((o: Any) => o.team).join(", ")}</dd></>}
        {t.proposed.route_away.length > 0 && <><dt>Not for</dt><dd>{t.proposed.route_away.map((o: Any) => `${o.team} (${o.why})`).join("; ")}</dd></>}
      </dl>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "18px 0 10px", color: "var(--sr-ink-2)" }}>Confirmed</h3>
      {t.history.length === 0 ? <p className={s.empty}>Not confirmed yet.</p> : (
        <ol className={s.timeline}>
          {t.history.map((h: Any) => (
            <li key={h.entry_id}>
              <span className={`${s.dot} ${h.handoff ? s.handoff : ""}`} aria-hidden />
              <div>
                <strong>{h.owner}</strong> · {h.severity}{h.handoff ? <span className={`${s.pill} ${s.anomaly}`} style={{ marginLeft: 8 }}>handed off</span> : null}
                <p>{h.reason}</p>
                <p className={s.note} style={{ margin: 0 }}>{h.by} · {time(h.at, true)}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
      {t.handoff_warning && <p className={s.warning}>{t.handoff_warning}</p>}
      <How>Severity: {t.proposed.severity.basis} ({t.proposed.severity.rule}). Owner: {t.proposed.owner.basis}.</How>
    </section>
  );
}

function Reference({ run }: { run: Any }) {
  const [showAll, setShowAll] = useState(false);
  const ref = run.reference;
  const cmp = run.findings.reference_comparison;
  const declared = ref.status === "declared" || ref.status === "explicit";
  return (
    <section id="reference" className={s.panel}>
      <h2>Compared against</h2>
      <p className={s.lede}>What normal looks like for this case.</p>
      <dl className={s.kv}>
        <dt>Normal period</dt>
        <dd><span className={`${s.pill} ${declared && !assumedBasis(ref) ? s.verified : s.anomaly}`}>{declared ? (assumedBasis(ref) ? `assumed normal, set by ${ref.by || "a person"}` : `declared normal by ${ref.by || "a person"}`) : ref.status === "assumed_default" ? "assumed normal" : "none"}</span>{" "}
          <span className={s.mono}>{ref.window_iso ? `${time(ref.window_iso[0])}–${time(ref.window_iso[1])}` : ""}</span></dd>
        <dt>Why</dt><dd>{ref.basis}</dd>

      </dl>
      {cmp?.status === "computed" && (
        <div className={s.tableWrap} style={{ marginTop: 14 }}>
          <table className={s.table}>
            <thead><tr><th>Signal</th><th style={{ textAlign: "right" }}>Normal</th><th style={{ textAlign: "right" }}>During problem</th><th></th></tr></thead>
            <tbody>
              {cmp.signals.filter((g: Any) => showAll || g.status !== "within_reference_range").map((g: Any) => (
                <tr key={`${g.signal}${g.scope}${g.clock_domain}`}>
                  <td>{g.signal.replace("vehicle.", "").replace("network.", "")}<br /><small style={{ color: "var(--sr-ink-3)" }}>{g.scope}</small></td>
                  <td className={s.num}>{num(g.reference.median)} {g.unit}</td>
                  <td className={s.num}>{num(g.incident.median)} {g.unit}</td>
                  <td><span className={`${s.pill} ${g.status === "within_reference_range" ? s.verified : g.status === "insufficient_samples" ? "" : s.anomaly}`}>{g.status === "within_reference_range" ? "normal" : g.status === "above_reference_range" || g.status === "higher_than_reference" ? "higher" : g.status === "below_reference_range" || g.status === "lower_than_reference" ? "lower" : human(g.status).replace(" than reference", "")}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {(() => { const normal = cmp.signals.filter((g: Any) => g.status === "within_reference_range").length; return normal > 0 ? (
            <button className={s.link} onClick={() => setShowAll(!showAll)}>{showAll ? "Hide normal signals" : `Show ${normal} normal signal${normal === 1 ? "" : "s"}`}</button>) : null; })()}
          <How>Medians per window. Rule: {cmp.rule}. {cmp.clock_note}</How>
        </div>
      )}
    </section>
  );
}

function Spark({ event }: { event: Any }) {
  const pts = event.context as { t: number; v: number }[];
  if (!pts?.length) return null;
  const w = 300, h = 86, pad = 6;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const vs = pts.map((p) => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const x = (t: number) => pad + ((t - t0) / (t1 - t0 || 1)) * (w - 2 * pad);
  const y = (v: number) => h - pad - ((v - lo) / (hi - lo || 1)) * (h - 2 * pad);
  const inEvent = (t: number) => t >= event.t_start && t <= event.t_end;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Samples around the brief event at ${time(event.start)}`}>
      <line x1={pad} x2={w - pad} y1={y(event.local_level)} y2={y(event.local_level)} stroke="rgba(35,37,92,.2)" strokeDasharray="3 3" />
      <polyline points={pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} fill="none" stroke="var(--sr-thread)" strokeWidth="1.5" strokeLinejoin="round" />
      {pts.filter((p) => inEvent(p.t)).map((p) => <circle key={p.t} cx={x(p.t)} cy={y(p.v)} r="3.5" fill="#f2a93b" stroke="#fff" strokeWidth="1.5" />)}
    </svg>
  );
}

function BriefEvents({ run }: { run: Any }) {
  const tr = run.findings.transients;
  if (!tr) return null;
  const takeover = run.interventions?.[0];
  const window = run.symptom_window;
  const coverage = run.findings.coverage || [];
  return (
    <section id="brief" className={s.panel}>
      <h2>Short spikes</h2>
      <p className={s.lede}>Too short to see on a plot. {tr.events_total} found, {tr.events_in_window} during the problem.</p>
      {tr.signals.filter((g: Any) => g.events.length).map((g: Any) => {
        const cov = coverage.find((c: Any) => c.signal === g.signal && c.clock_domain === g.clock_domain);
        const span = cov ? cov.end - cov.start : 0;
        const pos = (t: number) => (span ? ((t - cov.start) / span) * 100 : 0);
        const ws = window ? new Date(window[0]).getTime() / 1000 : null, we = window ? new Date(window[1]).getTime() / 1000 : null;
        return (
          <div key={g.signal + g.scope}>
            <p style={{ margin: "0 0 4px" }}><b>{g.signal.replace("vehicle.", "").replace(/_/g, " ")}</b> · {g.scope} · <span className={s.mono}>{num(g.rate_hz)} Hz, {g.samples.toLocaleString("en")} samples</span></p>
            {cov && (
              <>
                <div className={s.strip} aria-label="Whole recording with the symptom window and event positions">
                  {ws != null && we != null && <div className={s.stripWindow} style={{ left: `${pos(ws)}%`, width: `${pos(we) - pos(ws)}%` }} />}
                  {g.events.map((e: Any) => <div key={e.start} className={s.stripTick} style={{ left: `${pos(e.t_start)}%` }} />)}
                </div>
                <div className={s.stripAxis}><span className={s.mono}>{time(new Date(cov.start * 1000).toISOString())}</span><span>Shaded: the problem</span><span className={s.mono}>{time(new Date(cov.end * 1000).toISOString())}</span></div>
              </>
            )}
            <div className={s.events}>
              {g.events.map((e: Any) => {
                const offset = takeover ? Math.round((e.t_start - new Date(takeover.occurred_at).getTime() / 1000) * 1000) : null;
                return (
                  <article key={e.start} className={s.event}>
                    <header>
                      <b className={s.mono}>{time(e.start)}</b>
                      <span className={`${s.pill} ${e.in_symptom_window ? s.anomaly : ""}`}>{e.in_symptom_window ? "during the problem" : "before"}</span>
                    </header>
                    <Spark event={e} />
                    <p><span className={s.mono}>{e.samples}</span> sample{e.samples > 1 ? "s" : ""} · <span className={s.mono}>{e.duration_ms} ms</span> · {e.direction === "up" ? "spike" : "drop"} to <span className={s.mono}>{num(e.peak_value)} {g.unit}</span> from about <span className={s.mono}>{num(e.local_level)}</span></p>
                    {offset != null && Math.abs(offset) < 5000 && <p><b>{offset > 0 ? "+" : ""}{offset} ms</b> from the takeover <span className={s.dim}>(clocks may differ)</span></p>}
                    <p className={s.note} style={{ margin: 0 }}>Rows {e.rows.map((r: Any) => r.row).join(", ")}</p>
                  </article>
                );
              })}
            </div>
          </div>
        );
      })}
      <How>Every sample of every signal at 5 Hz or faster was checked, not a plot. {tr.skipped?.length > 0 ? `Not scanned: ${tr.skipped.map((k: Any) => `${k.signal} (${k.reason.toLowerCase()})`).join("; ")}. ` : ""}{tr.boundary}</How>
    </section>
  );
}

function Hypotheses({ run, ledger }: { run: Any; ledger: Any[] }) {
  const byId: Record<string, Any> = Object.fromEntries(ledger.map((e) => [e.id, e]));
  const evidenceName = (id: string) => {
    const e = byId[id];
    if (!e) return id;
    if (e.kind === "reproduction_attempt") return `${human(e.payload.environment)} (${human(e.payload.result)})`;
    return `${e.payload.check_id} (${human(e.payload.status)})`;
  };
  const board: Any[] = run.hypotheses || [];
  const columns: [string, string, string][] = [["supported", "Supported", s.supported], ["open", "Open", s.open], ["ruled_out", "Ruled out", s.ruledOut]];
  return (
    <section id="hypotheses" className={s.panel}>
      <h2>Which part is it?</h2>
      <p className={s.lede}>Findings keep a part open. A part is supported only by a measured delay or a person's verdict.</p>
      <div className={s.board}>
        {columns.map(([key, label, cls]) => {
          const items = board.filter((b) => b.column === key);
          return (
            <div key={key} className={s.column}>
              <h3><span>{label}</span><span className={s.mono}>{items.length}</span></h3>
              {items.length === 0 && <p className={s.empty}>None</p>}
              {items.map((b) => (
                <article key={b.domain} className={`${s.card} ${cls}`}>
                  <h4>{dl(b.domain)}</h4>
                  <p><span className={s.pill}>{ENGINE_STATUS[b.engine_status] ?? human(b.engine_status)}</span> <span className={s.by}>{b.evidence_count} finding{b.evidence_count === 1 ? "" : "s"} · {b.owner}</span></p>
                  {b.human ? (
                    <>
                      <p>{b.human.basis}</p>
                      {b.human.scope && <p><b>Scope:</b> {b.human.scope}</p>}
                      <p className={s.by}>{human(b.human.status)} by {b.human.by} · evidence: {[...b.human.evidence_entries.map(evidenceName), ...b.human.finding_rules.map((r: string) => `finding ${r}`)].join(", ") || "—"}</p>
                    </>
                  ) : <p className={s.by}>{b.engine_hint ?? "Nobody has checked yet"}</p>}
                  {b.human && b.engine_hint && <p className={s.by}>{b.engine_hint}</p>}
                  {b.human?.note && <p className={s.by}><b>{b.human.note}</b></p>}
                  {b.partly_ruled_out && b.partly_ruled_out.remaining.length > 0 && <p className={s.by}>
                    Ruled out for what was named only. Still open: {b.partly_ruled_out.remaining.map((k: string) => k.split("|").filter((x) => x !== "*").slice(1).join(" ") || k.split("|")[0]).join("; ")}</p>}
                </article>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Checks({ run, ledger }: { run: Any; ledger: Any[] }) {
  const all: Any[] = run.checks || [];
  const ran: Record<string, Any> = Object.fromEntries(ledger.filter((e) => e.kind === "check_execution" && e.payload.run_id === run.id).map((e) => [e.payload.check_id, e]));
  const open = all.filter((c) => isEligible(c) && !ran[c.check_id]);
  const done = all.filter((c) => !open.includes(c));
  return (
    <section id="checks" className={s.panel}>
      <h2>Next checks</h2>
      <p className={s.lede}>In order. Each says why.</p>
      <ol className={s.checks}>
        {open.slice(0, 6).map((c, i) => (
          <li key={c.check_id} className={s.check}>
            <span className={s.rank}>{i + 1}</span>
            <div>
              <strong>{plain(c.question)}</strong>
              <p>{plain(c.trigger ?? c.why)}</p>
              <p className={s.note} style={{ margin: "4px 0 0" }}>{c.who}</p>
            </div>
            <span className={`${s.pill} ${c.availability.state === "ready_automatic" ? s.thread : c.availability.state === "onsite_required" ? s.anomaly : ""}`}>{AVAILABILITY[c.availability.state] ?? human(c.availability.state)}</span>
          </li>
        ))}
      </ol>
      {done.length > 0 && (
        <details className={s.done}>
          <summary>{done.length} done</summary>
          <ul>{done.map((c) => <li key={c.check_id}>{plain(c.question)} — {ran[c.check_id] ? `done by ${ran[c.check_id].actor}${ran[c.check_id].payload.tool_result?.count != null ? ` (${ran[c.check_id].payload.tool_result.count} records)` : ""}` : plain(c.availability.detail) || "done"}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function Intervention({ run, ledger }: { run: Any; ledger: Any[] }) {
  const items: Any[] = run.interventions || [];
  const repro = ledger.filter((e) => e.kind === "reproduction_attempt");
  return (
    <section id="intervention" className={s.panel}>
      <h2>When someone stepped in</h2>
      <p className={s.lede}>That moment is evidence. Reproducing it tests the idea before anyone travels.</p>
      {items.length === 0 && <p className={s.empty}>Nobody stepped in.</p>}
      {items.map((i) => (
        <div key={i.entry_id} style={{ display: "grid", gap: 10 }}>
          <dl className={s.kv}>
            <dt>What</dt><dd>{human(i.intervention_type)} by {i.operator} at <span className={s.mono}>{time(i.occurred_at)}</span></dd>
            <dt>Said</dt><dd>“{i.stated_reason}”</dd>
            {i.symptom && <><dt>Reported symptom</dt><dd>{symptomText({ symptom: i.symptom, function: i.affected_function })} </dd></>}
            <dt>In the data nearby</dt>
            <dd>{i.evidence_nearby.length === 0 ? "Nothing" : (
              <ul style={{ margin: 0, paddingLeft: 18 }}>{i.evidence_nearby.slice(0, 4).map((n: Any, k: number) => <li key={k}><span className={s.mono}>{n.offset_ms > 0 ? "+" : ""}{n.offset_ms} ms</span> {n.signal.replace("vehicle.", "").replace(/_/g, " ")} — {n.detail}</li>)}</ul>
            )}</dd>
            <dt>Review</dt>
            <dd>{i.review ? <><span className={`${s.pill} ${i.review.verdict === "necessary" ? s.verified : s.anomaly}`}>{i.review.verdict}</span> {i.review.reason} <span className={s.note}>— {i.review.by}</span></> : <span className={`${s.pill} ${s.anomaly}`}>not reviewed</span>}</dd>
          </dl>
          <How>{i.clock_note}</How>
        </div>
      ))}
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "16px 0 8px", color: "var(--sr-ink-2)" }}>Tried to reproduce</h3>
      {repro.length === 0 ? <p className={s.empty}>Not yet.</p> : repro.map((r) => (
        <div key={r.id} className={s.card} style={{ borderLeft: "3px solid var(--sr-thread)" }}>
          <p><span className={`${s.pill} ${r.payload.result === "reproduced" ? s.thread : ""}`}>{human(r.payload.result)}</span> <b>{human(r.payload.environment)}</b> · {r.payload.platform}</p>
          <p>{r.payload.setup}</p>
          <p>{r.payload.observations}</p>
          <p className={s.by}>{r.actor} · {r.payload.boundary}</p>
        </div>
      ))}
    </section>
  );
}

function History({ run }: { run: Any }) {
  const h = run.machine_history;
  if (!h) return null;
  return (
    <section id="history" className={s.panel}>
      <h2>This machine before</h2>
      <p className={s.lede}>Earlier cases on {run.machine_id}.</p>
      {h.notes.map((n: Any) => <p key={n.domain} className={s.warning} style={{ background: "var(--sr-thread-soft)", color: "var(--sr-thread-deep)", marginTop: 0 }}>{n.text}</p>)}
      {h.prior_cases.length === 0 ? <p className={s.empty}>No earlier cases on this machine.</p> : (
        <div className={s.tableWrap}>
          <table className={s.table}>
            <thead><tr><th>Case</th><th>When</th><th>Operator</th><th>Found in</th><th>Decided</th><th>Result</th><th>Cause</th></tr></thead>
            <tbody>{h.prior_cases.map((c: Any) => (
              <tr key={c.case_id}>
                <td>{c.title.replace(/\s*\(SYNTHETIC[^)]*\)\s*$/, "")}</td>
                <td className={s.mono}>{c.symptom_window ? c.symptom_window[0].slice(0, 10) : "—"}</td>
                <td>{c.operators.join(", ") || "—"}</td>
                <td>{c.observed_domains.map((d: string) => dl(d)).join(", ") || "—"}</td>
                <td>{c.decision ?? "—"}</td>
                <td>{c.outcomes.map((o: Any) => `${human(o.result)}${o.verification === "unverified" ? " (unverified)" : ""}`).join(", ") || "—"}</td>
                <td>{c.closure ? <><b>{causeTitle(c.closure)}</b>{hasCause(c.closure) ? <>: {dl(c.closure.cause_domain)}, {c.closure.cause} <StrengthBadge p={c.closure} /></> : null}<span className={s.dim}> · machine: {label(RESOLUTION, c.closure.resolution_status ?? (c.closure.fix ? "resolved" : null)).toLowerCase()}</span>{c.closure.fix ? <span className={s.dim}> · fix: {c.closure.fix} ({human(c.closure.fix_held)})</span> : null}{c.closure.decisive_check ? <span className={s.dim}> · settled by {c.closure.decisive_check}</span> : null}</> : <span className={s.dim}>not closed</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <How>{h.boundary}</How>
    </section>
  );
}

const ACTION_TYPES = ["remote_reconfiguration", "remote_restart", "software_update", "network_change", "operating_change", "onsite_inspection", "component_replacement", "calibration", "other"];
const RESULTS = [["recovered", "Recovered"], ["partially_recovered", "Partly recovered"], ["not_recovered", "Not recovered"], ["unknown", "Unknown yet"]];
const nowLocal = () => new Date().toISOString().slice(0, 19);

function Decisions({ ledger, run, caseId, onSaved }: { ledger: Any[]; run: Any; caseId: string; onSaved: () => Promise<void> }) {
  const decisions = ledger.filter((e) => e.kind === "decision");
  const actions = ledger.filter((e) => e.kind === "action");
  const outcomes = ledger.filter((e) => e.kind === "outcome");
  const lastDecision = decisions[decisions.length - 1];
  const actionFor = lastDecision && actions.filter((a) => a.payload.decision_id === lastDecision.id).slice(-1)[0];
  const outcomeFor = actionFor && outcomes.filter((o) => o.payload.action_id === actionFor.id).slice(-1)[0];
  const step = !lastDecision || outcomeFor ? "decision" : !actionFor ? "action" : actionFor.payload.status === "not_performed" ? "decision" : "outcome";
  const [options, setOptions] = useState("");
  const [chosen, setChosen] = useState("");
  const [why, setWhy] = useState("");
  const [actionType, setActionType] = useState("other");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState("performed");
  const [performedAt, setPerformedAt] = useState(nowLocal());
  const [performer, setPerformer] = useState("");
  const [result, setResult] = useState("unknown");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState(nowLocal());
  const [observations, setObservations] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = options.split("\n").map((o) => o.trim()).filter(Boolean);

  async function save(kind: string, payload: Any) {
    setBusy(true); setError("");
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind, actor: ENGINEER, source: "field case page", payload }) });
      await onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  }

  return (
    <section id="decisions" className={s.panel}>
      <h2>Decide, act, check</h2>
      <p className={s.lede}>Record each step when it happens.</p>
      {decisions.length > 0 && (
        <ol className={s.ledger} style={{ marginBottom: 14 }}>
          {decisions.map((d) => {
            const acts = actions.filter((a) => a.payload.decision_id === d.id);
            return (
              <li key={d.id} style={{ display: "block" }}>
                <div><b>Decided:</b> {d.payload.chosen_option} <span className={s.dim}>· {d.payload.rationale}</span></div>
                {acts.map((a) => {
                  const outs = outcomes.filter((o) => o.payload.action_id === a.id);
                  return (
                    <div key={a.id} style={{ marginLeft: 14 }}>
                      <div><b>{a.payload.status === "not_performed" ? "Not done" : a.payload.status === "simulated" ? "Simulated" : "Done"}:</b> {a.payload.description}</div>
                      {outs.map((o) => <div key={o.id} style={{ marginLeft: 14 }}><b>Outcome:</b> {human(o.payload.result)} <span className={s.dim}>· {o.payload.observations} ({o.payload.verification === "unverified" ? "reported, not yet verified" : "evidence attached"})</span></div>)}
                    </div>);
                })}
              </li>);
          })}
        </ol>
      )}

      {step === "decision" && (
        <form onSubmit={(e) => { e.preventDefault(); save("decision", { run_id: run.id, options_considered: list, chosen_option: chosen || list[0], rationale: why }); setOptions(""); setChosen(""); setWhy(""); }}>
          <div className={s.formGrid}>
            <label className={s.field}><span>Options (one per line)</span><textarea rows={3} value={options} onChange={(e) => setOptions(e.target.value)} placeholder={"Restart the controller remotely\nSend a technician"} /></label>
            <label className={s.field}><span>Chosen option</span>
              <select value={chosen || list[0] || ""} onChange={(e) => setChosen(e.target.value)} disabled={!list.length}>
                {list.map((o) => <option key={o} value={o}>{o}</option>)}
              </select></label>
          </div>
          <label className={s.field}><span>Why</span><input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Cheapest reversible step first" /></label>
          <button className={s.button} disabled={busy || !list.length || !why.trim()} style={{ marginTop: 10 }}>Record decision</button>
        </form>
      )}

      {step === "action" && (
        <form onSubmit={(e) => { e.preventDefault(); save("action", { decision_id: lastDecision.id, action_type: actionType, description, status,
            ...(status === "not_performed" ? { reason: description } : { performed_at: toIso(performedAt), performer }) }); }}>
          <p className={s.note} style={{ marginTop: 0 }}>What was done for: <b>{lastDecision.payload.chosen_option}</b></p>
          <div className={s.formGrid}>
            <label className={s.field}><span>What was done</span><input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Controller restarted remotely" /></label>
            <label className={s.field}><span>Type</span><select value={actionType} onChange={(e) => setActionType(e.target.value)}>{ACTION_TYPES.map((t) => <option key={t} value={t}>{human(t)}</option>)}</select></label>
            <label className={s.field}><span>Status</span><select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="performed">Done</option><option value="simulated">Simulated (test)</option><option value="not_performed">Not done</option></select></label>
            {status !== "not_performed" && <>
              <label className={s.field}><span>When (UTC)</span><input type="datetime-local" step="1" value={performedAt} onChange={(e) => setPerformedAt(e.target.value)} /></label>
              <label className={s.field}><span>Who did it</span><input value={performer} onChange={(e) => setPerformer(e.target.value)} placeholder="Field technician" /></label>
            </>}
          </div>
          <button className={s.button} disabled={busy || !description.trim() || (status !== "not_performed" && (!performer.trim() || !performedAt))} style={{ marginTop: 10 }}>Record action</button>
        </form>
      )}

      {step === "outcome" && (
        <form onSubmit={(e) => { e.preventDefault(); save("outcome", { action_id: actionFor.id, result, observed_from: toIso(from) || actionFor.payload.performed_at, observed_to: toIso(to), verification: "unverified", observations }); }}>
          <p className={s.note} style={{ marginTop: 0 }}>What happened after: <b>{actionFor.payload.description}</b></p>
          <div className={s.formGrid}>
            <label className={s.field}><span>Result</span><select value={result} onChange={(e) => setResult(e.target.value)}>{RESULTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className={s.field}><span>Watched from (UTC)</span><input type="datetime-local" step="1" value={from || (actionFor.payload.performed_at || "").slice(0, 19)} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className={s.field}><span>Watched until (UTC)</span><input type="datetime-local" step="1" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          </div>
          <label className={s.field}><span>What you saw</span><input value={observations} onChange={(e) => setObservations(e.target.value)} placeholder="Operator reports normal response for two hours" /></label>
          <button className={s.button} disabled={busy || !observations.trim()} style={{ marginTop: 10 }}>Record outcome</button>
        </form>
      )}
      {error && <p className={s.warning}>{error}</p>}
    </section>
  );
}

const CONFIRMED_BY: [string, string][] = [["inspection", "Inspection of the part"], ["reproduction", "Reproduced on bench, replay or simulation"],
  ["fix_resolved_it", "The fix resolved it"], ["evidence_in_case", "The evidence in this case"], ["engineer_judgement", "Engineer's judgement"]];
const CONFIRMING = ["inspection", "reproduction", "fix_resolved_it"];
const FIX_HELD: [string, string][] = [["held", "Held"], ["partly", "Partly"], ["not_held", "Did not hold"], ["not_yet_known", "Not yet known"]];
const INVESTIGATION: [string, string][] = [["cause_confirmed", "Cause confirmed"], ["cause_probable", "Cause probable, not confirmed"],
  ["cause_unknown", "Cause still unknown"], ["not_reproduced", "Could not reproduce"]];
const RESOLUTION: [string, string][] = [["resolved", "Resolved"], ["temporary_workaround", "Temporary workaround in place"], ["not_yet_repaired", "Not yet repaired"],
  ["not_resolved", "Not resolved"], ["machine_retired", "Machine retired"], ["handed_over", "Handed over to another team"]];
const EVIDENCE_STRENGTH: Record<string, string> = { direct: "Direct evidence", indirect: "Indirect evidence", circumstantial: "Circumstantial evidence", judgement: "Engineer's judgement" };
const STRENGTH_OF: Record<string, string> = { inspection: "direct", reproduction: "direct", fix_resolved_it: "indirect", evidence_in_case: "circumstantial", engineer_judgement: "judgement" };
const label = (pairs: [string, string][], v?: string | null) => pairs.find(([k]) => k === v)?.[1] ?? human(v);
const hasCause = (p: Any) => p && p.cause && ["cause_confirmed", "cause_probable"].includes(p.investigation_status ?? "cause_confirmed");
const causeTitle = (p: Any) => p.investigation_status === "cause_probable" ? "Probable cause" : p.investigation_status === "cause_confirmed" || !p.investigation_status ? "Confirmed cause" : label(INVESTIGATION, p.investigation_status);

function StrengthBadge({ p }: { p: Any }) {
  const st = p.evidence_strength || STRENGTH_OF[p.confirmed_by];
  if (!st) return null;
  return <span className={`${s.strength} ${s["strength_" + st] || ""}`} title={`How the conclusion was reached: ${human(p.confirmed_by)}`}>{EVIDENCE_STRENGTH[st] ?? human(st)}</span>;
}

function ClosureSummary({ p, actor, at }: { p: Any; actor?: string; at?: string }) {
  return <>
    <div className={s.dim}>
      {p.confirmed_by ? <>Basis: {label(CONFIRMED_BY, p.confirmed_by)}{p.evidence_note ? ` (${p.evidence_note})` : ""} <StrengthBadge p={p} /></> : null}
    </div>
    <div className={s.dim}>
      Machine: <b>{label(RESOLUTION, p.resolution_status ?? (p.fix ? "resolved" : null))}</b>{p.handed_over_to ? ` to ${p.handed_over_to}` : ""}
      {p.fix ? ` · Fix: ${p.fix} (${p.fix_held === "not_yet_known" ? "not yet known if it held" : human(p.fix_held)})` : ""}
      {p.decisive_check ? ` · Settled by ${p.decisive_check}` : ""}
      {p.hours_to_cause != null ? ` · ${p.hours_to_cause} h to the cause` : ""}
    </div>
    {p.would_have_saved_time && <div className={s.dim}>Would have saved time: {p.would_have_saved_time}</div>}
    {actor && <div className={s.note} style={{ margin: 0 }}>{actor} · {(at || "").slice(0, 16).replace("T", " ")}</div>}
  </>;
}

function Closure({ ledger, run, caseId, onSaved }: { ledger: Any[]; run: Any; caseId: string; onSaved: () => Promise<void> }) {
  const closures = ledger.filter((e) => e.kind === "case_closure");
  const latest = closures[closures.length - 1];
  const evidenceKinds = ["check_execution", "reproduction_attempt", "outcome", "hypothesis_status", "outcome_review"];
  const evidenceEntries = ledger.filter((e) => evidenceKinds.includes(e.kind));
  const domains: string[] = Object.keys(run.domains || {});
  const checks: Any[] = run.checks || [];
  const [open, setOpen] = useState(!latest);
  const [f, setF] = useState<Any>({ investigation_status: "cause_confirmed", cause_domain: domains[0] || "vehicle_interface", cause: "", confirmed_by: "inspection",
    evidence_entries: [] as string[], evidence_note: "", resolution_status: "resolved", handed_over_to: "",
    fix: "", fix_held: "not_yet_known", decisive_check: "", hours_to_cause: "", would_have_saved_time: "", applies_when: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string) => (e: Any) => setF({ ...f, [k]: e.target.value });
  const withCause = ["cause_confirmed", "cause_probable"].includes(f.investigation_status);
  const methods = f.investigation_status === "cause_confirmed" ? CONFIRMED_BY.filter(([v]) => CONFIRMING.includes(v)) : CONFIRMED_BY;
  const setStatus = (e: Any) => {
    const v = e.target.value;
    setF({ ...f, investigation_status: v, confirmed_by: v === "cause_confirmed" && !CONFIRMING.includes(f.confirmed_by) ? "inspection" : f.confirmed_by });
  };
  const needsProof = withCause && CONFIRMING.includes(f.confirmed_by) && !f.evidence_entries.length && !f.evidence_note.trim();
  const incomplete = (withCause && !f.cause.trim()) || needsProof || (f.resolution_status === "handed_over" && !f.handed_over_to.trim());

  async function save(e: Any) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const hours = f.hours_to_cause === "" ? null : Number(f.hours_to_cause);
      const cause = withCause ? { cause_domain: f.cause_domain, cause: f.cause, confirmed_by: f.confirmed_by } : {};
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "case_closure", actor: ENGINEER, source: "field case page",
        payload: { run_id: run.id, investigation_status: f.investigation_status, ...cause, evidence_entries: f.evidence_entries,
          evidence_note: f.evidence_note || null, resolution_status: f.resolution_status, handed_over_to: f.handed_over_to || null,
          fix: f.fix || null, fix_held: f.fix ? f.fix_held : null, decisive_check: f.decisive_check || null, applies_when: f.applies_when || null,
          hours_to_cause: hours, would_have_saved_time: f.would_have_saved_time || null } }) });
      setOpen(false);
      await onSaved();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    setBusy(false);
  }

  return (
    <section id="closure" className={s.panel}>
      <h2>Close the case</h2>
      <p className={s.lede}>Two questions: what caused it, and is the machine fixed?</p>
      {closures.length > 0 && (
        <ol className={s.ledger} style={{ marginBottom: 14 }}>
          {[...closures].reverse().map((c, i) => (
            <li key={c.id} style={{ display: "block" }}>
              <div>
                {i > 0 && <span className={s.dim}>Earlier conclusion (superseded) · </span>}
                <b>{causeTitle(c.payload)}</b>{hasCause(c.payload) ? <>: {dl(c.payload.cause_domain)} — {c.payload.cause}</> : null}
              </div>
              <ClosureSummary p={c.payload} actor={c.actor} at={c.recorded_at} />
            </li>))}
        </ol>
      )}
      {latest && !open && <button className={s.quiet} onClick={() => setOpen(true)}>Revise the conclusion</button>}
      {open && (
        <form onSubmit={save}>
          <fieldset className={s.fieldset}>
            <legend>The cause</legend>
            <div className={s.formGrid}>
              <label className={s.field}><span>Cause</span>
                <select value={f.investigation_status} onChange={setStatus}>{INVESTIGATION.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
              {withCause && <label className={s.field}><span>Which part</span>
                <select value={f.cause_domain} onChange={set("cause_domain")}>{domains.map((d) => <option key={d} value={d}>{dl(d)}</option>)}</select></label>}
              {withCause && <label className={s.field}><span>How you know</span>
                <select value={f.confirmed_by} onChange={set("confirmed_by")}>{methods.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
              {withCause && <div className={s.field}><span>Evidence strength</span><div style={{ paddingTop: 6 }}><StrengthBadge p={{ confirmed_by: f.confirmed_by }} /></div></div>}
            </div>
            {f.investigation_status === "cause_confirmed" && <p className={s.note}>“Confirmed” needs an inspection, a reproduction or a fix that worked. Otherwise choose “probable”.</p>}
            {withCause && <label className={s.field} style={{ marginTop: 12 }}><span>What it was</span>
              <input value={f.cause} onChange={set("cause")} placeholder="Left front knee drive: encoder cable chafed at the hip, intermittent position signal" /></label>}
            {withCause && evidenceEntries.length > 0 && (
              <label className={s.field} style={{ marginTop: 12 }}><span>Records that show it (optional)</span>
                <select multiple value={f.evidence_entries} onChange={(e) => setF({ ...f, evidence_entries: [...e.target.selectedOptions].map((o) => o.value) })} style={{ minHeight: 70 }}>
                  {evidenceEntries.map((e) => <option key={e.id} value={e.id}>{human(e.kind)} · {e.recorded_at.slice(0, 16).replace("T", " ")}</option>)}
                </select></label>)}
            <label className={s.field} style={{ marginTop: 12 }}><span>{withCause ? `Proof${needsProof ? " (needed)" : " (optional)"}` : "What was tried (optional)"}</span>
              <input value={f.evidence_note} onChange={set("evidence_note")} placeholder={withCause ? "Technician report 2026-03-05: cable insulation worn through, photo attached in the work order" : "Bench replay of the recorded commands did not show the fault"} /></label>
            <label className={s.field} style={{ marginTop: 12 }}><span>Which check settled it (optional)</span>
              <select value={f.decisive_check} onChange={set("decisive_check")}>
                <option value="">None of the listed checks</option>
                {checks.map((c: Any) => <option key={c.check_id} value={c.check_id}>{plain(c.question).slice(0, 80)}</option>)}
              </select></label>
          </fieldset>
          <fieldset className={s.fieldset}>
            <legend>The machine</legend>
            <div className={s.formGrid}>
              <label className={s.field}><span>Machine now</span>
                <select value={f.resolution_status} onChange={set("resolution_status")}>{RESOLUTION.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
              {f.resolution_status === "handed_over" && <label className={s.field}><span>Handed over to</span><input value={f.handed_over_to} onChange={set("handed_over_to")} placeholder="Drive supplier field service" /></label>}
              <label className={s.field}><span>What was done (optional)</span><input value={f.fix} onChange={set("fix")} placeholder="Cable replaced and re-routed" /></label>
              {f.fix && <label className={s.field}><span>Did it hold?</span>
                <select value={f.fix_held} onChange={set("fix_held")}>{FIX_HELD.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
            </div>
          </fieldset>
          <div className={s.formGrid}>
            <label className={s.field}><span>Hours to find the cause (optional)</span><input type="number" min="0" step="0.5" value={f.hours_to_cause} onChange={set("hours_to_cause")} /></label>
          </div>
          <label className={s.field} style={{ marginTop: 12 }}><span>What would have saved time (optional)</span>
            <textarea rows={2} value={f.would_have_saved_time} onChange={set("would_have_saved_time")} placeholder="Reading the drive's error register first; it showed the encoder fault before anyone went on site" /></label>
          <label className={s.field} style={{ marginTop: 12 }}><span>When does this lesson apply? (optional)</span>
            <input value={f.applies_when} onChange={set("applies_when")} placeholder="Same firmware family, and the drive error register shows the encoder fault" /></label>
          {error && <p className={s.warning}>{error}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className={s.button} disabled={busy || incomplete}>{busy ? "Recording…" : latest ? "Record revised conclusion" : "Close the case"}</button>
            {latest && <button type="button" className={s.quiet} onClick={() => setOpen(false)}>Cancel</button>}
          </div>
          <p className={s.note}>Saved to the case record. A revision keeps the old one.</p>
        </form>
      )}
    </section>
  );
}

const sameKey = (a: Any, b: Any) => a && b && ["rule", "signal", "scope", "value"].every((k) => (a[k] ?? null) === (b[k] ?? null));

function proposalState(ledger: Any[], proposalId: string): Any {
  const reviews = ledger.filter((e) => e.kind === "normal_review" && e.payload.proposal_id === proposalId);
  return reviews[reviews.length - 1] || null;
}

function Findings({ run, ledger, caseId, onSaved }: { run: Any; ledger: Any[]; caseId: string; onSaved: () => Promise<void> }) {
  const rows: Any[] = Object.entries(run.domains || {}).flatMap(([d, a]: [string, Any]) => (a.observations || []).map((o: Any) => ({ ...o, domain: d })));
  const anomalies = rows.filter((o) => o.kind === "anomaly" || o.kind === "delay");
  const normal = rows.filter((o) => o.kind === "normal" || o.kind === "routine" || o.kind === "explained");
  const proposals = ledger.filter((e) => e.kind === "normal_proposal");
  const profile = run.machine_profile || {};
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [form, setForm] = useState<Any>({ reason: "", scope: "machine", name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function propose(o: Any) {
    setBusy(true); setError("");
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "normal_proposal", actor: form.name.trim(), source: "field case page",
        payload: { run_id: run.id, observation_key: { rule: o.rule, ...(o.key || {}) }, observation_text: o.text, scope: form.scope,
          machine_class: form.scope === "machine_type" ? profile.id : null, reason: form.reason } }) });
      setOpenKey(null); setForm({ ...form, reason: "" });
      await onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  }

  return (
    <section id="findings" className={s.panel}>
      <h2>All findings</h2>
      <p className={s.lede}>Normal on this machine? Mark it. A second person approves.</p>
      {anomalies.length === 0 && <p className={s.empty}>Nothing flagged.</p>}
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
        {anomalies.map((o, i) => {
          const k = `${o.domain}-${i}`;
          const mine = proposals.filter((p) => sameKey(p.payload.observation_key, { rule: o.rule, ...(o.key || {}) }));
          const last = mine[mine.length - 1];
          const review = last && proposalState(ledger, last.id);
          return (
            <li key={k} className={s.nextBox} style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", gap: 10, justifyContent: "space-between", alignItems: "start", flexWrap: "wrap" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <b>{dl(o.domain)}</b>
                  <div title={o.text}>{plain(o.text)}</div>
                  {o.attribution_eligible === false && <p className={s.by} style={{ margin: "4px 0 0" }}>
                    <span className={s.pill}>Conditional</span> Measured between two clocks whose offset is not verified. It calls for a clock check; it can&apos;t point at a part on its own.</p>}
                  {(() => { const spec = plotFor(run, o.rule, o.text); return spec ? <Waveform caseId={caseId} spec={spec} load={call} /> : null; })()}
                </div>
                {!o.key ? null : last ? (
                  <span className={`${s.pill} ${review?.payload.verdict === "approved" ? s.verified : ""}`}>
                    {review ? (review.payload.verdict === "approved" ? "Approved as normal" : "Rejected") : "Waiting for review"}
                  </span>
                ) : <button className={s.quiet} onClick={() => setOpenKey(openKey === k ? null : k)}>Mark as normal</button>}
              </div>
              {openKey === k && (
                <form onSubmit={(e) => { e.preventDefault(); propose(o); }} style={{ display: "grid", gap: 8 }}>
                  <div className={s.formGrid} style={{ marginTop: 0 }}>
                    <label className={s.field}><span>Applies to</span>
                      <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                        <option value="machine">This machine ({run.machine_id})</option>
                        {profile.id && profile.id !== "generic" && <option value="machine_type">Every {profile.name}</option>}
                      </select></label>
                    <label className={s.field}><span>Your name</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Who is proposing this" /></label>
                  </div>
                  <label className={s.field}><span>Why it's normal</span><input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Short pitch spikes on every step over rough ground; seen on all known-good walks" /></label>
                  {error && <p className={s.warning}>{error}</p>}
                  <div><button className={s.button} disabled={busy || !form.reason.trim() || !form.name.trim()}>{busy ? "Recording…" : "Propose as normal"}</button></div>
                </form>
              )}
            </li>);
        })}
      </ul>
      {normal.length > 0 && (
        <details className={s.done} style={{ marginTop: 12 }}>
          <summary>{normal.length} treated as normal</summary>
          <ul>{normal.map((o, i) => <li key={i}><b>{dl(o.domain)}</b> — {plain(o.text)}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function NormalReviews({ ledger, caseId, onSaved }: { ledger: Any[]; caseId: string; onSaved: () => Promise<void> }) {
  const proposals = ledger.filter((e) => e.kind === "normal_proposal");
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!proposals.length) return null;

  async function review(proposalId: string, verdict: string) {
    setBusy(true); setError("");
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "normal_review", actor: name.trim(), source: "field case page",
        payload: { proposal_id: proposalId, verdict, reason } }) });
      setReason("");
      await onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  }

  return (
    <section id="normals" className={s.panel}>
      <h2>Marked as normal</h2>
      <p className={s.lede}>A second person approves or rejects.</p>
      <ol className={s.ledger}>
        {proposals.map((p) => {
          const r = proposalState(ledger, p.id);
          return (
            <li key={p.id} style={{ display: "block" }}>
              <div><b>{p.payload.observation_text}</b></div>
              <div className={s.dim}>Proposed by {p.actor} for {p.payload.scope === "machine_type" ? `every ${p.payload.machine_class.replace(/_/g, " ")}` : "this machine"}: {p.payload.reason}</div>
              {r ? <div><span className={`${s.pill} ${r.payload.verdict === "approved" ? s.verified : ""}`}>{r.payload.verdict}</span> <span className={s.dim}>by {r.actor}: {r.payload.reason}</span></div> : (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
                  <button className={s.button} disabled={busy || !name.trim() || !reason.trim()} onClick={() => review(p.id, "approved")}>Approve</button>
                  <button className={s.quiet} disabled={busy || !name.trim() || !reason.trim()} onClick={() => review(p.id, "rejected")}>Reject</button>
                </div>)}
            </li>);
        })}
      </ol>
      {proposals.some((p) => !proposalState(ledger, p.id)) && (
        <div className={s.formGrid}>
          <label className={s.field}><span>Reviewer (a different person)</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" /></label>
          <label className={s.field}><span>Reason</span><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Matches what we see on every known-good run" /></label>
        </div>)}
      {error && <p className={s.warning}>{error}</p>}
      
    </section>
  );
}

function Record({ ledger, verification, caseId }: { ledger: Any[]; verification: Any; caseId: string }) {
  const [showInternal, setShowInternal] = useState(false);
  const internal = ledger.filter(isInternal).length;
  return (
    <section id="record" className={s.panel}>
      <h2>Record</h2>
      <p className={s.lede}>Entries can be added, never changed.</p>
      <p style={{ margin: "0 0 12px" }}>
        <span className={`${s.pill} ${verification?.ledger_chain.valid ? s.verified : s.fault}`}>{verification?.ledger_chain.valid ? "Chain intact" : "Chain broken"}</span>{" "}
        <a href={`${API}/engine/cases/${caseId}/episodes`} target="_blank" rel="noreferrer" style={{ color: "var(--sr-thread)" }}>Export episodes (JSON)</a>
      </p>
      <ol className={s.ledger}>
        {ledger.filter((e) => showInternal || !isInternal(e)).map((e) => (
          <li key={e.id} style={isInternal(e) ? { opacity: 0.6 } : undefined}>
            <time>{time(e.recorded_at, true).slice(0, 19)}</time>
            <span><b>{isInternal(e) ? "Veyra self-check (internal)" : e.kind === "next_action" ? `Veyra: ${{ plan: "plan", prepared: "request drafted", report: "readiness report" }[e.payload.stage as string] || e.payload.stage}` : KIND_LABEL[e.kind] ?? human(e.kind)}</b> · {e.actor}{e.kind === "triage" ? ` → ${e.payload.owner}` : e.kind === "check_review" || e.kind === "check_execution" ? ` · ${e.payload.check_id}` : e.kind === "request_sent" ? ` · ${human(e.payload.channel)} to ${e.payload.sent_to}` : e.kind === "hypothesis_status" ? ` · ${dl(e.payload.domain)} ${human(e.payload.status)}` : ""}</span>
            <code>{e.hash.slice(0, 10)}</code>
          </li>
        ))}
      </ol>
      {internal > 0 && <button className={s.link} onClick={() => setShowInternal(!showInternal)}>{showInternal ? "Hide" : "Show"} {internal} internal Veyra self-check{internal === 1 ? "" : "s"} (audit only, not decisions)</button>}
    </section>
  );
}

const PREMISE: Record<string, [string, string]> = { supported: ["Data agrees", "thread"], not_supported: ["Data disagrees", "anomaly"], cannot_tell: ["Data can't tell", ""] };

function Computations({ items }: { items: Any[] }) {
  if (!items?.length) return null;
  return (
    <details>
      <summary className={s.note} style={{ cursor: "pointer" }}>How it was worked out ({items.length})</summary>
      <div style={{ display: "grid", gap: 10, marginTop: 8 }}>{items.map((c: Any, i: number) => (
        <div key={i} style={{ borderLeft: "2px solid var(--sr-line, #d9dce8)", paddingLeft: 10 }}>
          <p style={{ margin: 0, fontSize: 13 }}><b>{c.kind === "python" ? "Own calculation (Python)" : c.kind === "team_method" ? `Team method: ${c.name}` : c.name.replace(/_/g, " ")}</b>
            {c.ok === false ? <span className={s.note}> · did not run</span> : null}</p>
          <p className={s.note} style={{ margin: "2px 0" }}>{(c.provenance?.inputs || []).map((x: Any) => `${x.signal}${x.scope ? ` (${x.scope})` : ""} · ${x.unit ?? "no unit"} · ${x.clock} · ${x.rows} readings · ${x.first_source} … ${x.last_source}`).join(" | ")}</p>
          {c.code && <pre className={s.mono} style={{ whiteSpace: "pre-wrap", fontSize: 12, margin: "4px 0", maxHeight: 220, overflow: "auto" }}>{c.code}</pre>}
          <p className={s.note} style={{ margin: 0 }}>Result: <span className={s.mono}>{c.result_preview?.slice(0, 400)}</span></p>
          {c.provenance?.isolation && <p className={s.note} style={{ margin: 0 }}>{c.provenance.isolation} · code {c.provenance.code_sha256?.slice(0, 10)} · inputs {c.provenance.inputs_sha256?.slice(0, 10)}</p>}
        </div>))}</div>
    </details>
  );
}

function AskAgent({ asked, onAsk, busy, cite }: { asked: Any[]; onAsk: (q: string) => void; busy: boolean; cite: (e: Any, ids?: string[]) => string }) {
  const [q, setQ] = useState("");
  return (
    <div style={{ display: "grid", gap: 12, marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--sr-line, #e3e5ee)" }}>
      <h3 style={{ fontSize: 15, margin: 0 }}>Ask about this case</h3>
      <p className={s.note} style={{ margin: 0 }}>It checks your question against the data, then works out the answer.</p>
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) { onAsk(q.trim()); setQ(""); } }} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={600} disabled={busy} aria-label="Question for the agent"
          placeholder="e.g. Did the torque drop before the leg stopped following its command?" style={{ flex: "1 1 320px", minWidth: 0, padding: "9px 12px", borderRadius: 10, border: "1px solid var(--sr-line, #d9dce8)", font: "inherit" }} />
        <button className={s.button} type="submit" disabled={busy || !q.trim()}>{busy ? "Working… ~1 min" : "Ask"}</button>
      </form>
      {asked.slice().reverse().map((e: Any) => {
        const r = e.payload.report || {};
        const [label, tone] = PREMISE[r.premise?.status] || ["", ""];
        return (
          <div key={e.id} style={{ display: "grid", gap: 6 }}>
            <p style={{ margin: 0, fontWeight: 600 }}>“{e.payload.question}”</p>
            {r.premise && <p style={{ margin: 0 }}><span className={`${s.pill} ${tone === "thread" ? s.thread : tone === "anomaly" ? s.anomaly : ""}`}>{label}</span></p>}
            {r.answer && <p style={{ margin: 0, maxWidth: "65ch" }}>{r.answer.text}</p>}
            {r.premise?.explanation && r.premise.explanation !== r.answer?.text && <p className={s.dim} style={{ margin: 0, maxWidth: "65ch" }}>{r.premise.explanation}</p>}
            <Computations items={e.payload.computations} />
            <p className={s.note} style={{ margin: 0 }}>Sources: {cite(e, [...(r.answer?.evidence || []), ...(r.premise?.evidence || [])].filter((x: string, i: number, a: string[]) => a.indexOf(x) === i))}{e.payload.citations_valid ? "" : " · some claims uncited"}</p>
          </div>);
      })}
    </div>
  );
}

function AgentPanel({ ledger, run, info, busy, error, onRun }: { ledger: Any[]; run: Any; info: Any; busy: boolean; error: string; onRun: (question?: string) => void }) {
  const all = ledger.filter((e) => e.kind === "agent_report");
  const asked = all.filter((e) => e.payload.question);
  const reports = all.filter((e) => !e.payload.question);
  const citeIn = (e: Any, ids: string[] = []) => { const m: Record<string, Any> = Object.fromEntries((e.payload.tool_calls || []).map((c: Any) => [c.id, c])); return ids.map((id) => m[id]?.tool?.replace(/_/g, " ") ?? id).join(", "); };
  const latest = reports[reports.length - 1];
  const r = latest?.payload.report;
  const calls: Record<string, Any> = Object.fromEntries((latest?.payload.tool_calls || []).map((c: Any) => [c.id, c]));
  const cite = (ids: string[] = []) => ids.map((id) => calls[id]?.tool?.replace(/_/g, " ") ?? id).join(", ");
  const stale = latest && latest.payload.run_id !== run.id;
  return (
    <section id="agent" className={s.panel}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", flexWrap: "wrap" }}>
        <div>
          <h2>AI agent</h2>
          <p className={s.lede} style={{ marginBottom: 8 }}>Reads the data and suggests what to check. Every claim links to its source.</p>
        </div>
        {info?.configured !== false && <button className={s.button} onClick={() => onRun()} disabled={busy}>{busy ? "Working… ~1 min" : latest ? "Run again" : "Run AI agent"}</button>}
      </div>
      {info && !info.configured && <p className={s.note} style={{ marginTop: 0 }}>Runs where an AI model is configured. Here you can read past reports{latest ? "" : " (the ALFA flight case has one)"}.</p>}
      {error && <p className={s.warning} style={{ marginTop: 0 }}>{error}</p>}
      {!latest && !error && info?.configured !== false && <p className={s.empty}>No report yet.</p>}
      {latest && r && (() => {
        const lead = (r.hypotheses || []).find((h: Any) => h.status === "leading");
        const sentences = (t: string, n: number) => plain(t).split(/(?<=[.!?])\s+(?=[A-Z])/).slice(0, n).join(" ");
        const h3 = { fontSize: 13, fontWeight: 600, margin: "0 0 6px", color: "var(--sr-ink-2)" } as const;
        return (
        <div style={{ display: "grid", gap: 14 }}>
          <p style={{ margin: 0, fontSize: 15, maxWidth: "65ch" }}>{sentences(r.summary, 2)}</p>
          {lead && <div><h3 style={h3}>The agent's leading hypothesis (not confirmed)</h3><p style={{ margin: 0, maxWidth: "65ch" }}>{sentences(lead.statement, 1)}</p></div>}
          {(r.proposed_checks || []).length > 0 && (
            <div>
              <h3 style={h3}>Check next (you decide)</h3>
              <ol style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>{r.proposed_checks.slice(0, 3).map((c: Any, i: number) => <li key={i} title={c.why}>{sentences(c.action, 1)}</li>)}</ol>
              {latest.payload.first_check_differs && <p className={s.dim} style={{ margin: "6px 0 0", fontSize: 13 }}>
                The agent ranks a different check first than Veyra's plan ({latest.payload.plan_first}). Its reason: {sentences(r.proposed_checks[0]?.why || "not given", 1)} Veyra's own check of the plan is independent of the agent.</p>}
            </div>
          )}
          <details className={s.how}>
            <summary>Full report</summary>
            <div style={{ display: "grid", gap: 12, fontSize: 13, color: "var(--sr-ink-2)" }}>
              <div><h3 style={h3}>What the data shows</h3>
                <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>{(r.observations || []).map((o: Any, i: number) => <li key={i}>{plain(o.text)} <span className={s.dim}>· {cite(o.evidence)}</span></li>)}</ul></div>
              <div><h3 style={h3}>Possible causes</h3>
                <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 6 }}>{(r.hypotheses || []).map((h: Any, i: number) => (
                  <li key={i}><span className={`${s.pill} ${h.status === "leading" ? s.thread : h.status === "weakened" ? "" : s.anomaly}`}>{h.status === "leading" ? "most likely" : h.status === "weakened" ? "less likely" : "open"}</span> {plain(h.statement)}</li>))}</ul></div>
              {(r.proposed_checks || []).length > 3 && <div><h3 style={h3}>More checks</h3>
                <ul style={{ margin: 0, paddingLeft: 18 }}>{r.proposed_checks.slice(3).map((c: Any, i: number) => <li key={i}>{plain(c.action)}</li>)}</ul></div>}
              {(r.questions_for_engineers || []).length > 0 && <div><h3 style={h3}>Ask the team</h3>
                <ul style={{ margin: 0, paddingLeft: 18 }}>{r.questions_for_engineers.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></div>}
              <div><h3 style={h3}>Still unknown</h3><p style={{ margin: 0 }}>{plain(r.uncertainty)}</p></div>
              <Computations items={latest.payload.computations} />
            </div>
          </details>
          <p className={s.note} style={{ margin: 0 }}>
            {latest.payload.citations_valid ? "Every claim cited" : "Some claims uncited"}{stale ? " · written for an earlier run" : ""} · AI suggestions, not confirmed causes
          </p>
        </div>);
      })()}
      {info?.configured !== false && <AskAgent asked={asked} onAsk={(q) => onRun(q)} busy={busy} cite={citeIn} />}
      {info?.configured === false && asked.length > 0 && <AskAgent asked={asked} onAsk={() => {}} busy={true} cite={citeIn} />}
    </section>
  );
}

const nextStep = (ledger: Any[], runId: string) => {
  const mine = ledger.filter((e: Any) => e.kind === "next_action" && e.payload.run_id === runId);
  return mine.some((e: Any) => e.payload.stage === "report") ? 3 : mine.some((e: Any) => e.payload.stage === "plan") ? 2 : 1;
};
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function SiteResult({ check, runId, caseId, onSaved }: { check: Any; runId: string; caseId: string; onSaved: () => Promise<void> }) {
  const [f, setF] = useState<Any>({ outcome: "", observations: "", performer: "", evidence_note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string) => (e: Any) => setF({ ...f, [k]: e.target.value });
  async function save(e: Any) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "check_execution", actor: f.performer.trim() || ENGINEER, source: "next action: site result",
        payload: { run_id: runId, check_id: check.check_id, status: "performed", executed_at: nowIso(), ...f, outcome: f.outcome === "other" ? null : f.outcome } }) });
      await onSaved();
    } catch (err: Any) { setError(err.message); }
    setBusy(false);
  }
  return (
    <form onSubmit={save} style={{ display: "grid", gap: 8, marginTop: 10 }}>
      <div className={s.formGrid} style={{ marginTop: 0 }}>
        <label className={s.field}><span>What the site found</span>
          <select value={f.outcome} onChange={set("outcome")}><option value="">Choose…</option>{check.outcomes.map((o: Any) => <option key={o.key} value={o.key}>{o.label}</option>)}<option value="other">Something else (describe below)</option></select></label>
        <label className={s.field}><span>Who did it</span><input value={f.performer} onChange={set("performer")} placeholder="Site supervisor" /></label>
      </div>
      <label className={s.field}><span>What they saw</span><input value={f.observations} onChange={set("observations")} placeholder="What the log shows around the problem, with times" /></label>
      <label className={s.field}><span>Where the record is</span><input value={f.evidence_note} onChange={set("evidence_note")} placeholder="Controller log export, ticket 43" /></label>
      {error && <p className={s.warning}>{error}</p>}
      <div><button className={s.button} disabled={busy || !f.outcome || !f.observations.trim() || !f.performer.trim() || !f.evidence_note.trim()}>{busy ? "Saving…" : "Record the site's answer"}</button></div>
    </form>
  );
}

function Approve({ check, runId, caseId, onSaved, others, me }: { check: Any; runId: string; caseId: string; onSaved: () => Promise<void>; others: string; me?: string }) {
  const [name, setName] = useState(me || "");
  const [reason, setReason] = useState(`Informs ${others}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function approve(e: Any) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "check_review", actor: name.trim(), source: "next action: approval",
        payload: { run_id: runId, check_id: check.check_id, verdict: "accepted", reason } }) });
      await onSaved();
    } catch (err: Any) { setError(err.message); }
    setBusy(false);
  }
  return (
    <form onSubmit={approve} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end", marginTop: 10 }}>
      {me ? <span className={s.dim} style={{ alignSelf: "center" }}>Approving as {me}</span>
        : <label className={s.field} style={{ flex: "1 1 160px" }}><span>Approved by</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" /></label>}
      <label className={s.field} style={{ flex: "2 1 240px" }}><span>Why</span><input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <button className={s.button} disabled={busy || !name.trim() || !reason.trim()}>{busy ? "Saving…" : "Approve draft"}</button>
      <span className={s.dim} style={{ fontSize: 13, alignSelf: "center" }}>Approving sends nothing.</span>
      {error && <p className={s.warning} style={{ flexBasis: "100%" }}>{error}</p>}
    </form>
  );
}

const ACTION_LABEL: Record<string, string> = { dispatch: "Send someone to the machine", replace_part: "Replace a part", remote_fix: "Fix remotely", close_case: "Close the case" };
const PURPOSE_LABEL: Record<string, string> = { diagnose: "to diagnose", repair: "to repair", safety_inspection: "for a safety inspection", restore_service: "to restore service" };
const VERDICT: Record<string, [string, string]> = { supported: ["supported", "verified"], contradicted: ["contradicted", "fault"], insufficient: ["not enough evidence", "anomaly"], not_assessed: ["not checked", ""] };
const GAP_STATE: Record<string, string> = { not_done: "not done", inaccessible: "needs permission", failed: "tried, no result", done: "done" };

function DecisionCheck({ caseId, me, onChanged }: { caseId: string; me?: string; onChanged: () => Promise<void> }) {
  const [sg, setSg] = useState<Any>(null);
  const [f, setF] = useState<Any>({ action: "dispatch", purpose: "repair", part: "", urgent: false, believe: false });
  const [touchedPart, setTouchedPart] = useState(false);
  const [relies, setRelies] = useState<Any[]>([]);
  const [res, setRes] = useState<Any>(null);
  const [labels, setLabels] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { call(`/engine/cases/${caseId}/decision-check`).then((d) => { setSg(d); if (d.suggested_hypothesis) setF((x: Any) => ({ ...x, part: d.suggested_hypothesis })); }).catch((e) => setError(e.message)); }, [caseId]);
  if (!sg?.available) return null;
  const partLabel = (p: string) => sg.parts.find((x: Any) => x.part === p)?.label || p;
  const label = () => `${ACTION_LABEL[f.action]} ${PURPOSE_LABEL[f.purpose]}${f.part ? ` (${partLabel(f.part)})` : ""}`;
  const suggested = !touchedPart && f.part && f.part === sg.suggested_hypothesis;

  async function runCheck(e: Any) {
    e.preventDefault(); setBusy(true); setError(""); setDone("");
    try {
      const body: Any = { action: f.action, purpose: f.purpose, urgent: f.urgent, target_part: f.part || null, actor: me || ENGINEER,
        hypothesis: f.believe && f.part ? { part: f.part, provenance: suggested ? "system_suggested_confirmed" : "engineer_stated" } : null,
        relies_on: relies.map((r) => ({ ...r, provenance: "engineer_stated" })) };
      setRes(await call(`/engine/cases/${caseId}/decision-check`, { method: "POST", body: JSON.stringify(body) }));
      setLabels((l) => (l.includes(label()) ? l : [...l, label()]));
      await onChanged();
    } catch (err: Any) { setError(err.message); }
    setBusy(false);
  }
  async function commit() {
    setBusy(true); setError("");
    try {
      const chosen = label();
      const options = labels.includes(chosen) ? labels : [...labels, chosen];
      await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "decision", actor: me || ENGINEER, source: "decision check",
        payload: { run_id: res.check.payload.run_id, options_considered: options, chosen_option: chosen, rationale: reason.trim() || "Recorded after the decision check",
          proposal_id: res.proposal.id, check_id: res.check.id, changed_from_proposal: options[0] !== chosen, proceed_reason: reason.trim() || null } }) });
      setDone(chosen); setRes(null); setReason("");
      await onChanged();
    } catch (err: Any) { setError(err.message); }
    setBusy(false);
  }
  const c = res?.check?.payload;
  const tone = c?.alert === "warning" ? s.dcWarn : c?.alert === "notice" ? s.dcNote : s.dcQuiet;
  return (
    <div className={s.dc}>
      <h3 className={s.stepTitle} style={{ marginTop: 0 }}>Your decision</h3>
      <form onSubmit={runCheck} className={s.dcForm}>
        <select aria-label="Action" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}>{sg.actions.map((a: string) => <option key={a} value={a}>{ACTION_LABEL[a]}</option>)}</select>
        <select aria-label="Purpose" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })}>{sg.purposes.map((a: string) => <option key={a} value={a}>{PURPOSE_LABEL[a]}</option>)}</select>
        <select aria-label="Part" value={f.part} onChange={(e) => { setTouchedPart(true); setF({ ...f, part: e.target.value }); }}>
          <option value="">no specific part</option>{sg.parts.map((x: Any) => <option key={x.part} value={x.part}>{x.label}</option>)}</select>
        {suggested && <span className={s.dim} style={{ fontSize: 12 }}>part suggested by Veyra</span>}
        <label className={s.dcTick}><input type="checkbox" checked={f.believe} disabled={!f.part} onChange={(e) => setF({ ...f, believe: e.target.checked })} /> I think this part is at fault</label>
        <label className={s.dcTick}><input type="checkbox" checked={f.urgent} onChange={(e) => setF({ ...f, urgent: e.target.checked })} /> Urgent / safety</label>
        <details className={s.how} style={{ flexBasis: "100%", margin: 0 }}>
          <summary>I'm relying on a finding (optional)</summary>
          <div style={{ display: "grid", gap: 4 }}>{sg.findings.slice(0, 12).map((o: Any, i: number) => {
            const on = relies.find((r) => r.text === o.text);
            return (
              <label key={i} className={s.dcTick} style={{ alignItems: "start" }}>
                <input type="checkbox" checked={!!on} onChange={(e) => setRelies(e.target.checked ? [...relies, { domain: o.domain, rule: o.rule, text: o.text, use: o.kind === "anomaly" || o.kind === "delay" ? "supports" : "rules_out", part: o.domain }] : relies.filter((r) => r.text !== o.text))} />
                <span>{plain(o.text)} <span className={s.dim}>· {o.kind === "anomaly" || o.kind === "delay" ? `points to ${partLabel(o.domain)}` : `rules out ${partLabel(o.domain)}`}</span></span>
              </label>);
          })}</div>
        </details>
        <button className={s.button} disabled={busy}>{busy ? "Checking…" : "Check this decision"}</button>
      </form>
      {error && <p className={s.warning}>{error}</p>}
      {done && <p style={{ margin: "10px 0 0" }}><span className={`${s.pill} ${s.verified}`}>Recorded</span> {done}. Record what happened later under Record.</p>}
      {c && <div className={`${s.dcCard} ${tone}`}>
        <p style={{ margin: 0, fontWeight: 600 }}>{c.alert === "quiet" ? `Nothing found that would change this. Coverage: ${c.coverage.status === "none_needed" ? "no deciding checks" : c.coverage.status}.` : plain(c.headline)}</p>
        {c.alert !== "quiet" && c.alert !== "not_assessed" && <>
          {c.claims.length > 0 && <div><p className={s.kicker}>What you stated</p>
            <ul className={s.dcList}>{c.claims.map((x: Any, i: number) => <li key={i}><span className={`${s.pill} ${s[VERDICT[x.verdict][1]] || ""}`}>{VERDICT[x.verdict][0]}</span> {plain(x.claim)}{x.provenance === "system_suggested_confirmed" ? <span className={s.dim}> (Veyra's suggestion, kept by you)</span> : null}<br /><span className={s.dim}>{plain(x.why)}</span></li>)}</ul></div>}
          {c.next_check && <div><p className={s.kicker}>Check this first</p>
            <p style={{ margin: 0, fontSize: 14 }}>{plain(c.next_check.question)} <span className={s.dim}>· {c.next_check.who} · {GAP_STATE[c.next_check.state]}</span><br /><span className={s.dim}>{plain(c.next_check.why)}</span></p>
            {c.gaps.length > 1 && <details className={s.how}><summary>{c.gaps.length - 1} more that could change it</summary>
              <ul className={s.dcList}>{c.gaps.filter((g: Any) => g.check_id !== c.next_check.check_id).map((g: Any) => <li key={g.check_id}>{plain(g.question)} <span className={s.dim}>· {GAP_STATE[g.state]}</span></li>)}</ul></details>}</div>}
          <details className={s.how}><summary>What the records show</summary><ul className={s.dcList}>{c.facts.map((x: Any, i: number) => <li key={i}>{plain(x.text)}</li>)}
            {c.prerequisites.map((x: string, i: number) => <li key={"p" + i}>{plain(x)}</li>)}</ul></details>
        </>}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 4 }}>
          {(c.alert === "warning" || c.alert === "notice" || c.alert === "not_assessed") && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason to go ahead (optional)" style={{ flex: "1 1 220px", minWidth: 0 }} />}
          <button className={s.button} disabled={busy} onClick={commit}>Go ahead</button>
          <button className={s.quiet} disabled={busy} onClick={() => setRes(null)}>Change the decision</button>
        </div>
        <p className={s.note} style={{ margin: 0 }}>Advisory. It doesn't say this is the best action and never blocks it.</p>
      </div>}
    </div>
  );
}

const CHANNELS = ["email", "phone", "chat", "ticket", "in_person", "other"];

function MarkSent({ ids, runId, to, busy, onSave }: { ids: string[]; runId: string; to: string; busy: boolean; onSave: (p: Any) => void }) {
  const [channel, setChannel] = useState("email");
  const [sentTo, setSentTo] = useState(to);
  const [ref, setRef] = useState("");
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSave({ run_id: runId, check_ids: ids, channel, sent_to: sentTo.trim(), sent_at: nowIso(), reference: ref.trim() || null }); }}
      style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
      <label className={s.field}><span>Sent by</span><select value={channel} onChange={(e) => setChannel(e.target.value)}>{CHANNELS.map((c) => <option key={c} value={c}>{human(c)}</option>)}</select></label>
      <label className={s.field} style={{ flex: "1 1 180px" }}><span>To</span><input value={sentTo} onChange={(e) => setSentTo(e.target.value)} /></label>
      <label className={s.field} style={{ flex: "1 1 140px" }}><span>Ticket or thread (optional)</span><input value={ref} onChange={(e) => setRef(e.target.value)} /></label>
      <button className={s.button} disabled={busy || !sentTo.trim()}>{busy ? "Saving…" : "Mark as sent"}</button>
    </form>
  );
}

function Assistant({ caseId, me, onChanged, onDecideOther, stamp }: { caseId: string; me?: string; onChanged: () => Promise<void>; onDecideOther: () => void; stamp: number }) {
  const [v, setV] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      let view = await call(`/engine/cases/${caseId}/autopilot`);
      // Only where this person can write (signed in, or holding the access code); visitors just see the current state.
      if (view.enabled && view.stage === "not_started" && (me || storedCode())) view = await call(`/engine/cases/${caseId}/autopilot`, { method: "POST", body: "{}" });
      setV(view);
    } catch (e: Any) { setError(e.message); }
  }, [caseId, me]);
  useEffect(() => { load(); }, [load, stamp]);
  const after = async () => { await onChanged(); };
  async function ledger(kind: string, payload: Any) {
    setBusy(true); setError("");
    try { await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind, actor: me || ENGINEER, source: "assistant", payload }) }); await after(); }
    catch (e: Any) { setError(e.message); }
    setBusy(false);
  }
  if (!v) return error ? <p className={s.warning}>{error}</p> : null;
  const req = v.request;
  const copy = async () => { try { await navigator.clipboard.writeText(`To: ${req.to}\nSubject: ${req.subject}\n\n${req.body}`); setCopied(true); } catch { setError("Copy failed: select the text and copy it"); } };
  const rec = v.recommendation;
  return (
    <div className={s.assistant}>
      <p className={s.kicker} style={{ margin: 0 }}>Veyra</p>
      <p className={s.say}>{plain(v.say)}</p>
      {v.already_done?.length > 0 && <p className={s.dim} style={{ margin: 0 }}>Already done by Veyra: {v.already_done.map((d: Any) => `${plain(d.procedure).replace(/^Inspect/, "inspected")}${d.count != null ? ` (${Number(d.count).toLocaleString("en")} records)` : ""}`).join("; ")}.</p>}
      {v.investigated?.effects?.length > 0 && <p className={s.dim} style={{ margin: 0 }}>Not counted as a cause: {v.investigated.effects.join(", ")}. It only shows how the machine moved, likely the symptom itself.</p>}
      {v.agent?.state === "running" && <p className={s.dim} style={{ margin: 0 }}>The AI agent is writing its report in the background…</p>}
      {v.agent?.state === "needs_authorization" && <p className={s.dim} style={{ margin: 0 }}>AI agent: {v.agent.detail}</p>}
      {v.investigated?.cannot_tell_apart?.length > 0 && v.stage !== "recommend" && v.stage !== "decided" &&
        <p className={s.dim} style={{ margin: 0 }}>Can't tell apart yet: {v.investigated.cannot_tell_apart.join(" vs ")}.</p>}

      {["awaiting_approval", "approved_not_sent", "awaiting_result"].includes(v.stage) && <>
        <ol className={s.stepper} aria-label="Request status">
          {[["awaiting_approval", "Draft"], ["approved_not_sent", "Approved"], ["awaiting_result", "Sent"], ["answered", "Answered"]].map(([k, label], i) => {
            const at = ["awaiting_approval", "approved_not_sent", "awaiting_result"].indexOf(v.stage);
            return <li key={k} className={i < at ? s.stepDone : i === at ? s.stepNow : ""} aria-current={i === at ? "step" : undefined}><span>{i + 1}</span>{label}</li>; })}
        </ol>
        <ul className={s.canList}>{(v.checks || []).map((c: Any) => <li key={c.check_id}>{plain(c.question)}{c.result ? " · answered" : ""}</li>)}</ul>
        <details className={s.how}>
          <summary>Why these checks</summary>
          {Object.entries(v.verification || {}).map(([cid, x]: [string, Any]) => <div key={cid} style={{ marginBottom: 8 }}>
            {(x.claims || []).map((c: Any, i: number) => <p key={i} style={{ margin: "2px 0" }}><b>{c.claim}.</b> <span className={s.dim}>{plain(c.why)}</span></p>)}
            {(x.permission || []).map((t: string, i: number) => <p key={i} className={s.dim} style={{ margin: 0 }}>{t}</p>)}
          </div>)}
          {(v.comparison || []).length > 0 && <table className={s.table} style={{ marginTop: 8 }}>
            <thead><tr><th>Check</th><th>Could change</th><th>Informs</th><th>Access</th></tr></thead>
            <tbody>{v.comparison.map((r: Any) => <tr key={r.check_id}>
              <td>{plain(r.question)}{r.tied_with?.length ? <span className={s.dim}> (tied)</span> : ""}</td>
              <td>{r.could_change_travel.length ? "whether someone travels" : r.could_change.length ? "which is more likely" : "nothing"}</td>
              <td>{r.informs.join(", ") || "—"}</td><td>{r.access}</td></tr>)}</tbody>
          </table>}
          <p className={s.dim} style={{ margin: "6px 0 0" }}>Ranked by: could it change whether someone travels, then which explanation is more likely, then how many open explanations it informs, then access and disruption. These are written rules, not yet validated against outcomes.</p>
        </details>
        <details className={s.how} open={v.stage !== "awaiting_result"}>
          <summary>The request ({req.to})</summary>
          <pre className={s.request}>{`Subject: ${req.subject}\n\n${req.body}`}</pre>
        </details>
        {v.stage === "awaiting_approval" && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className={s.button} disabled={busy} onClick={async () => { for (const c of v.checks) if (!c.approval) await ledger("check_review", { run_id: v.run_id, check_id: c.check_id, verdict: "accepted", reason: "Draft approved for sending" }); }}>{busy ? "Saving…" : "Approve draft"}</button>
            <button className={s.quiet} onClick={onDecideOther}>Do something else</button>
            <span className={s.dim} style={{ fontSize: 13 }}>Approving sends nothing. You send it, then mark it as sent.</span>
          </div>)}
        {v.stage === "approved_not_sent" && <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className={s.quiet} onClick={copy}>{copied ? "Copied" : "Copy the request"}</button>
            <span className={s.dim} style={{ fontSize: 13 }}>Approved by {v.check.approval.by}. Not sent yet.</span>
          </div>
          <MarkSent ids={v.checks.map((c: Any) => c.check_id)} runId={v.run_id} to={req.to} busy={busy} onSave={(p: Any) => ledger("request_sent", p)} />
        </>}
        {v.stage === "awaiting_result" && <>
          <p className={s.dim} style={{ margin: 0 }}>Sent {time(v.sent.sent_at)} by {v.sent.by} ({human(v.sent.channel)}{v.sent.reference ? `, ${v.sent.reference}` : ""}). Record each answer when it arrives.</p>
          {(v.checks || []).filter((c: Any) => !c.result).map((c: Any) => <div key={c.check_id}><p className={s.kicker} style={{ margin: "10px 0 0" }}>{plain(c.question)}</p>
            <SiteResult check={c} runId={v.run_id} caseId={caseId} onSaved={after} /></div>)}
        </>}
      </>}
      {v.stage === "weak_evidence" && <p className={s.dim} style={{ margin: 0 }}>Nothing drafted.</p>}
      {v.stage === "old_rules" && <div><button className={s.button} disabled={busy} onClick={async () => {
        setBusy(true); setError("");
        try { await call(`/engine/cases/${caseId}/runs`, { method: "POST", body: JSON.stringify({ actor: me || ENGINEER }) }); await after(); } catch (e: Any) { setError(e.message); }
        setBusy(false); }}>{busy ? "Investigating…" : "Investigate again"}</button></div>}

      {v.stage === "recommend" && <>
        {rec && <p style={{ margin: 0 }}><span className={`${s.pill} ${rec.alert === "quiet" ? s.verified : s.anomaly}`}>{rec.alert === "quiet" ? "checked" : "check flagged something"}</span> <span className={s.dim}>{plain(rec.headline)} Coverage: {rec.coverage === "none_needed" ? "no deciding checks left" : rec.coverage}.</span></p>}
        <div className={s.two}>
          <div><p className={s.kicker}>Can do now</p><ul className={s.canList}>{v.report.can_do_now.slice(0, 2).map((x: Any, i: number) => <li key={i}>{plain(x.text)}</li>)}</ul></div>
          <div><p className={s.kicker}>Can't do yet</p><ul className={s.cantList}>{v.report.cannot_do_yet.slice(0, 2).map((x: Any, i: number) => <li key={i}>{plain(x.text)}</li>)}</ul></div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {rec && <button className={s.button} disabled={busy} onClick={() => {
            const chosen = `${ACTION_LABEL[rec.action]}: ${plain(v.report.leading)}`;
            ledger("decision", { run_id: v.run_id, options_considered: [chosen, "Send someone to the machine"], chosen_option: chosen,
              rationale: "Veyra's recommendation, checked against the evidence", changed_from_proposal: false,
              ...(rec.legacy ? { proposal_id: rec.proposal_id, check_id: rec.check_entry } : { veyra_check_id: rec.check_entry }) });
          }}>{busy ? "Saving…" : "Record this decision"}</button>}
          <button className={s.quiet} onClick={onDecideOther}>Decide differently</button>
        </div>
      </>}
      {v.stage === "decided" && <p style={{ margin: 0 }}><span className={`${s.pill} ${s.verified}`}>Decision recorded</span> <span className={s.dim}>Record what happened afterwards under Record.</span></p>}
      {error && <p className={s.warning}>{error}</p>}
    </div>
  );
}

function NextAction({ caseId, data, onChanged, me, onEvidence }: { caseId: string; data: Any; onChanged: () => Promise<void>; me?: string; onEvidence: () => void }) {
  const [st, setSt] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => { try { setSt(await call(`/engine/cases/${caseId}/next-action`)); } catch (e: Any) { setError(e.message); } }, [caseId]);
  useEffect(() => { refresh(); }, [refresh, data]);
  const after = async () => { await onChanged(); await refresh(); };
  const [qa, setQa] = useState<Any>(null);
  useEffect(() => { call(`/engine/cases/${caseId}/questions`).then(setQa).catch(() => setQa(null)); }, [caseId, data]);
  async function post(path: string) {
    setBusy(true); setError("");
    try { setSt(await call(`/engine/cases/${caseId}/next-action/${path}`, { method: "POST", body: "{}" })); await onChanged(); } catch (e: Any) { setError(e.message); }
    setBusy(false);
  }
  if (!st) return <section className={s.panel}><p className={s.empty}>{error || "Loading…"}</p></section>;
  if (st.step === 0) return <section className={s.panel}><p className={s.empty}>{st.note}</p></section>;
  const a = st.assessment;
  const apart = a.cannot_tell_apart.map((c: Any) => c.label).join(" vs ");
  const r = st.report;
  const stepNames = ["Investigate", "Next check", "Decide"];
  return (
    <section className={s.panel} id="next">
      <h2 style={{ margin: "0 0 12px" }}>From incident to dispatch decision</h2>
      {qa?.applicable && <div className={s.answered}>
        <p className={s.kicker}>Checked in the records already (before asking anyone)</p>
        <ul className={s.factList}>
          {qa.questions.map((q: Any) => <li key={q.id}><b>{q.question}</b><br />
            {q.status === "answered" || q.status === "reported_done" ? <span>{q.answer}</span> : <span className={s.dim}>Not in these records. Needs: {q.needs}.</span>}
            {q.order_note ? <><br /><span className={s.dim}>Order of events: {q.order_note}</span></> : null}</li>)}
        </ul>
        <RecordingRead d={qa.discovery} />
      </div>}
      {(qa?.lessons?.lessons || []).length > 0 && <div className={s.answered}>
        <p className={s.kicker}>Earlier cases with a similar signature</p>
        <Lessons lessons={qa.lessons} />
      </div>}
      {qa?.causal?.applicable && qa.causal.events?.length > 0 && <div className={s.answered}>
        <p className={s.kicker}>Which mechanisms the records leave open</p>
        <SignalChain causal={qa.causal} />
      </div>}
      <Assistant caseId={caseId} me={me} onChanged={after} stamp={(data.ledger || []).length} onDecideOther={() => document.getElementById("your-decision")?.scrollIntoView({ behavior: "smooth" })} />
      <details className={s.reasoning}>
      <summary>Show the full reasoning</summary>
      <ol className={s.stepper}>{stepNames.map((n, i) => <li key={n} className={st.step > i + 1 ? s.stepDone : st.step === i + 1 ? s.stepNow : ""}><span>{i + 1}</span>{n}</li>)}</ol>

      <h3 className={s.stepTitle}>1 · What the logs say</h3>
      <div className={s.two}>
        <div>
          <p className={s.kicker}>Supported</p>
          {a.supported.length === 0 ? <p className={s.empty}>Nothing points at a part yet.</p> : a.supported.map((x: Any) => (
            <p key={x.part} style={{ margin: "0 0 6px" }}><b>{x.label}</b><br /><span className={s.dim}>{x.evidence.length} finding{x.evidence.length === 1 ? "" : "s"}</span>{" "}
              <button className={s.link} onClick={onEvidence}>See the evidence</button></p>))}
        </div>
        <div>
          <p className={s.kicker}>Can't tell apart</p>
          {apart ? <><p style={{ margin: "0 0 4px" }}><b>{apart}</b></p><p className={s.dim} style={{ margin: 0 }}>{a.why_not_separable}</p></> : <p className={s.dim}>{a.why_not_separable}</p>}
          {(a.effects || []).length > 0 && <p className={s.dim} style={{ marginTop: 8 }}>Not counted as a cause: {a.effects.map((x: Any) => x.label).join(", ")} (only shows how the machine moved)</p>}
          {a.less_likely.map((x: Any) => <p key={x.part} className={s.dim} style={{ marginTop: 8 }}>Less likely: {x.label} ({x.why.charAt(0).toLowerCase() + x.why.slice(1)})</p>)}
        </div>
      </div>
      {st.step === 1 && <div style={{ marginTop: 14 }}><button className={s.button} disabled={busy} onClick={() => post("plan")}>{busy ? "Working…" : "Find the most useful next check"}</button></div>}

      {st.step >= 2 && <>
        <h3 className={s.stepTitle}>2 · Check next <span className={s.dim} style={{ fontWeight: 400 }}>(a person approves, the site does it)</span></h3>
        <div style={{ display: "grid", gap: 12 }}>{st.checks.map((c: Any) => (
          <div key={c.check_id} className={s.nextBox} style={{ display: "block" }}>
            <p style={{ margin: 0 }}><b>{plain(c.question)}</b></p>
            <p className={s.dim} style={{ margin: "2px 0 8px", fontSize: 13 }}>{c.who}</p>
            <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 2, fontSize: 14 }}>{c.outcomes.map((o: Any) => <li key={o.key}>If <i>{o.label.toLowerCase()}</i> → {o.meaning}</li>)}</ul>
            {c.settles_note && <p className={s.dim} style={{ margin: "6px 0 0", fontSize: 13 }}>{c.settles_note}.{c.note ? ` ${c.note}` : ""}</p>}
            {!c.approval && st.step === 2 && <Approve check={c} runId={st.run_id} caseId={caseId} onSaved={after} others={apart || "the open explanations"} me={me} />}
            {c.approval && <p style={{ margin: "10px 0 0" }}><span className={`${s.pill} ${s.verified}`}>Approved</span> <span className={s.dim}>by {c.approval.by} · {c.approval.reason}</span></p>}
            {c.approval && !c.result && st.step === 2 && <SiteResult check={c} runId={st.run_id} caseId={caseId} onSaved={after} />}
            {c.result && <p style={{ margin: "8px 0 0" }}><span className={`${s.pill} ${s.thread}`}>Site result</span> {c.outcomes.find((o: Any) => o.key === c.result.outcome)?.label || "Something else"}. <span className={s.dim}>{c.result.observations} · {c.result.performer} · {c.result.evidence_note}</span></p>}
          </div>))}</div>
        {st.step === 2 && <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className={s.button} disabled={busy || !st.checks.some((c: Any) => c.result)} onClick={() => post("report")}>{busy ? "Working…" : "Make the dispatch decision"}</button>
          {!st.checks.some((c: Any) => c.result) && <span className={s.dim}>Needs at least one site result.</span>}
        </div>}
      </>}

      {r && <>
        <h3 className={s.stepTitle}>3 · Dispatch readiness</h3>
        <p className={s.decision}>{r.headline}</p>
        <div className={s.two}>
          <div><p className={s.kicker}>Can do now</p>
            <ul className={s.canList}>{r.can_do_now.map((x: Any, i: number) => <li key={i}>{plain(x.text)} <span className={s.dim}>· {x.who}</span></li>)}</ul></div>
          <div><p className={s.kicker}>Can't do yet</p>
            <ul className={s.cantList}>{r.cannot_do_yet.map((x: Any, i: number) => <li key={i}><b>{plain(x.text)}</b><br /><span className={s.dim}>{plain(x.why)}</span></li>)}</ul></div>
        </div>
        <p className={s.kicker} style={{ marginTop: 14 }}>Why</p>
        <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4, fontSize: 14 }}>{r.why.map((w: Any, i: number) => <li key={i}>{plain(w.text)} <span className={s.dim}>· {w.source}</span></li>)}</ul>
        {r.still_unknown.length > 0 && <><p className={s.kicker} style={{ marginTop: 14 }}>Still unknown</p>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{r.still_unknown.map((u: string, i: number) => <li key={i}>{plain(u)}</li>)}</ul></>}
        <p className={s.note}>{r.boundary} A person makes the call.</p>
      </>}
      </details>
      <div id="your-decision"><DecisionCheck caseId={caseId} me={me} onChanged={after} /></div>
      {error && <p className={s.warning}>{error}</p>}
      <How>Rules {st.policy}: candidates are the parts the logs implicate that nobody ruled out. A check is proposed only if one of its outcomes makes some candidates more likely and others less likely; the two that split them best are shown. Results move the balance; a lead of two or more decides the report. Every line links to a record in this case.</How>
    </section>
  );
}

function Sources({ ledger, imports }: { ledger: Any[]; imports: Any[] }) {
  const trigger = ledger.find((e) => e.kind === "trigger_event");
  const acquisitions = ledger.filter((e) => e.kind === "acquisition");
  return (
    <section id="sources" className={s.panel}>
      <h2>Where the evidence came from</h2>
      <p className={s.lede}>Files and connectors behind this case.</p>
      {trigger && <p style={{ margin: "0 0 12px" }}><span className={`${s.pill} ${s.thread}`}>trigger</span> {trigger.payload.source} · {human(trigger.payload.type)} · <span className={s.mono}>{trigger.payload.event_id}</span> at <span className={s.mono}>{time(trigger.payload.occurred_at, true)}</span></p>}
      {acquisitions.length === 0 ? (
        <p className={s.empty}>{imports.length} file{imports.length === 1 ? "" : "s"} uploaded by hand.</p>
      ) : (
        <div className={s.tableWrap}>
          <table className={s.table}>
            <thead><tr><th>Connector</th><th>File</th><th>Source</th><th>Result</th></tr></thead>
            <tbody>{acquisitions.flatMap((a) => (a.payload.items.length ? a.payload.items : [{ filename: "—", source: a.payload.error || "nothing found", status: a.payload.error ? "error" : "empty" }]).map((i: Any, k: number) => (
              <tr key={a.id + k}>
                <td>{a.payload.connector_id}<br /><small style={{ color: "var(--sr-ink-3)" }}>{human(a.payload.connector_kind)}</small></td>
                <td className={s.mono} style={{ fontSize: 12 }}>{i.filename}</td>
                <td style={{ fontSize: 12 }}>{i.source}{i.notes?.length ? <><br /><small style={{ color: "var(--sr-ink-3)" }}>{i.notes[0]}</small></> : null}</td>
                <td><span className={`${s.pill} ${i.status === "imported" ? s.verified : i.status === "duplicate" ? "" : s.anomaly}`}>{human(i.status)}</span>{i.rows != null ? <span className={s.mono} style={{ fontSize: 12 }}> {i.rows} rows</span> : null}</td>
              </tr>)))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const RULE_PRIORITY = ["R-OUTPUT-STUCK", "R-OUTPUT-MIRROR", "R-TRACKING", "R-SEGMENT-RELATIVE", "R-SEGMENT", "R-IO", "R-REFERENCE", "R-BRIEF", "R-SCREEN", "R-EVENT", "R-HEARTBEAT"];
const SECTION_OF_RULE: Record<string, string> = {
  "R-OUTPUT-STUCK": "outputs", "R-OUTPUT-MIRROR": "outputs", "R-TRACKING": "tracking", "R-SEGMENT-RELATIVE": "hypotheses", "R-SEGMENT": "hypotheses", "R-IO": "hypotheses",
  "R-REFERENCE": "reference", "R-BRIEF": "brief", "R-SCREEN": "reference", "R-EVENT": "hypotheses", "R-HEARTBEAT": "hypotheses",
};
const STRENGTH: Record<string, number> = { delay_observed: 3, anomaly_observed: 2, not_observed: 1, no_delay_observed_in_measured_part: 0 };

function buildBrief(data: Any, run: Any) {
  const board: Any[] = run.hypotheses || [];
  const intervention = run.interventions?.[0];
  const trigger = data.ledger.find((e: Any) => e.kind === "trigger_event");
  const what = intervention
    ? { line: intervention.intervention_type === "other"
        ? `Reported on ${data.case.machine_id} at ${time(intervention.occurred_at)} UTC${intervention.symptom ? `: ${human(intervention.symptom)}${intervention.affected_function ? ` (${intervention.affected_function})` : ""}` : ""}`
        : `${intervention.operator} — ${human(intervention.intervention_type)} on ${data.case.machine_id} at ${time(intervention.occurred_at)} UTC`, quote: intervention.stated_reason }
    : trigger ? { line: `${trigger.payload.title} — ${time(trigger.payload.occurred_at)} UTC`, quote: null }
    : { line: data.case.title.replace(/\s*\(SYNTHETIC[^)]*\)\s*$/, ""), quote: null };

  // Ties on status are broken by the strongest kind of evidence: a frozen output or a measured delay beats a shifted statistic.
  const bestRule = (domain: string) => Math.min(99, ...(run.domains[domain]?.observations || [])
    .filter((o: Any) => o.kind === "anomaly" || o.kind === "delay").map((o: Any) => { const i = RULE_PRIORITY.indexOf(o.rule); return i < 0 ? 98 : i; }));
  const candidates = board.filter((b) => b.column !== "ruled_out")
    .sort((a, b) => (b.column === "supported" ? 1 : 0) - (a.column === "supported" ? 1 : 0) || (STRENGTH[b.engine_status] ?? 0) - (STRENGTH[a.engine_status] ?? 0)
      || bestRule(a.domain) - bestRule(b.domain));
  const top = candidates[0] && (STRENGTH[candidates[0].engine_status] ?? 0) >= 2 ? candidates[0] : null;
  const observations: Any[] = top ? [...(run.domains[top.domain]?.observations || [])].filter((o) => o.kind === "anomaly" || o.kind === "delay")
    .sort((a, b) => (RULE_PRIORITY.indexOf(a.rule) + 99) % 99 - (RULE_PRIORITY.indexOf(b.rule) + 99) % 99) : [];
  const ruledOut = [
    ...board.filter((b) => b.column === "ruled_out").map((b) => ({ domain: b.domain, why: b.human?.basis, scope: b.human?.scope, by: b.human?.by })),
    ...board.filter((b) => b.column !== "ruled_out" && b.engine_status === "no_delay_observed_in_measured_part" && !b.human)
      .map((b) => ({ domain: b.domain, why: "No delay in the measured part", scope: "measured part only", by: null })),
  ].slice(0, 2);
  const doneHere = new Set(data.ledger.filter((e: Any) => e.kind === "check_execution" && e.payload.run_id === run.id).map((e: Any) => e.payload.check_id));
  const next = (run.checks || []).find((c: Any) => isEligible(c) && !doneHere.has(c.check_id));
  const ref = run.reference;
  // One unknown, and never the same sentence the next check already shows.
  const unknowns: string[] = [
    ...(ref && ref.status !== "declared" && ref.status !== "explicit" ? ["No normal period was declared. Comparisons assume the earlier data was normal."]
      : ref && assumedBasis(ref) ? ["The normal period is assumed (the start of the recording), not confirmed by someone who saw the machine."] : []),
    ...(run.checks || []).filter((c: Any) => ["CHK-CLOCK-OFFSET", "CHK-PROBE-PATH", "CHK-CONFIRM-CONTEXT"].includes(c.check_id) && c.trigger && !c.previously_performed).map((c: Any) => c.trigger),
    ...(run.missing_information || []).map((m: string) => m.replace(/^[^:]*\.(csv|jsonl?|json): /, "")),
  ];
  const nextText = next ? next.trigger || next.why : "";
  const unknown = unknowns.find((u) => u !== nextText) || null;
  const cmp = run.findings.reference_comparison?.signals || [];
  const normal = cmp.filter((g: Any) => g.status === "within_reference_range").length;
  const reports = data.ledger.filter((e: Any) => e.kind === "agent_report" && e.payload.run_id === run.id && !e.payload.question);
  const planEntry = [...data.ledger].reverse().find((e: Any) => e.kind === "next_action" && e.payload.run_id === run.id && e.payload.stage === "plan");
  const oldRules = !!planEntry && planEntry.payload.policy !== PLAN_POLICY;   // a draft made under earlier rules is not acted on
  const prep = oldRules ? null : [...data.ledger].reverse().find((e: Any) => e.kind === "next_action" && e.payload.run_id === run.id && e.payload.stage === "prepared");
  const prepIds: string[] = prep ? (prep.payload.check_ids || [prep.payload.check_id]) : [];
  const prepCheck = prep && (run.checks || []).find((c: Any) => c.check_id === prepIds[0]);
  const answered = prep && prepIds.every((id) => data.ledger.some((e: Any) => e.kind === "check_execution" && e.payload.run_id === run.id && e.payload.check_id === id));
  const sent = prep && data.ledger.some((e: Any) => e.kind === "request_sent" && e.payload.run_id === run.id && e.payload.check_ids.includes(prepIds[0]));
  const hasReport = data.ledger.some((e: Any) => e.kind === "next_action" && e.payload.run_id === run.id && e.payload.stage === "report");
  const approved = prep && prepIds.every((id) => data.ledger.some((e: Any) => e.kind === "check_review" && e.payload.run_id === run.id && e.payload.check_id === id));
  const prepared = prepCheck && !answered && !hasReport ? { question: prepIds.length > 1 ? `Ask the site for ${prepIds.length} logs: ${prepIds.map((id) => plain((run.checks || []).find((c: Any) => c.check_id === id)?.question || id).split(" (")[0].replace(/^Retrieve /, "")).join("; ")}` : prepCheck.question,
    status: sent ? "sent, waiting for the site's answer" : approved ? "draft approved, not sent yet" : "draft, waiting for your approval", action: sent ? "Record the answer" : approved ? "Mark as sent" : "Review" } : null;
  const rep = [...data.ledger].reverse().find((e: Any) => e.kind === "next_action" && e.payload.run_id === run.id && e.payload.stage === "report");
  const decidedHere = data.ledger.some((e: Any) => e.kind === "decision" && e.payload.run_id === run.id);
  const recommendation = rep && !decidedHere ? rep.payload.headline : null;
  return { what, top, observations, ruledOut, next, unknown, normal, prepared, recommendation, oldRules, agent: reports[reports.length - 1]?.payload };
}

const symptomText = (r: Any) => `${human(r.symptom)}${r.function ? ` (${r.function})` : ""}`;

function Precedent({ m }: { m: Any }) {
  const [open, setOpen] = useState(false);
  const c = m.closure || {};
  const different = (m.only_now || []).length + (m.only_then || []).length;
  const symptomOnly = m.match_basis === "reported symptom only";
  return (
    <div className={s.precedent}>
      <p className={s.answerSmall} style={{ margin: 0 }}>
        <b>{m.title}</b> <span className={s.dim}>· {symptomOnly ? "same reported symptom" : `${Math.round(m.similarity * 100)}% similar findings`}{m.same_machine ? " · same machine" : ` · ${m.machine_id}`}</span>
      </p>
      {symptomOnly && <p className={s.answerSmall} style={{ margin: "4px 0 0" }}>
        <b>Same complaint, different findings.</b> Then: {(m.parts_then || []).join(", ") || "nothing"}. Now: {(m.parts_now || []).join(", ") || "nothing"}.
      </p>}
      <p className={s.answerSmall} style={{ margin: "4px 0 0" }}>
        Then: {causeTitle(c).toLowerCase()}{hasCause(c) ? <>, {dl(c.cause_domain)} — {c.cause}</> : null} <StrengthBadge p={c} />
        <span className={s.dim}> · machine {label(RESOLUTION, c.resolution_status).toLowerCase()}</span>
      </p>
      {c.decisive_check && <p className={s.answerSmall} style={{ margin: "4px 0 0" }}>Settled by: <b>{c.decisive_check}</b></p>}
      {m.context?.status === "differs" && <p className={s.answerSmall} style={{ margin: "4px 0 0" }}><b>Different setup:</b> {m.context.differs.map((d: Any) => `${human(d.key)} ${d.then} then, ${d.now} now`).join("; ")}</p>}
      {m.context?.status === "unverified" && <p className={s.answerSmall} style={{ margin: "4px 0 0" }}>Setup can&apos;t be compared: {m.context.recorded_for_one_case_only.map(human).join(", ")} recorded for one case only</p>}
      {m.context?.status === "not_recorded" && <p className={s.note} style={{ margin: "4px 0 0" }}>Firmware, software and workload not recorded for either case.</p>}
      {m.check_not_moved_up && <p className={s.note} style={{ margin: "4px 0 0" }}>{m.check_not_moved_up}</p>}
      <p className={s.answerSmall} style={{ margin: "4px 0 0" }}>
        Same: {(m.same || []).length} finding{(m.same || []).length === 1 ? "" : "s"} · Different: <b>{different}</b>{" "}
        <button className={s.link} onClick={() => setOpen(!open)}>{open ? "Hide" : "What differs"}</button>
      </p>
      {different === 0 && <p className={s.note} style={{ margin: "4px 0 0" }}>Same findings can have different causes. The old cause isn't proof.</p>}
      {open && <div className={s.diff}>
        {(m.only_now || []).length > 0 && <div><b>Only in this case</b><ul>{m.only_now.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
        {(m.only_then || []).length > 0 && <div><b>Only in the earlier case</b><ul>{m.only_then.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
        {(m.same || []).length > 0 && <div><b>In both</b><ul>{m.same.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
      </div>}
    </div>
  );
}

function Brief({ data, run, verification, go, onAccept, acceptBusy, error }: { data: Any; run: Any; verification: Any; go: (s: string) => void; onAccept: (c: Any) => void; acceptBusy: boolean; error: string }) {
  const b = buildBrief(data, run);
  // The same reading Next action shows (GET only, nothing is written): when no remote check could change which part
  // is more likely, the Brief says so too, instead of offering a context check as if it were the next step.
  const [assistant, setAssistant] = useState<Any>(null);
  const ledgerSize = (data.ledger || []).length;
  useEffect(() => {
    let live = true;
    call(`/engine/cases/${data.case.id}/autopilot`).then((v) => { if (live) setAssistant(v); }).catch(() => { if (live) setAssistant(null); });
    return () => { live = false; };
  }, [data.case.id, run.id, ledgerSize]);
  const noSplit = assistant?.stage === "nothing_to_split" && assistant.run_id === run.id && !b.recommendation && !b.prepared && !b.oldRules;
  // What the case's own records already answer (GET, computed on view). When available, the Brief states facts first.
  // undefined = still loading: the Brief shows a short placeholder instead of one layout that then jumps to another.
  const [qaFor, setQaFor] = useState<{ id: string; v: Any } | null>(null);   // answers belong to one case
  const qa = qaFor && qaFor.id === data.case.id ? qaFor.v : undefined;
  useEffect(() => {
    let live = true;
    const id = data.case.id;
    call(`/engine/cases/${id}/questions`).then((v) => { if (live) setQaFor({ id, v }); }).catch(() => { if (live) setQaFor({ id, v: null }); });
    return () => { live = false; };
  }, [data.case.id, run.id, ledgerSize]);
  const facts = qa?.applicable && qa.brief ? qa : null;
  const qaPending = qa === undefined;
  const factFindings: Any[] = Object.values(run.domains || {}).flatMap((d: Any) => (d.observations || []))
    .filter((o: Any) => ["R-OUTPUT-STUCK", "R-TRACKING"].includes(o.rule));
  const nextCheck = facts?.next?.check_id ? (run.checks || []).find((c: Any) => c.check_id === facts.next.check_id) : null;
  const t = run.triage;
  const closure = [...data.ledger].reverse().find((e: Any) => e.kind === "case_closure");
  const learned: Any[] = (run.machine_history?.prior_cases || []).filter((c: Any) => c.closure);
  // Earlier cases that the lessons layer already weighed (with what differs) are not repeated as a coarser match.
  // For a text log the coarse match (findings on generic log signals) says nothing: only the lessons layer, which reads the log's own signature, decides.
  const similar: Any[] = qa?.log ? [] : (run.precedents?.matches || []).filter((m: Any) => !(qa?.lessons?.lessons || []).some((l: Any) => l.case_id === m.case_id));
  // A text log's own checks come before generic engine drafts, which are written for measured signals.
  const logFirst = !!(qa?.log && qa?.brief && qa?.next);
  const accepted = (id: string) => data.ledger.some((e: Any) => e.kind === "check_review" && e.payload.run_id === run.id && e.payload.check_id === id && e.payload.verdict !== "rejected");
  const agentLead = b.agent?.report?.hypotheses?.find((h: Any) => h.status === "leading");
  // Use the agent's wording only when it points at the same part as the engine's evidence; otherwise show both.
  const leading = agentLead && b.top && (!agentLead.domain || agentLead.domain === b.top.domain) ? agentLead : null;
  const agentElsewhere = agentLead && !leading ? agentLead : null;
  return (
    <section className={s.brief} aria-labelledby="brief-title">
      <div className={s.briefMeta}>
        <span>{data.case.machine_id}{run.machine_profile && run.machine_profile.id !== "generic" ? ` · ${run.machine_profile.name}` : ""}</span>
        <span>Severity <b>{t?.confirmed?.severity ?? t?.proposed.severity.level}</b></span>
        <span>Owner <b>{t?.confirmed?.owner ?? t?.proposed.owner.team}</b></span>
        <span>Cause <b>{closure ? label(INVESTIGATION, closure.payload.investigation_status ?? "cause_confirmed").replace(/, not confirmed$/, "").toLowerCase() : "open"}</b></span>
        {closure && <span>Machine <b>{label(RESOLUTION, closure.payload.resolution_status ?? (closure.payload.fix ? "resolved" : null)).toLowerCase()}</b></span>}
        {data.case.synthetic ? <span className={s.synthetic}>Synthetic demo data</span> : <span className={s.synthetic} style={{ borderStyle: "solid", color: "var(--sr-verified)" }}>Real data{data.case.machine_id.startsWith("ALFA") ? " · CMU AirLab ALFA" : data.case.machine_id.startsWith("ANYmal") ? " · ETH RSL GrandTour" : ""}</span>}
      </div>

      <div className={s.row}>
        <span className={s.rowLabel}>What happened</span>
        <div>
          <h1 id="brief-title" className={s.briefTitle}>{b.what.line}</h1>
          {b.what.quote && <p className={s.quoteSmall}>“{b.what.quote}”</p>}
        </div>
      </div>

      {closure && (
        <div className={s.row}>
          <span className={s.rowLabel}>{causeTitle(closure.payload)}</span>
          <div>
            {hasCause(closure.payload)
              ? <p className={s.answer}><b>{dl(closure.payload.cause_domain)}</b> — {closure.payload.cause} <StrengthBadge p={closure.payload} /></p>
              : <p className={s.answer}>No cause established{closure.payload.evidence_note ? ` — ${closure.payload.evidence_note}` : ""}</p>}
            <p className={s.answerSmall}>
              Machine: <b>{label(RESOLUTION, closure.payload.resolution_status ?? (closure.payload.fix ? "resolved" : null))}</b>
              {closure.payload.fix ? ` · ${closure.payload.fix} (${closure.payload.fix_held === "not_yet_known" ? "not yet known if it held" : human(closure.payload.fix_held)})` : ""}
              {" · "}Closed by {closure.actor}, {closure.recorded_at.slice(0, 10)}{" "}
              <button className={s.link} onClick={() => go("closure")}>Details</button>
            </p>
          </div>
        </div>
      )}

      {!closure && similar.length > 0 && (
        <div className={s.row}>
          <span className={s.rowLabel}>Similar earlier cases</span>
          <div>
            {similar.map((m: Any) => <Precedent key={m.case_id} m={m} />)}
            {run.precedents.not_shown > 0 && <p className={s.answerSmall}>+{run.precedents.not_shown} more similar case{run.precedents.not_shown === 1 ? "" : "s"}</p>}
            <p className={s.note} style={{ marginTop: 6 }}>Leads, not answers.</p>
          </div>
        </div>
      )}

      {!closure && similar.length === 0 && learned.length > 0 && (
        <div className={s.row}>
          <span className={s.rowLabel}>Learned before</span>
          <div>{learned.slice(0, 2).map((c: Any) => (
            <p key={c.case_id} className={s.answerSmall}>
              An earlier case on this machine ({c.title.replace(/\s*\(SYNTHETIC[^)]*\)\s*$/, "")}) was closed: <b>{causeTitle(c.closure).toLowerCase()}</b>{hasCause(c.closure) ? <>, {dl(c.closure.cause_domain)}: {c.closure.cause}</> : null}
              {c.closure.fix ? <span className={s.dim}> · fix: {c.closure.fix} ({human(c.closure.fix_held)})</span> : null}
              {c.closure.would_have_saved_time ? <><br /><span className={s.dim}>What would have saved time: {c.closure.would_have_saved_time}</span></> : null}
            </p>))}
            <p className={s.note} style={{ marginTop: 6 }}>Not similar enough to be a lead. <button className={s.link} onClick={() => go("history")}>History</button></p>
          </div>
        </div>
      )}

      {facts && <>
        <div className={s.row}>
          <span className={s.rowLabel}>What we established</span>
          <div>
            <ul className={s.factList}>
              {factFindings.map((o: Any, i: number) => <li key={`f${i}`}>{plain(o.text).replace(/^(\S+) output held (\S+) /, (_m: string, ch: string, v: string) => `${ch} output held at ${v}${/rc\d/.test(ch) ? " µs" : ""} `)}</li>)}
              {facts.established.map((t: string, i: number) => <li key={`e${i}`}>{t}</li>)}
            </ul>
            {(() => { const lead = factFindings.map((o: Any) => plotFor(run, o.rule, o.text)).find(Boolean);
              return lead ? <Waveform caseId={data.case.id} spec={lead} load={call} compact /> : null; })()}
          </div>
        </div>
        {facts.cannot_establish.length > 0 && <div className={s.row}>
          <span className={s.rowLabel}>What we can&apos;t establish</span>
          <div>
            <ul className={s.factList}>{facts.cannot_establish.map((q: Any, i: number) => <li key={i}><b>{q.question}</b><br /><span className={s.dim}>Needs: {q.needs}</span></li>)}</ul>
            {agentLead && <p className={s.answerSmall} style={{ marginTop: 8 }}>One explanation to test (AI agent, not confirmed): {firstSentence(agentLead.statement)}{" "}
              <button className={s.link} onClick={() => go("agent")}>Why</button></p>}
          </div>
        </div>}
      </>}

      {qaPending && <div className={s.row}>
        <span className={s.rowLabel}>What we established</span>
        <p className={s.dim} style={{ margin: 0 }} aria-live="polite">Checking the case&apos;s records…</p>
      </div>}

      {!facts && !qaPending && <div className={s.row}>
        <span className={s.rowLabel}>Look here first</span>
        <div>
          {b.top ? <>
            {(() => {
              const headline = plain(leading ? firstSentence(leading.statement) : b.top.human?.basis || b.observations[0]?.text || ENGINE_STATUS[b.top.engine_status]);
              const supporting = b.observations.filter((o: Any) => plain(o.text) !== headline);
              return <>
                <p className={s.answer}><b>{dl(b.top.domain)}</b>{" — "}{leading ? <>most likely: {headline.charAt(0).toLowerCase() + headline.slice(1)} <span className={s.dim} style={{ fontSize: 13 }}>(AI agent's hypothesis, not confirmed)</span></> : headline}</p>
                <div className={s.chips}>
                  {b.observations[0] && headline === plain(b.observations[0].text) && <button className={s.link} onClick={() => go(SECTION_OF_RULE[b.observations[0].rule] || "hypotheses")}>See the record</button>}
                  {supporting.slice(0, 2).map((o: Any, i: number) => (
                    <button key={i} className={s.evidenceChip} onClick={() => go(SECTION_OF_RULE[o.rule] || "hypotheses")}>{plain(o.text).length > 70 ? plain(o.text).slice(0, 68) + "…" : plain(o.text)}</button>))}
                  {supporting.length > 2 && <button className={s.link} onClick={() => go("hypotheses")}>+{supporting.length - 2} more</button>}
                </div>
                {(() => { const lead = b.observations.map((o: Any) => plotFor(run, o.rule, o.text)).find(Boolean);
                  return lead ? <Waveform caseId={data.case.id} spec={lead} load={call} compact /> : null; })()}
                {agentElsewhere && <p className={s.answerSmall} style={{ marginTop: 10 }}>The AI agent points to <b>{dl(agentElsewhere.domain)}</b> — {agentElsewhere.statement.split(/(?<=\.)\s/)[0]} <button className={s.link} onClick={() => go("agent")}>Why</button></p>}
              </>;
            })()}
          </> : <p className={s.answer}>Nothing stands out yet. <button className={s.link} onClick={() => go("hypotheses")}>See the data</button></p>}
        </div>
      </div>}

      {(qa?.lessons?.lessons || []).length > 0 && <div className={s.row}>
        <span className={s.rowLabel}>Learned from earlier cases</span>
        <div><Lessons lessons={qa.lessons} compact /></div>
      </div>}

      {qa?.causal?.applicable && qa.causal.events?.length > 0 && <div className={s.row}>
        <span className={s.rowLabel}>Where it started</span>
        <div><SignalChain causal={qa.causal} compact domains={run.machine_profile?.domains} /></div>
      </div>}

      {b.ruledOut.length > 0 && (
        <div className={s.row}>
          <span className={s.rowLabel}>Less likely</span>
          <div>{b.ruledOut.map((r: Any) => (
            <p key={r.domain} className={s.answerSmall}><b>{dl(r.domain)}</b> — {r.why}{r.scope ? <span className={s.dim}> ({r.scope})</span> : null}</p>))}</div>
        </div>
      )}

      {!qaPending && <div className={s.row}>
        <span className={s.rowLabel}>{facts && (logFirst || (!b.recommendation && !b.prepared && !b.oldRules)) ? "Investigate next" : "Do next"}</span>
        <div>
          {facts?.next && (logFirst || (!b.recommendation && !b.prepared && !b.oldRules)) ? <div className={s.nextBox}>
            <div>
              <p className={s.answer} style={{ margin: 0 }}><b>{facts.next.question}</b></p>
              <p className={s.answerSmall}>Needs: {facts.next.needs}.</p>
              <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>Why a person: {facts.next.why_a_person}</p>
            </div>
            {nextCheck && isEligible(nextCheck) ? (accepted(nextCheck.check_id) ? <span className={`${s.pill} ${s.verified}`}>Accepted</span>
              : <button className={s.button} disabled={acceptBusy} onClick={() => onAccept(nextCheck)}>{acceptBusy ? "Recording…" : "Ask for it"}</button>)
              : <button className={s.button} onClick={() => go("next")}>Open</button>}
          </div> : b.recommendation ? <div className={s.nextBox}>
            <div>
              <p className={s.answer} style={{ margin: 0 }}><b>{plain(b.recommendation)}</b></p>
              <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>Veyra's recommendation, checked against the evidence · your decision</p>
            </div>
            <button className={s.button} onClick={() => go("next")}>Review</button>
          </div> : b.oldRules ? <div className={s.nextBox}>
            <div>
              <p className={s.answer} style={{ margin: 0 }}><b>Investigate again</b></p>
              <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>Veyra's earlier draft for this case was made with older rules and is no longer used.</p>
            </div>
            <button className={s.button} onClick={() => go("next")}>Open</button>
          </div> : b.prepared ? <div className={s.nextBox}>
            <div>
              <p className={s.answer} style={{ margin: 0 }}><b>{plain(b.prepared.question)}</b></p>
              <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>Drafted by Veyra · {b.prepared.status}</p>
            </div>
            <button className={s.button} onClick={() => go("next")}>{b.prepared.action}</button>
          </div> : noSplit ? <div>
            <div className={s.nextBox}>
              <div>
                <p className={s.answer} style={{ margin: 0 }}><b>Veyra&apos;s rules can&apos;t separate the open explanations</b></p>
                <p className={s.answerSmall}>{plain(firstSentence(assistant.say))}</p>
                <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>Veyra&apos;s reading of the evidence · your decision</p>
              </div>
              <button className={s.button} onClick={() => go("next")}>Decide</button>
            </div>
            {b.next && <p className={s.answerSmall} style={{ marginTop: 8 }}>
              Optional, for context: {plain(b.next.question).replace(/^./, (c: string) => c.toLowerCase())}. <span className={s.dim}>Under Veyra&apos;s rules its result would not change which part is more likely.</span>{" "}
              {accepted(b.next.check_id) ? <span className={`${s.pill} ${s.verified}`}>Accepted</span>
                : <button className={s.link} disabled={acceptBusy} onClick={() => onAccept(b.next)}>{acceptBusy ? "Recording…" : "Accept"}</button>}
            </p>}
          </div> : b.next ? <div className={s.nextBox}>
            <div>
              <p className={s.answer} style={{ margin: 0 }}><b>{plain(b.next.question)}</b></p>
              <p className={s.answerSmall}>{plain(b.next.trigger || b.next.why)}</p>
              <p className={s.dim} style={{ margin: "2px 0 0", fontSize: 13 }}>{b.next.who} · {AVAILABILITY[b.next.availability.state] ?? human(b.next.availability.state)}
                {(b.next.eligibility?.prerequisites || []).length > 0 && <> · first: {b.next.eligibility.prerequisites.join("; ").toLowerCase()}</>}</p>
            </div>
            {accepted(b.next.check_id) ? <span className={`${s.pill} ${s.verified}`}>Accepted</span>
              : <button className={s.button} disabled={acceptBusy} onClick={() => onAccept(b.next)}>{acceptBusy ? "Recording…" : b.next.availability.state === "ready_automatic" ? "Accept and run" : "Accept"}</button>}
          </div> : <p className={s.answer}>Nothing left to check. Record a decision.</p>}
          {error && <p className={s.warning}>{error}</p>}
        </div>
      </div>}

      {facts && <div className={s.row}>
        <span className={s.rowLabel}>Decision status</span>
        <p className={s.answerSmall} style={{ margin: 0 }}><b>{facts.decision_status}</b></p>
      </div>}

      {b.unknown && !facts && (
        <div className={s.row}>
          <span className={s.rowLabel}>Still unknown</span>
          <p className={s.answerSmall} style={{ margin: 0 }}>{plain(b.unknown)}</p>
        </div>
      )}

      <p className={s.briefFoot}>
        {b.normal > 0 ? `${b.normal} other signal${b.normal === 1 ? "" : "s"} normal. ` : ""}
        {verification?.ledger_chain.valid ? "Record intact. " : ""}
        <button className={s.link} onClick={() => go("hypotheses")}>All evidence</button>
        {!closure && <> · <button className={s.link} onClick={() => go("closure")}>Close the case</button></>}
      </p>
    </section>
  );
}

function Outputs({ run }: { run: Any }) {
  const o = run.findings.actuator_outputs;
  const normal = o.machine_baseline?.suppressed_as_normal || [];
  const runs = o.machine_baseline?.known_good_cases?.length || 0;
  return (
    <section id="outputs" className={s.panel}>
      <h2>Actuator outputs</h2>
      <p className={s.lede}>Outputs that froze, or stopped matching their twin.</p>
      {o.findings.length > 0 && <p className={s.findingRef}>{o.findings.length} output{o.findings.length === 1 ? "" : "s"} flagged ({o.findings.map((f: Any) => f.channel).join(", ")}): shown with the recorded samples under All findings.</p>}
      {normal.length > 0 && <p className={s.note}>Skipped, normal on this machine: {normal.map((n: Any) => plain(n.text)).join("; ")}.</p>}
      <How>{runs ? `Checked against ${runs} normal runs of this machine. ` : "Compared with the normal period. "}Rules {o.rules_version}: {o.rules.STUCK}; {o.rules.MIRROR}. {o.boundary}</How>
    </section>
  );
}

function Tracking({ run }: { run: Any }) {
  const t = run.findings.tracking;
  return (
    <section id="tracking" className={s.panel}>
      <h2>Did the machine follow its commands?</h2>
      <p className={s.lede}>Commanded vs measured.</p>
      {t.findings.length > 0 && <p className={s.findingRef} style={{ margin: "0 0 10px" }}>Stopped following: {t.findings.map((f: Any) => f.axis).join(", ")}. Command and measured are plotted under All findings.</p>}
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead><tr><th>Joint / axis</th><th>Command size</th><th style={{ textAlign: "right" }}>Normal error</th><th style={{ textAlign: "right" }}>During problem</th><th></th></tr></thead>
          <tbody>{t.axes.filter((a: Any) => a.baseline).flatMap((a: Any) => Object.entries(a.baseline).map(([bin, b]: [string, Any], i: number) => (
            <tr key={a.axis + bin}>
              <td>{i === 0 ? a.axis : ""}</td><td className={s.mono}>{bin}{a.unit ?? "°"}</td>
              <td className={s.num}>{b[a.baseline_statistic ?? "median"]} {a.unit ?? "°"} <span className={s.note}>{a.baseline_statistic ?? "median"}</span></td>
              <td className={s.num}>{a.symptom_window?.[bin]?.[a.baseline_statistic ?? "median"] ?? "—"}</td>
              <td>{i === 0 ? <span className={`${s.pill} ${a.status === "departed" ? s.anomaly : s.verified}`}>{a.status === "departed" ? "departed" : "followed"}</span> : null}</td>
            </tr>)))}</tbody>
        </table>
      </div>
      <How>Compared with {t.axes.find((a: Any) => a.baseline_source)?.baseline_source || "the normal period"}, grouped by command size. Rules {t.rules_version}: {t.rule}. {t.boundary}</How>
    </section>
  );
}

const fileToBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
  reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
  reader.readAsDataURL(file);
});
const toIso = (local: string) => (local ? new Date(local + "Z").toISOString().replace(".000Z", "Z") : "");

function UploadDialog({ onClose, onDone, initialFiles = [] }: { onClose: () => void; onDone: (caseId: string) => void; initialFiles?: File[] }) {
  const [profiles, setProfiles] = useState<Any[]>([]);
  const [profileId, setProfileId] = useState("veyra-canonical-csv");
  const [custom, setCustom] = useState("");
  const [machine, setMachine] = useState("");
  const [title, setTitle] = useState("");
  const [symptom, setSymptom] = useState({ start: "", end: "", source: "" });
  const [reference, setReference] = useState({ start: "", end: "", basis: "" });
  const [files, setFiles] = useState<File[]>(initialFiles);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Any[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const actor = "Engineer (upload)";

  useEffect(() => { call("/engine/profiles").then((d) => setProfiles(d.profiles)).catch((e) => setError(e.message)); }, []);
  const chosen = profiles.find((p) => p.id === profileId);
  const profile = () => (profileId === "custom" ? JSON.parse(custom) : chosen?.profile);

  async function check() {
    setError(""); setBusy("Checking files…");
    try {
      const prof = profile();
      if (!prof) throw new Error("Choose a file format");
      if (!machine.trim() || !title.trim()) throw new Error("Give the machine id and a short title");
      if (!files.length) throw new Error("Add at least one file");
      if (!caseId && (!symptom.start || !symptom.end)) throw new Error("Add roughly when the problem started and ended (UTC).");
      if (!caseId && new Date(symptom.end + "Z") <= new Date(symptom.start + "Z")) throw new Error("The problem must end after it started.");
      let id = caseId;
      if (!id) {
        const body: Any = { machine_id: machine.trim(), title: title.trim(), actor, synthetic: !!prof.synthetic };
        if (symptom.start || symptom.end) Object.assign(body, { symptom_start: toIso(symptom.start), symptom_end: toIso(symptom.end), symptom_source: symptom.source || "Reported by the engineer who uploaded the logs" });
        id = (await call("/engine/cases", { method: "POST", body: JSON.stringify(body) })).id;
        setCaseId(id);
      }
      const out = [];
      for (const file of files) {
        const content_base64 = await fileToBase64(file);
        const r = await call(`/engine/cases/${id}/imports/preview`, { method: "POST", body: JSON.stringify({ actor, filename: file.name, content_base64, profile: prof }) });
        out.push({ file, content_base64, status: r.status, accepted: r.accepted_row_count ?? r.rows_accepted ?? (r.preview_rows || []).length, rejected: r.rejected_row_count ?? 0,
          reasons: [...new Set((r.rejected_rows || []).flatMap((x: Any) => x.reasons || []))].slice(0, 3), errors: r.file_errors || [], warnings: (r.warnings || []).slice(0, 2) });
      }
      setPreviews(out);
    } catch (e: Any) { setError(e.message); } finally { setBusy(""); }
  }

  async function importAll() {
    if (!caseId) return;
    setError(""); setBusy("Importing and investigating…");
    try {
      const prof = profile();
      for (const p of previews.filter((x) => x.status !== "rejected" && !x.errors.length)) {
        await call(`/engine/cases/${caseId}/imports`, { method: "POST", body: JSON.stringify({ actor, filename: p.file.name, content_base64: p.content_base64, profile: prof,
          review_reason: p.rejected ? `Reviewed in the upload dialog: ${p.rejected} rejected row(s) left out` : null }) });
      }
      if (reference.start && reference.end) {
        await call(`/engine/cases/${caseId}/ledger`, { method: "POST", body: JSON.stringify({ kind: "reference_declared", actor, source: "upload dialog",
          payload: { window_from: toIso(reference.start), window_to: toIso(reference.end), basis: reference.basis || "Declared normal operation by the uploading engineer" } }) });
      }
      await call(`/engine/cases/${caseId}/runs`, { method: "POST", body: JSON.stringify({ actor }) });
      onDone(caseId);
    } catch (e: Any) { setError(e.message); } finally { setBusy(""); }
  }

  const importable = previews.some((p) => p.status !== "rejected" && !p.errors.length);
  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-labelledby="upload-title">
      <div className={s.dialog}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 12 }}>
          <div>
            <h2 id="upload-title" style={{ margin: 0 }}>Upload logs</h2>
            <p className={s.lede} style={{ margin: "4px 0 0" }}>Checked first. Nothing is saved until you import.</p>
          </div>
          <button className={s.quiet} onClick={onClose} aria-label="Close">Close</button>
        </div>

        <div className={s.formGrid}>
          <label className={s.field}><span>Machine id</span><input value={machine} onChange={(e) => setMachine(e.target.value)} placeholder="e.g. EX7" disabled={!!caseId} /></label>
          <label className={s.field}><span>What happened (short title)</span><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Boom slow to respond" disabled={!!caseId} /></label>
          <label className={s.field}><span>File format</span>
            <select value={profileId} onChange={(e) => setProfileId(e.target.value)} disabled={!!caseId}>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              <option value="custom">My own import profile (JSON)</option>
            </select></label>
          <label className={s.field}><span>Files (up to about 5 MB each)</span><input type="file" multiple accept=".csv,.json,.jsonl,.ndjson" onChange={(e) => { setFiles(Array.from(e.target.files || [])); setPreviews([]); }} /></label>
        </div>
        {profileId === "custom" ? <label className={s.field}><span>Import profile JSON</span><textarea rows={5} value={custom} onChange={(e) => setCustom(e.target.value)} disabled={!!caseId} /></label>
          : chosen && <p className={s.note} style={{ marginTop: 6 }}>{chosen.help}</p>}

        <div className={s.formGrid}>
          <label className={s.field}><span>Problem started (UTC, roughly is fine)</span><input type="datetime-local" step="1" value={symptom.start} onChange={(e) => setSymptom({ ...symptom, start: e.target.value })} disabled={!!caseId} /></label>
          <label className={s.field}><span>Problem ended (UTC)</span><input type="datetime-local" step="1" value={symptom.end} onChange={(e) => setSymptom({ ...symptom, end: e.target.value })} disabled={!!caseId} /></label>
        </div>
        <details className={s.done}>
          <summary>Add a normal period (optional, better comparison)</summary>
          <div className={s.formGrid}>
            <label className={s.field}><span>Normal from (UTC)</span><input type="datetime-local" step="1" value={reference.start} onChange={(e) => setReference({ ...reference, start: e.target.value })} /></label>
            <label className={s.field}><span>Normal until (UTC)</span><input type="datetime-local" step="1" value={reference.end} onChange={(e) => setReference({ ...reference, end: e.target.value })} /></label>
          </div>
          <label className={s.field}><span>Who reported the problem</span><input value={symptom.source} onChange={(e) => setSymptom({ ...symptom, source: e.target.value })} placeholder="Operator call to support, 10:42" disabled={!!caseId} /></label>
        </details>

        {previews.length > 0 && (
          <div className={s.tableWrap} style={{ marginTop: 14 }}>
            <table className={s.table}>
              <thead><tr><th>File</th><th>Check</th><th style={{ textAlign: "right" }}>Accepted</th><th style={{ textAlign: "right" }}>Rejected</th><th>Why rejected</th></tr></thead>
              <tbody>{previews.map((p) => (
                <tr key={p.file.name}>
                  <td className={s.mono} style={{ fontSize: 12 }}>{p.file.name}</td>
                  <td><span className={`${s.pill} ${p.status === "rejected" || p.errors.length ? s.fault : p.rejected ? s.anomaly : s.verified}`}>{human(p.status)}</span></td>
                  <td className={s.num}>{p.accepted}</td><td className={s.num}>{p.rejected}</td>
                  <td style={{ fontSize: 12 }}>{[...p.errors, ...p.reasons].join("; ") || p.warnings.join("; ") || "—"}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
        {error && <p className={s.warning}>{error}</p>}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 16, flexWrap: "wrap" }}>
          {busy && <span className={s.note} style={{ margin: "auto 0" }}>{busy}</span>}
          <button className={s.quiet} onClick={check} disabled={!!busy}>{previews.length ? "Check again" : "Check files"}</button>
          <button className={s.button} onClick={importAll} disabled={!!busy || !importable}>Import and investigate</button>
        </div>
      </div>
    </div>
  );
}

const SYMPTOM_OPTIONS: [string, string][] = [["", "Not given"], ["late_response", "Responds late"], ["no_response", "Does not respond"], ["unexpected_motion", "Moves unexpectedly"],
  ["erratic_motion", "Erratic or unsteady motion"], ["stopped_unexpectedly", "Stopped by itself"], ["other", "Something else"]];
const FORMAT_LABEL: Record<string, string> = { ros1_bag: "ROS 1 bag", ros2_bag: "ROS 2 bag", mcap: "MCAP", px4_ulog: "PX4 log", text_log: "Text log" };
const clock = (sec: number) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
function seconds(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const m = t.match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/);
  const v = m ? Number(m[1]) * 60 + Number(m[2]) : Number(t);
  return Number.isFinite(v) && v >= 0 ? v : NaN;
}

function uploadWithProgress(file: File, onProgress: (pct: number) => void): Promise<Any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/engine/recordings?filename=${encodeURIComponent(file.name)}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.setRequestHeader("x-veyra-request", "1");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      let body: Any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 401 && body?.code === "login_required") { window.location.href = "/login"; return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body?.detail ? String(body.detail) : `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("The upload was interrupted. Check the connection and try again."));
    xhr.send(file);
  });
}

const TABULAR = /\.(csv|json|jsonl|ndjson)$/i;

function RecordingDialog({ onClose, onDone, onTabular }: { onClose: () => void; onDone: (caseId: string, jobId?: string) => void; onTabular: (files: File[]) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [rec, setRec] = useState<Any>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [json, setJson] = useState("");
  const [types, setTypes] = useState<Any[]>([]);
  const [f, setF] = useState<Any>({ machine_id: "", machine_class: "", title: "", stated_reason: "", symptom: "", affected: "",
    problem_from: "", problem_to: "", normal_from: "", normal_to: "", normal_basis: "", synthetic: false, allow_model: true });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const set = (k: string) => (e: Any) => setF({ ...f, [k]: e.target.value });

  useEffect(() => { call("/engine/machine-types").then((d) => setTypes(d.machine_types)).catch(() => setTypes([])); }, []);

  async function upload() {
    if (!file) return;
    setError(""); setBusy("Uploading"); setPct(0);
    try {
      const meta = await uploadWithProgress(file, (p) => { setPct(p); if (p >= 100) setBusy("Reading the recording"); });
      setRec(meta);
      setJson(JSON.stringify(meta.suggested || {}, null, 1));
      if (!meta.suggested) {  // PX4 and text logs: fixed reading, no topic choice; a log's whole span is the window
        setF((cur: Any) => ({ ...cur, title: cur.title || file.name.replace(/\.(log|txt|out|console|ulg)$/i, ""), normal_from: "", normal_to: "", normal_basis: "",
          synthetic: cur.synthetic || /synthetic|simulat|\bsim\b|fake|test/i.test(file.name) }));
        return;
      }
      const dur = meta.inspection.duration_s || 0;
      setF((cur: Any) => ({ ...cur, title: cur.title || file.name.replace(/\.(bag|mcap|zip)$/i, ""), normal_from: "0:00", normal_to: clock(Math.min(60, dur / 3)),
        synthetic: cur.synthetic || /synthetic|simulat|\bsim\b|fake|test/i.test(file.name),
        normal_basis: "Assumed normal: the start of the recording, before the problem" }));
    } catch (e: Any) { setError(e.message); } finally { setBusy(""); setPct(null); }
  }

  const duration = rec?.inspection?.duration_s || 0;
  const startIso = rec?.inspection?.start_ns ? new Date(rec.inspection.start_ns / 1e6).toISOString() : null;
  const suggested: Any[] = rec?.suggested?.topics || [];
  const mappedTopic = (topic: string) => suggested.find((t: Any) => t.topic === topic);

  const fixedReading = !!rec && !rec.suggested;
  function mapping(): Any {
    if (fixedReading) return {};
    let base: Any;
    try { base = JSON.parse(json); } catch { throw new Error("The mapping JSON is not valid"); }
    return { ...base, topics: (base.topics || []).filter((t: Any) => !off.has(t.topic)) };
  }

  async function debrief() {
    setError("");
    try {
      if (!f.machine_id.trim() || !f.title.trim()) throw new Error("Give the machine id and a short title");
      const pf = seconds(f.problem_from), pt = seconds(f.problem_to), nf = seconds(f.normal_from), nt = seconds(f.normal_to);
      for (const [v, name] of [[pf, "Problem from"], [pt, "Problem to"], [nf, "Normal from"], [nt, "Normal to"]] as const) {
        if (Number.isNaN(v)) throw new Error(`${name}: write minutes:seconds (1:05) or seconds (65)`);
      }
      if ((pf == null) !== (pt == null)) throw new Error("Give both ends of the problem, or neither");
      if ((nf == null) !== (nt == null)) throw new Error("Give both ends of the normal period, or neither");
      const body: Any = { mapping: mapping(), machine_id: f.machine_id.trim(), title: f.title.trim(), machine_class: f.machine_class || null,
        problem_from_s: pf, problem_to_s: pt, normal_from_s: nf, normal_to_s: nt, normal_basis: f.normal_basis || null, synthetic: f.synthetic, allow_model: !!f.allow_model };
      if (f.stated_reason.trim()) body.report = { stated_reason: f.stated_reason.trim(), symptom: f.symptom || null, affected_function: f.affected.trim() || null, at_s: pf };
      setBusy("Starting");
      const { job_id } = await call(`/engine/recordings/${rec.id}/debrief`, { method: "POST", body: JSON.stringify(body) });
      for (;;) {
        await sleep(1000);
        const job = await call(`/engine/jobs/${job_id}`);
        if (job.state === "failed") throw new Error(job.error);
        if (job.case_id || job.state === "done") { onDone(job.case_id || job.result.case_id, job.state === "running" ? job_id : undefined); return; }  // the investigation continues on the case page
        setBusy(job.step);
      }
    } catch (e: Any) { setError(e.message); setBusy(""); }
  }

  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-labelledby="rec-title">
      <div className={s.dialog}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 12 }}>
          <div>
            <h2 id="rec-title" style={{ margin: 0 }}>Upload data</h2>
            <p className={s.lede} style={{ margin: "4px 0 0" }}>Robot recordings (.bag, .mcap, zipped ROS 2, PX4 .ulg), console or system logs (.log, .txt), or tables (.csv, .json). Stays in your workspace.</p>
          </div>
          <button className={s.quiet} onClick={onClose} aria-label="Close" disabled={!!busy && !rec}>Close</button>
        </div>

        {!rec && <>
          <label className={s.field} style={{ marginTop: 16 }}><span>File</span>
            <input type="file" multiple accept=".bag,.mcap,.zip,.ulg,.log,.txt,.out,.console,.csv,.json,.jsonl,.ndjson" onChange={(e) => {
              const picked = Array.from(e.target.files || []); setError("");
              if (picked.length && picked.every((x) => TABULAR.test(x.name))) { onTabular(picked); return; }
              if (picked.length > 1) { setFile(null); setError("Upload one recording at a time, or several CSV / JSON files together"); return; }
              setFile(picked[0] || null);
            }} disabled={!!busy} /></label>
          {file && <p className={s.note}>{file.name} · {(file.size / 1e6).toFixed(1)} MB</p>}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center", marginTop: 16 }}>
            {busy && <span className={s.note} style={{ margin: 0 }}>{busy}{pct != null && pct < 100 ? ` ${pct}%` : "…"}</span>}
            <button className={s.button} onClick={upload} disabled={!file || !!busy}>Upload and read</button>
          </div>
          <RealSamples busy={!!busy} setBusy={setBusy} setError={setError} onDone={onDone} />
        </>}

        {rec && fixedReading && <>
          <p className={s.answerSmall} style={{ marginTop: 14 }}>
            <b>{FORMAT_LABEL[rec.inspection.format] || rec.inspection.format}</b>
            {rec.inspection.format === "text_log"
              ? <> · {rec.inspection.lines.toLocaleString()} lines · {rec.inspection.clock === "line_order" ? "no timestamps (line order only)" : `${rec.inspection.clock} timestamps`}</>
              : <> · {rec.inspection.topics.length} topics · {rec.inspection.message_count.toLocaleString()} messages</>}
          </p>
          {(rec.inspection.summary || []).length > 0 && <ul className={s.factList}>{rec.inspection.summary.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul>}
          <div className={s.formGrid}>
            <label className={s.field}><span>Machine id</span><input value={f.machine_id} onChange={set("machine_id")} placeholder="e.g. CF-LAB-12" /></label>
            <label className={s.field}><span>Machine type</span>
              <select value={f.machine_class} onChange={set("machine_class")}>
                <option value="">Not sure (generic)</option>
                {types.map((t: Any) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select></label>
            <label className={s.field} style={{ gridColumn: "1 / -1" }}><span>Short title</span><input value={f.title} onChange={set("title")} /></label>
            <label className={s.field} style={{ gridColumn: "1 / -1" }}><span>What happened, and what you already checked (optional)</span>
              <input value={f.stated_reason} onChange={set("stated_reason")} placeholder="Restarted in flight. Stack overflow already ruled out." /></label>
            <label className={s.field}><span>Symptom</span>
              <select value={f.symptom} onChange={set("symptom")}>{SYMPTOM_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className={s.field}><span>Which part (optional)</span><input value={f.affected} onChange={set("affected")} placeholder="flight" /></label>
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, fontSize: 13 }}>
            <input type="checkbox" checked={f.synthetic} onChange={(e) => setF({ ...f, synthetic: e.target.checked })} /> Test or simulated data</label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, fontSize: 13 }}>
            <input type="checkbox" checked={!!f.allow_model} onChange={(e) => setF({ ...f, allow_model: e.target.checked })} /> Let Veyra's AI investigator read this data (sent to the AI model configured on this instance)</label>
          {error && <p className={s.warning} role="alert">{error}</p>}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center", marginTop: 16, flexWrap: "wrap" }}>
            {busy && <span className={s.note} style={{ margin: 0 }} aria-live="polite">{busy}…</span>}
            <button className={s.button} onClick={debrief} disabled={!!busy}>Investigate</button>
          </div>
        </>}

        {rec && !fixedReading && <>
          <p className={s.answerSmall} style={{ marginTop: 14 }}>
            <b>{FORMAT_LABEL[rec.inspection.format] || rec.inspection.format}</b> · {clock(duration)} long · {rec.inspection.topics.length} topics · {rec.inspection.message_count.toLocaleString()} messages
            {startIso && <> · starts {startIso.slice(0, 19).replace("T", " ")} UTC</>}
          </p>
          <h3 style={{ margin: "14px 0 4px", fontSize: 14 }}>What to read</h3>
          <p className={s.note} style={{ marginTop: 0 }}>Picked from standard ROS types. Untick to skip.</p>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead><tr><th></th><th>Topic</th><th style={{ textAlign: "right" }}>Rate</th><th>Read as</th></tr></thead>
              <tbody>{rec.inspection.topics.map((t: Any) => {
                const m = mappedTopic(t.topic);
                return (
                  <tr key={t.topic}>
                    <td>{m && <input type="checkbox" aria-label={`Read ${t.topic}`} checked={!off.has(t.topic)} onChange={(e) => {
                      const next = new Set(off); if (e.target.checked) next.delete(t.topic); else next.add(t.topic); setOff(next); }} />}</td>
                    <td><span className={s.mono} style={{ fontSize: 12 }}>{t.topic}</span><br /><span className={s.dim} style={{ fontSize: 11 }}>{t.type}</span></td>
                    <td className={s.num}>{t.rate_hz != null ? `${t.rate_hz} Hz` : "—"}</td>
                    <td style={{ fontSize: 12 }}>{m ? [...new Set(m.entries.map((e: Any) => e.signal))].join(", ") : <span className={s.dim}>not read</span>}</td>
                  </tr>);
              })}</tbody>
            </table>
          </div>
          <details className={s.done}>
            <summary>Custom message types (JSON)</summary>
            <textarea rows={10} style={{ width: "100%", marginTop: 8, fontFamily: "var(--sr-font-data)", fontSize: 12 }} value={json} onChange={(e) => setJson(e.target.value)} />
            <p className={s.note}>Entries: series (field, scope or scope_from, signal, unit), attitude (quaternion field), event (on change), diagnostics, arrivals. Readings are kept at up to {rec.suggested.max_hz} Hz per signal.</p>
          </details>

          <div className={s.formGrid}>
            <label className={s.field}><span>Machine id</span><input value={f.machine_id} onChange={set("machine_id")} placeholder="e.g. ANYmal-03" /></label>
            <label className={s.field}><span>Machine type</span>
              <select value={f.machine_class} onChange={set("machine_class")}>
                <option value="">Not sure (generic)</option>
                {types.map((t: Any) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select></label>
            <label className={s.field} style={{ gridColumn: "1 / -1" }}><span>Short title</span><input value={f.title} onChange={set("title")} /></label>
            <label className={s.field} style={{ gridColumn: "1 / -1" }}><span>What was noticed (optional)</span>
              <input value={f.stated_reason} onChange={set("stated_reason")} placeholder="Robot started limping on the left front leg" /></label>
            <label className={s.field}><span>Symptom</span>
              <select value={f.symptom} onChange={set("symptom")}>{SYMPTOM_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className={s.field}><span>Which part (optional)</span><input value={f.affected} onChange={set("affected")} placeholder="left front leg" /></label>
          </div>
          <div className={s.formGrid}>
            <label className={s.field}><span>Problem from (min:sec)</span><input value={f.problem_from} onChange={set("problem_from")} placeholder="1:05" /></label>
            <label className={s.field}><span>Problem to</span><input value={f.problem_to} onChange={set("problem_to")} placeholder={clock(duration)} /></label>
            <label className={s.field}><span>Normal from</span><input value={f.normal_from} onChange={set("normal_from")} /></label>
            <label className={s.field}><span>Normal to</span><input value={f.normal_to} onChange={set("normal_to")} /></label>
            <label className={s.field} style={{ gridColumn: "1 / -1" }}><span>Why it was normal</span><input value={f.normal_basis} onChange={set("normal_basis")} /></label>
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, fontSize: 13 }}>
            <input type="checkbox" checked={f.synthetic} onChange={(e) => setF({ ...f, synthetic: e.target.checked })} />
            Test or simulated data{/synthetic|simulat|\bsim\b|fake|test/i.test(file?.name || "") ? <span className={s.dim}> (ticked because of the file name)</span> : null}</label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, fontSize: 13 }}>
            <input type="checkbox" checked={!!f.allow_model} onChange={(e) => setF({ ...f, allow_model: e.target.checked })} /> Let Veyra's AI investigator read this data (sent to the AI model configured on this instance)</label>
          <p className={s.note}>Times from the start of the recording, 0:00 to {clock(duration)}.</p>
          {error && <p className={s.warning} role="alert">{error}</p>}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center", marginTop: 16, flexWrap: "wrap" }}>
            {busy && <span className={s.note} style={{ margin: 0 }} aria-live="polite">{busy}…</span>}
            <button className={s.button} onClick={debrief} disabled={!!busy}>Create the debrief</button>
          </div>
        </>}
        {!rec && error && <p className={s.warning} role="alert">{error}</p>}
      </div>
    </div>
  );
}

function ShareDialog({ caseId, title, onClose }: { caseId: string; title: string; onClose: () => void }) {
  const [links, setLinks] = useState<Any[]>([]);
  const [days, setDays] = useState(14);
  const [fresh, setFresh] = useState<Any>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(() => call(`/engine/cases/${caseId}/shares`).then((d) => setLinks(d.shares)).catch((e) => setError(e.message)), [caseId]);
  useEffect(() => { refresh(); }, [refresh]);
  const absolute = !fresh ? "" : API.startsWith("http") ? `${API}${fresh.path}` : `${window.location.origin}${API}${fresh.path}`;

  async function create() {
    setError(""); setCopied(false);
    try { setFresh(await call(`/engine/cases/${caseId}/shares`, { method: "POST", body: JSON.stringify({ days }) })); await refresh(); }
    catch (e: Any) { setError(e.message); }
  }
  async function revoke(id: string) {
    try { await call(`/engine/shares/${id}`, { method: "DELETE" }); if (fresh?.id === id) setFresh(null); await refresh(); }
    catch (e: Any) { setError(e.message); }
  }

  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-labelledby="share-title">
      <div className={s.dialog} style={{ maxWidth: 560 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 12 }}>
          <div>
            <h2 id="share-title" style={{ margin: 0 }}>Share the report</h2>
            <p className={s.lede} style={{ margin: "4px 0 0" }}>Anyone with the link can read the report. They can't change it.</p>
          </div>
          <button className={s.quiet} onClick={onClose} aria-label="Close">Close</button>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "end", marginTop: 14, flexWrap: "wrap" }}>
          <label className={s.field}><span>Link works for</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} day{d === 1 ? "" : "s"}</option>)}</select></label>
          <button className={s.button} onClick={create}>Create link</button>
        </div>
        {fresh && <div className={s.nextBox} style={{ marginTop: 14, display: "block" }}>
          <p className={s.answerSmall} style={{ margin: 0 }}>Copy it now. It's shown once.</p>
          <input readOnly value={absolute} style={{ width: "100%", marginTop: 6, fontFamily: "var(--sr-font-data)", fontSize: 12 }} onFocus={(e) => e.target.select()} />
          <button className={s.quiet} style={{ marginTop: 8 }} onClick={async () => { try { await navigator.clipboard.writeText(absolute); setCopied(true); } catch { setError("Copy failed: select the link and copy it"); } }}>{copied ? "Copied" : "Copy link"}</button>
        </div>}
        {links.length > 0 && <>
          <h3 style={{ margin: "16px 0 4px", fontSize: 14 }}>Links for this case</h3>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>{links.map((l: Any) => (
            <li key={l.id} className={s.answerSmall} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span className={s.mono}>{l.id}</span> by {l.created_by} · until {l.expires_at.slice(0, 16).replace("T", " ")} UTC ·{" "}
              {l.active ? <button className={s.link} onClick={() => revoke(l.id)}>Revoke</button> : <span className={s.dim}>{l.revoked ? "revoked" : "expired"}</span>}
            </li>))}</ul>
        </>}
        {error && <p className={s.warning} role="alert">{error}</p>}
      </div>
    </div>
  );
}


/** One click: a real failure log from a public issue thread, through the same upload path and the investigator. */
function RealSamples({ busy, setBusy, setError, onDone }: { busy: boolean; setBusy: (s: string) => void; setError: (s: string) => void; onDone: (caseId: string, jobId?: string) => void }) {
  const [list, setList] = useState<Any[]>([]);
  useEffect(() => { call("/engine/samples").then(setList).catch(() => setList([])); }, []);
  if (!list.length) return null;
  async function run(name: string) {
    setError(""); setBusy("Starting");
    try {
      const { job_id } = await call(`/engine/samples/${name}/run`, { method: "POST", body: JSON.stringify({ allow_model: true }) });
      for (;;) {
        await sleep(700);
        const job = await call(`/engine/jobs/${job_id}`);
        if (job.state === "failed") throw new Error(job.error);
        if (job.case_id || job.state === "done") { onDone(job.case_id || job.result.case_id, job.state === "running" ? job_id : undefined); return; }
        setBusy(job.step);
      }
    } catch (e: Any) { setError(e.message); setBusy(""); }
  }
  return (
    <div className={s.sampleRow}>
      <span>No file at hand? Open a real failure log from a public issue:</span>
      {list.map((x) => <button key={x.name} className={s.quiet} disabled={busy} onClick={() => run(x.name)} title={`${x.platform}. Source: ${x.source_url}`}>{x.title}</button>)}
    </div>
  );
}
