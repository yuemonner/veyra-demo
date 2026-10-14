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
    return { ok: true, late_evidence: "additional runtime trace", event_time: "2026-09-03T14:09:00Z", known_at: "2026-09-03T14:31:00Z" };
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
    const affected = 3;
    return {
      same_change: 12,
      same_signal: affected,
      no_signal: 9,
      table: [
        { context: "Autonomy 2.7", affected: `${affected}/${affected}`, unaffected: "9/9" },
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

  if (path.endsWith("/evidence-lineage")) {
    return {
      rule: "Evidence can support a claim only when it has explicit reference, exact id, human verification or stated inference. Temporal proximity alone is never causation.",
      evidence: demoEvidenceLineage(),
    };
  }

  if (path.endsWith("/hypotheses")) {
    return {
      belief_snapshot: { known_at: "2026-09-03T14:27:00Z", immutable: true },
      unresolved_uncertainty: ["L4 alone vs L4 plus Zone B", "localization-related vs unknown interaction"],
      hypotheses: demoHypotheses().map((item) => ({
        ...item,
        supporting_evidence: item.evidence_refs.slice(0, 2),
        contradicting_evidence: item.contradicting_refs,
        missing_evidence: item.missing_evidence,
        would_change_conclusion: item.would_change_conclusion,
      })),
    };
  }

  if (path.endsWith("/diagnostic-checks")) {
    return {
      ranking_method: "Deterministic heuristic: information value, cost, operational risk and whether the check changes physical state.",
      checks: demoChecks(),
    };
  }

  if (path.includes("/diagnostic-checks/") && path.endsWith("/observation")) {
    return {
      ...demoChecks()[0],
      status: "observed",
      actual_observation: readBodyField(body, "observation") || body,
      belief_update: { "h-l4-zone-b": "strengthened", "h-l4-alone": "still_unresolved" },
    };
  }

  if (path.endsWith("/replay")) {
    return {
      frames: [
        { time: "14:11", known_evidence_ids: ["EX03-safe-stop"], future_evidence_hidden: false },
        { time: "14:23", known_evidence_ids: ["EX03-safe-stop", "EX03-localization-map-update"], future_evidence_hidden: false },
        { time: "14:27", known_evidence_ids: ["EX03-safe-stop", "EX05-safe-stop", "EX08-safe-stop"], future_evidence_hidden: true },
        { time: "14:31", known_evidence_ids: ["EX11-delayed-safe-stop"], future_evidence_hidden: false },
      ],
      evaluation: {
        evidence_linkage_correctness: "prototype_checked",
        unsupported_ai_claims: [],
        possible_cause_coverage: "3 competing possible causes maintained",
        suggested_checks_usefulness: "EX11 check separates strongest remaining possible causes",
        precedent_applicability: "partially_applicable",
        decision_traceability: "decision snapshot preserves what was knowable at 14:27",
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
      applicability: {
        status: demoState.outcomeRecorded ? "partially_applicable" : "insufficient_evidence",
        why: "Similar signature, but unresolved confounders remain.",
        reuse_warning: "Use as a starting precedent, not an automatic fix.",
      },
      outcome_comparison: [
        { action: "rollback_localization", outcome: demoState.outcomeRecorded ? "returned to service" : "pending", attribution: demoState.outcomeRecorded ? "observed after action" : "not observed" },
        { action: "observe_EX11_zone_B", outcome: "not executed", attribution: "highest information value" },
        { action: "dispatch", outcome: "not needed in this replay", attribution: "not executed" },
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
      actual_action: demoState.decisionRecorded ? { label: "Rollback localization L4", scope: { rollback_localization: ["EX03", "EX05", "EX08"] } } : null,
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
    {
      id: "h-l4-alone",
      hypothesis: "Localization L4",
      status: "possible",
      confidence: 0.58,
      evidence_refs: ["EX03-localization-map-update", "EX05-localization-map-update", "EX08-localization-map-update"],
      contradicting_refs: ["EX11-localization-map-update"],
      missing_evidence: ["EX11 behavior in Zone B"],
      would_change_conclusion: ["multiple healthy L4 machines remain stable in Zone B"],
    },
    {
      id: "h-l4-zone-b",
      hypothesis: "Localization L4 + Zone B",
      status: "strong",
      confidence: 0.76,
      evidence_refs: ["EX03-safe-stop", "EX05-safe-stop", "EX08-safe-stop"],
      contradicting_refs: ["EX11-localization-map-update"],
      missing_evidence: ["healthy L4 machine observed in Zone B"],
      would_change_conclusion: ["EX11 remains healthy in Zone B"],
    },
    {
      id: "h-lidar-53",
      hypothesis: "LiDAR firmware 5.3",
      status: "weak",
      confidence: 0.28,
      evidence_refs: ["EX03-localization-map-update"],
      contradicting_refs: ["EX02-localization-map-update", "EX09-localization-map-update"],
      missing_evidence: ["No useful next check"],
      would_change_conclusion: ["LiDAR 5.3 healthy cohort shows same safe-stop pattern"],
    },
  ];
}

function demoEvidenceLineage() {
  return [
    { id: "EX03-safe-stop", source_id: "mission_runtime:EX03", source_type: "mission_runtime", asset_id: "EX03", original_reference: "mission_runtime/EX03/safe_stop/14:11", event_time: "2026-09-03T14:11:00Z", known_at: "2026-09-03T14:12:00Z", ingested_at: "2026-09-03T14:12:08Z", linkage_method: "exact_id", verification_state: "verified", evidence_class: "OBSERVED" },
    { id: "EX05-safe-stop", source_id: "mission_runtime:EX05", source_type: "mission_runtime", asset_id: "EX05", original_reference: "mission_runtime/EX05/safe_stop/14:18", event_time: "2026-09-03T14:18:00Z", known_at: "2026-09-03T14:19:00Z", ingested_at: "2026-09-03T14:19:08Z", linkage_method: "exact_id", verification_state: "verified", evidence_class: "OBSERVED" },
    { id: "EX08-safe-stop", source_id: "mission_runtime:EX08", source_type: "mission_runtime", asset_id: "EX08", original_reference: "mission_runtime/EX08/safe_stop/14:26", event_time: "2026-09-03T14:26:00Z", known_at: "2026-09-03T14:27:00Z", ingested_at: "2026-09-03T14:27:08Z", linkage_method: "exact_id", verification_state: "verified", evidence_class: "OBSERVED" },
  ];
}

function demoChecks() {
  return [
    {
      id: "check-ex11-zone-b",
      question: "Does EX11 safe-stop when observed in loading zone B?",
      check_type: "read_only",
      rank_score: 0.91,
      procedure: { asset_id: "EX11", route_segment: "loading-zone-B", do_not_modify_machine: true },
      expected_observations: { if_safe_stop: "Cause 2 strengthens", if_healthy: "Cause 2 weakens" },
      estimated_cost: { time_minutes: 15, operator_attention: "low" },
      operational_risk: { level: "low" },
      why_informative: "It separates L4 alone from L4 plus Zone B with one low-risk observation.",
      status: "recommended",
    },
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

export async function uploadFile<T>(path: string, file: File, description = ""): Promise<T> {
  if (!API) {
    throw new Error("No backend API is configured. The page can still run a local browser preview.");
  }
  const form = new FormData();
  form.append("file", file);
  form.append("description", description);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  const res = await fetch(`${API}${path}`, { method: "POST", body: form, signal: controller.signal });
  clearTimeout(timeout);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
