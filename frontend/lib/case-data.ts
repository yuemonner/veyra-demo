// Read-through views may be stale; mutations are never cached or replayed.
export async function fetchCaseJSON(base: string, path: string, body?: unknown, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${base}${path}`, {cache: "no-store", signal: controller.signal, ...(body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})});
    if (!response.ok) {
      const raw = await response.text();
      let detail = `Live service unavailable (HTTP ${response.status}). Please retry.`;
      try { const parsed = JSON.parse(raw); if (typeof parsed.detail === "string") detail = parsed.detail.slice(0,240); } catch { /* Never render proxy HTML or stack traces. */ }
      throw Object.assign(new Error(detail), {status: response.status});
    }
    try { return await response.json(); } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new Error("Live service returned an unexpected response. Please retry.");
    }
  } catch (error) {
    if (controller.signal.aborted) throw new Error(body === undefined ? "Live connection timed out. The saved view remains available." : "Response timed out; save status is unknown. Reload records before retrying.");
    throw error;
  } finally { clearTimeout(timer); }
}

export function readCaseCache(storage: Pick<Storage, "getItem">, caseId: string) {
  try {
    const cached = JSON.parse(storage.getItem(`veyra-case-view-v1:${caseId}`) || "null");
    if (cached?.version !== 1 || cached.analysis?.investigation_id !== caseId || !cached.saved_at || !Array.isArray(cached.analysis?.cohorts) || !Array.isArray(cached.analysis?.hypotheses)) return null;
    return cached;
  } catch { return null; }
}
