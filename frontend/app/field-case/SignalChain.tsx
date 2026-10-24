"use client";
// Where an anomaly was measured in the machine's signal chain, and which mechanisms the recorded evidence leaves open.
// A signal is not a cause: the chain shows the measurement point, what came after it (consequences, not more evidence),
// and each mechanism's status with the record behind it. Data: GET .../questions -> causal (engine signal-chain layer).
import s from "./field-case.module.css";

type Any = any; // engine records are versioned JSON

const STATUS: Record<string, { label: string; cls: string }> = {
  supported: { label: "Evidence points here", cls: "mSupported" },
  possible: { label: "Still possible", cls: "mPossible" },
  cannot_tell: { label: "Can't tell from this data", cls: "mUnknown" },
  unlikely: { label: "Unlikely", cls: "mUnlikely" },
};

export function SignalChain({ causal, compact = false, domains }: { causal: Any; compact?: boolean; domains?: Record<string, Any> }) {
  if (!causal?.applicable || !causal.events?.length) return null;
  const ev = causal.events[0];
  const consequences = (ev.members || []).filter((m: Any) => m.relation === "consequence");
  const marks: Record<string, "lead" | "after"> = { [ev.lead.point]: "lead" };
  consequences.forEach((m: Any) => { if (!marks[m.point]) marks[m.point] = "after"; });
  const mechs: Any[] = compact ? ev.mechanisms.filter((m: Any) => m.status !== "possible" || ev.mechanisms.every((x: Any) => x.status === "possible")) : ev.mechanisms;
  return (
    <div className={s.chainWrap}>
      <ol className={s.chain} aria-label="Signal chain, from operator input to machine motion">
        {causal.chain.map((p: Any) => {
          const mark = marks[p.id];
          const seen = p.in_case || p.in_recording;
          return (
            <li key={p.id} className={`${s.chainPoint} ${mark === "lead" ? s.chainLead : mark === "after" ? s.chainAfter : ""}`}>
              <span className={`${s.chainNode} ${seen ? s.chainSeen : ""}`} aria-hidden="true" />
              <span className={s.chainLabel}>{p.label}</span>
              <span className={s.chainState}>
                {mark === "lead" ? "measured here first" : mark === "after" ? "followed" : seen ? "recorded" : "not recorded"}
              </span>
            </li>
          );
        })}
      </ol>
      <p className={s.answerSmall} style={{ margin: "10px 0 0" }}>{ev.summary}</p>
      {consequences.length > 0 && <p className={s.dim} style={{ margin: "4px 0 0", fontSize: 13 }}>
        Not counted as separate evidence: {consequences.map((m: Any) => `${m.text.split(" from ")[0].split(":")[0]} (${m.dt_s > 0 ? "+" : ""}${m.dt_s} s)`).join("; ")}. It is what the first change would cause.
      </p>}
      {ev.routing?.differs && domains && <p className={s.routingNote}>
        Routing check: triage proposed {(ev.routing.triage_domains || []).map((d: string) => domains[d]?.owner || d).join(" and ")}, from where the signals were measured.
        {ev.routing.points_to.length > 0 ? <> The recorded evidence points to <b>{ev.routing.points_to.map((d: string) => domains[d]?.owner || d).join(" and ")}</b>.</> : null}
        {" "}Confirm the owner before dispatching.
      </p>}
      <ul className={s.mechList}>
        {mechs.map((m: Any) => {
          const st = STATUS[m.status] || STATUS.possible;
          const why = (m.evidence || []).find((e: Any) => e.bearing !== "none") || m.evidence?.[0];
          return (
            <li key={m.id}>
              <span className={`${s.mechStatus} ${s[st.cls]}`}>{st.label}</span>
              <span><b>{m.label}</b>{why ? <><br /><span className={s.dim}>{why.text}</span></> : null}</span>
            </li>
          );
        })}
      </ul>
      {compact && mechs.length < ev.mechanisms.length && <p className={s.dim} style={{ margin: "6px 0 0", fontSize: 13 }}>
        {ev.mechanisms.length - mechs.length} other mechanism{ev.mechanisms.length - mechs.length === 1 ? "" : "s"} still possible; nothing recorded separates {ev.mechanisms.length - mechs.length === 1 ? "it" : "them"}.
      </p>}
    </div>
  );
}

export function RecordingRead({ d }: { d: Any }) {
  if (!d) return null;
  const roles = Object.entries(d.roles || {}).filter(([r]) => r !== "null").map(([r, n]) => `${String(r).replace(/_/g, " ")} ${n}`).join(", ");
  return (
    <details className={s.recordingRead}>
      <summary>Read from the original recording: {d.recording} · {d.streams} streams understood{d.not_understood.length ? `, ${d.not_understood.length} not` : ""}</summary>
      <p className={s.dim} style={{ margin: "6px 0 0", fontSize: 13 }}>By role: {roles}.</p>
      {d.excluded.length > 0 && <p className={s.dim} style={{ margin: "4px 0 0", fontSize: 13 }}>
        Never used as evidence: {d.excluded.map((e: Any) => e.stream).join(", ")} (label or ground-truth streams).</p>}
      {d.not_understood.length > 0 && <p className={s.dim} style={{ margin: "4px 0 0", fontSize: 13 }}>
        Not understood (type does not say what it measures): {d.not_understood.map((e: Any) => e.stream).join(", ")}.</p>}
      {d.gaps.length > 0 && <ul className={s.factList} style={{ marginTop: 6 }}>
        {d.gaps.map((g: Any, i: number) => <li key={i}><b>Not in the recording: {g.missing}.</b> <span className={s.dim}>{g.means}</span></li>)}
      </ul>}
      {d.sha256 && <p className={s.note} style={{ marginTop: 6 }}>sha256 {d.sha256.slice(0, 16)}… · {d.version}</p>}
    </details>
  );
}
