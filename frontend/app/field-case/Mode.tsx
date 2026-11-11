"use client";

// Says plainly which of Veyra's three modes the reader is looking at, so nobody takes a replay or a rule-based
// reading for a live AI investigation. "live" is shown as available only where this instance has an AI model.
import { useEffect, useState } from "react";
import m from "./mode.module.css";

export type ModeKind = "replay" | "rules" | "live";

const MODES: { kind: ModeKind; name: string; what: string }[] = [
  { kind: "replay", name: "Interactive replay", what: "An investigation Veyra already ran, recorded and played back step by step. Nothing is re-run." },
  { kind: "rules", name: "Rules-only analysis", what: "Fixed checks with no AI model: on a text log, the first error, the warnings before it, restarts; on a recording or table, Veyra's signal rules." },
  { kind: "live", name: "Live AI investigation", what: "Veyra's AI investigator decides and runs checks on the original data (a text log, or a recording through Veyra's evidence tools), and every quoted line and number is verified." },
];

type Call = (path: string) => Promise<any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const NO_CALL: Call = () => Promise.resolve(null);

/** What this instance is: whether it runs live AI investigations, and whether it is the shared hosted demo. */
export function useInstance(call: Call): { liveAi: boolean | null; hosted: boolean } {
  const [mode, setMode] = useState<{ liveAi: boolean | null; hosted: boolean }>({ liveAi: null, hosted: false });
  useEffect(() => { call("/engine/mode").then((r) => setMode({ liveAi: !!r?.live_ai, hosted: !!r?.hosted_demo })).catch(() => setMode({ liveAi: false, hosted: false })); }, [call]);
  return mode;
}

/** Whether this instance runs live AI investigations. null while unknown. */
export function useLiveAi(call: Call): boolean | null {
  return useInstance(call).liveAi;
}

const SHORT: Record<ModeKind, string> = { replay: "Replay of a recorded investigation", rules: "Rules-only analysis, no AI model", live: "Live AI investigation" };

/** One quiet line naming the mode; the three-mode explanation opens on demand, so the investigation stays in view. */
export function ModeStrip({ current, liveAi }: { current: ModeKind; liveAi: boolean | null; compact?: boolean }) {
  return (
    <details className={m.chip}>
      <summary><span className={m.dot} aria-hidden /><span>{SHORT[current]}</span><span className={m.more}>What is this?</span></summary>
      <div className={m.panel} role="note">
        {MODES.map((x) => (
          <p key={x.kind} className={x.kind === current ? m.cur : undefined}>
            <b>{x.name}</b> <span>{x.what}</span>
            {x.kind === "live" && liveAi === false && <span className={m.avail}> Not on this public demo. Offered in a scheduled technical demo.</span>}
          </p>))}
      </div>
    </details>
  );
}

/** One line for the upload dialog: what an upload here will get. */
export function UploadModeNote({ liveAi, call }: { liveAi: boolean | null; call?: Call }) {
  const hosted = useInstance(call || NO_CALL).hosted;
  if (liveAi === null) return null;
  return <>{liveAi
    ? <p className={m.uploadNote}><b>Live AI investigation is on for this instance.</b> If you allow it below, Veyra's AI investigator runs its own checks: on a text log, over the original lines; on a recording or table, through Veyra's evidence tools (time windows, signal changes, relations between signals, event order, anomaly candidates).</p>
    : <p className={m.uploadNote}><b>This public demo runs rules-only analysis on uploads.</b> You get basic checks (on a text log: the first error, the warnings before it, restarts; on a recording or table: Veyra's signal rules), not an AI investigation. Live AI investigation is offered in a scheduled technical demo.</p>}
    {hosted && <p className={m.dataNote}>Don't upload logs with passwords, keys, tokens or personal data. On this shared demo, anyone holding the access code can open what you upload; visitors without it cannot. Uploads are deleted whenever the demo restarts.</p>}</>;
}
