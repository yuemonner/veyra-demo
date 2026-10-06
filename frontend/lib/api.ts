export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

const DEMO_ID = "inv-120-robots-bad-rollout";

type DemoState = {
  lateEvidence: boolean;
  decisionRecorded: boolean;
  outcomeRecorded: boolean;
  packageId: string;
};

const demoState: DemoState = {
  lateEvidence: false,
  decisionRecorded: false,
  outcomeRecorded: false,
  packageId: "pkg-demo-fc021",
};

function nowIso() {
  return "2026-09-03T14:31:00Z";
}

function fallbackResponse(path: string, method: string, body?: unknown): unknown {
  if (path === "/demo/reset") {
    demoState.lateEvidence = false;
    demoState.decisionRecorded = false;
    demoState.outcomeRecorded = false;
    return { ok: true, investigation_id: DEMO_ID, affected: 3 };
  }

  if (path === "/demo/late-evidence") {
    demoState.lateEvidence = true;
    return { ok: true, late_evidence: "EX11", event_time: "2026-09-03T14:09:00Z", known_at: "2026-09-03T14:31:00Z" };
  }

  if (path.endsWith("/reconstruct")) {
    return {
      investigation_id: DEMO_ID,
      title: "FC 021 · Unexpected safe stop after autonomy 2.7",
      last_known_healthy: { event_time: "2026-09-03T14:02:00Z" },
      first_abnormal_evidence: { event_time: "2026-09-03T14:11:08Z", payload: { signal: "safe-stop near loading zone B" } },
      human_discovery: { event_time: "2026-09-03T14:31:00Z" },
      changes: [
        { label: "Autonomy stack", from: "2.6", to: "2.7" },
        { label: "Localization", from: "L3", to: "L4" },
        { label: "LiDAR firmware", from: "5.2", to: "5.3" },
        { label: "Map", from: "M18", to: "M19" },
      ],
    };
  }

  if (path.endsWith("/comparison")) {
    const affected = demoState.lateEvidence ? 4 : 3;
    return {
      same_change: 12,
      same_signal: affected,
      no_signal: demoState.lateEvidence ? 8 : 9,
      table: [
        { context: "Autonomy 2.7", affected: `${affected}/${affected}`, unaffected: `${demoState.lateEvidence ? 8 : 9}/${demoState.lateEvidence ? 8 : 9}` },
        { context: "Localization profile L4", affected: `${affected}/${affected}`, unaffected: "2/9" },
        { context: "LiDAR firmware 5.3", affected: `${affected}/${affected}`, unaffected: "7/9" },
        { context: "Map M19", affected: `${affected}/${affected}`, unaffected: "5/9" },
        { context: "Loading zone B", affected: `${affected}/${affected}`, unaffected: "1/9" },
      ],
    };
  }

  if (path.endsWith("/decision-context")) {
    return {
      decision_records: demoState.decisionRecorded ? [{
        owner: "Operations Lead",
        chosen_option: { label: "Rollback localization L4 on EX03, EX05 and EX08" },
        hypotheses: demoHypotheses(),
      }] : [],
      options_considered: demoOptions(),
      observability_state: {
        status: "partial",
        connected_sources: ["release_manifest", "robot_config_registry", "mission_runtime", "operator_review"],
        completeness: { mission_runtime: demoState.lateEvidence ? "updated" : "partial" },
      },
    };
  }

  if (path.endsWith("/decision-package")) {
    return demoPackage(false);
  }

  if (path.includes("/decision-packages/") && path.endsWith("/seal")) {
    demoState.decisionRecorded = true;
    return demoPackage(true);
  }

  if (path.includes("/decision-packages/") && path.endsWith("/verify")) {
    return {
      valid: true,
      digest_matches: true,
      signature_valid: true,
      trusted_timestamp: "2026-09-03T14:27:00Z",
      timestamp_authority: "Veyra demo timestamp authority",
      verification_mode: "local-demo",
    };
  }

  if (path.endsWith("/decision")) {
    demoState.decisionRecorded = true;
    return { id: "dec-demo-fc021", investigation_id: DEMO_ID, decision: readBodyField(body, "decision") || "Rollback localization L4 on affected machines", owner: readBodyField(body, "owner") || "Operations Lead", decided_at: nowIso() };
  }

  if (path.endsWith("/outcome")) {
    demoState.outcomeRecorded = true;
    return { id: "out-demo-fc021", investigation_id: DEMO_ID, outcome: readBodyField(body, "outcome") || "All 3 affected machines recovered after rollback.", recorded_at: nowIso() };
  }

  if (path.startsWith("/memory/similar")) {
    return {
      similar_cases: demoState.outcomeRecorded ? [{ id: "FC 021", action: "Rollback localization L4", outcome: "Recovered after rollback", days_ago: 12 }] : [],
      precedent_comparison: {
        outcome_validation: demoState.outcomeRecorded ? { status: "observed", attribution: "observed after action, not causal proof" } : { status: "pending" },
      },
    };
  }

  if (path.startsWith("/precedents/compare")) {
    return {
      outcome_validation: demoState.outcomeRecorded ? { status: "observed", attribution: "precedent, not causal proof" } : { status: "pending" },
      outcome_comparison: [
        { action: "rollback_localization", outcome: demoState.outcomeRecorded ? "returned to service" : "pending", attribution: demoState.outcomeRecorded ? "observed after action" : "not observed" },
        { action: "observe_EX11_zone_B", outcome: "not executed", attribution: "highest information value" },
        { action: "dispatch", outcome: "avoided", attribution: "not executed" },
      ],
    };
  }

  if (path === "/assets") {
    return Array.from({ length: 12 }, (_, index) => ({ id: `EX${String(index + 1).padStart(2, "0")}`, asset_type: "Autonomous field robot" }));
  }

  return { ok: true, fallback: true, path, method };
}

function demoPackage(sealed: boolean) {
  return {
    id: demoState.packageId,
    sealed,
    digest: "4e82893a2232dd6442a9de73c81a7f9c8db7d4af",
    signature: sealed ? "demo-signature" : undefined,
    public_key: sealed ? "demo-public-key" : undefined,
    package: {
      id: demoState.packageId,
      investigation_id: DEMO_ID,
      human_decision: demoState.decisionRecorded ? { owner: "Operations Lead", decision: "Rollback localization L4 on EX03, EX05 and EX08" } : null,
      planned_action: { label: "Observe EX11 in Zone B" },
      actual_action: demoState.decisionRecorded ? { label: "Rollback localization L4", scope: { remote_recovery: ["EX03", "EX05", "EX08"] } } : null,
      hypotheses: demoHypotheses(),
      primary_hypothesis: demoHypotheses()[1],
      options_considered: demoOptions(),
      what_was_unknown: ["EX11 behavior in Zone B", "whether L4 alone caused safe-stop", "whether L4 interacted with Zone B"],
      missing_evidence: ["EX11 behavior in Zone B", "healthy L4 machine in Zone B"],
      observability_state: { status: "partial" },
      decision_substantiation: {
        question: "Can the team act now?",
        summary: "Enough evidence to choose an operational action. Not enough evidence to declare root cause.",
      },
      outcome_validation: demoState.outcomeRecorded ? { status: "observed", attribution: "observed after action, not causal proof" } : { status: "pending" },
    },
  };
}

function demoHypotheses() {
  return [
    { hypothesis: "Localization L4", status: "possible", confidence: 0.58 },
    { hypothesis: "Localization L4 + Zone B", status: "strong", confidence: 0.76 },
    { hypothesis: "LiDAR firmware 5.3", status: "weak", confidence: 0.28 },
  ];
}

function demoOptions() {
  return [
    {
      id: "opt-observe-ex11",
      option_type: "observe_EX11",
      label: "Observe EX11 in Zone B",
      expected_cost: { cost: "low", time_minutes: 15, risk: "low" },
      expected_risk: { reason: "Separates L4 alone from L4 + Zone B without changing a production machine" },
      historical_support: { status: "best_next_check", summary: "Highest information value" },
    },
    {
      id: "opt-rollback-l4",
      option_type: "rollback",
      label: "Roll back localization L4 on affected machines",
      expected_cost: { cost: "medium", disruption: "medium" },
      expected_risk: { reason: "Fast recovery but less clean as a diagnostic step" },
      historical_support: { status: demoState.outcomeRecorded ? "observed" : "pending", summary: demoState.outcomeRecorded ? "Returned to service after rollback" : "No outcome linked yet" },
      selected: demoState.decisionRecorded,
    },
    {
      id: "opt-dispatch",
      option_type: "dispatch",
      label: "Dispatch technician",
      expected_cost: { estimated_cost_usd: 1200, field_visit: true },
      expected_risk: { reason: "Current evidence does not justify the visit yet" },
      historical_support: { status: "not_supported", summary: "No hardware fault confirmed" },
    },
  ];
}

function readBodyField(body: unknown, key: string): string | undefined {
  if (!body || typeof body !== "object") return undefined;
  const value = (body as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

function summarizeFallbackWarning(error: unknown) {
  if (error instanceof Error) return error.message;
  return "backend unavailable";
}

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;

  if (!API) {
    return fallbackResponse(path, method, body) as T;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    const res = await fetch(`${API}${path}`, { ...init, cache: "no-store", signal: controller.signal });
    clearTimeout(timeout);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  } catch (error) {
    console.warn(`[Veyra demo] using local fallback for ${method} ${path}: ${summarizeFallbackWarning(error)}`);
    return fallbackResponse(path, method, body) as T;
  }
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return getJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}
