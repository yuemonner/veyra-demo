"use client";

// Says plainly which of Veyra's three modes the reader is looking at, so nobody takes a replay or a rule-based
// reading for a live AI investigation. "live" is shown as available only where this instance has an AI model.
import { useEffect, useState } from "react";
import m from "./mode.module.css";

export type ModeKind = "replay" | "rules" | "live";

const MODES: { kind: ModeKind; name: string; what: string }[] = [
  { kind: "replay", name: "Interactive replay", what: "An investigation Veyra already ran, recorded and played back step by step. Nothing is re-run." },
  { kind: "rules", name: "Rules-only analysis", what: "Basic checks on the log with no AI model: the first error, the warnings before it, restarts and stops." },
  { kind: "live", name: "Live AI investigation", what: "Veyra's AI investigator decides and runs checks on the original data, and every quoted line is verified." },
];

/** Whether this instance runs live AI investigations. null while unknown. */
export function useLiveAi(call: (path: string) => Promise<any>): boolean | null { // eslint-disable-line @typescript-eslint/no-explicit-any
  const [live, setLive] = useState<boolean | null>(null);
  useEffect(() => { call("/engine/mode").then((r) => setLive(!!r?.live_ai)).catch(() => setLive(false)); }, [call]);
  return live;
}

export function ModeStrip({ current, liveAi, compact = false }: { current: ModeKind; liveAi: boolean | null; compact?: boolean }) {
  return (
    <div className={`${m.strip} ${compact ? m.compact : ""}`} role="note" aria-label={`Mode: ${MODES.find((x) => x.kind === current)!.name}`}>
      {MODES.map((x) => {
        const on = x.kind === current;
        const offHere = x.kind === "live" && liveAi === false;
        return (
          <div key={x.kind} className={`${m.mode} ${on ? m.on : ""}`} aria-current={on ? "true" : undefined}>
            <div className={m.name}>{on && <span className={m.dot} aria-hidden />}{x.name}{on && <span className={m.here}>you are here</span>}</div>
            {!compact && <div className={m.what}>{x.what}</div>}
            {offHere && <div className={m.avail}>Not on this public demo. Offered in a scheduled technical demo.</div>}
          </div>
        );
      })}
    </div>
  );
}

/** One line for the upload dialog: what an upload here will get. */
export function UploadModeNote({ liveAi }: { liveAi: boolean | null }) {
  if (liveAi === null) return null;
  return liveAi
    ? <p className={m.uploadNote}><b>Live AI investigation is on for this instance.</b> If you allow it below, Veyra's AI investigator reads your data and runs its own checks.</p>
    : <p className={m.uploadNote}><b>This public demo runs rules-only analysis on uploads.</b> You get basic log checks (first error, warnings before it, restarts), not an AI investigation. Live AI investigation is offered in a scheduled technical demo.</p>;
}
