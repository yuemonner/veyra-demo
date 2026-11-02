"use client";
/* Investigate together: the engineer brings a guess or a bench result; the AI investigator queries the original data,
   says which parts hold, updates where the fault can be and proposes the next check; the engineer's next result moves
   it on. Rounds come from GET /engine/cases/{id}/discussion: recorded ones (real model output, the engineer's side
   scripted, both said so) are played back; on a workspace with the AI investigator the next round runs live. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import t from "./tools.module.css";
import { Tag } from "./CaseTools";
import { rewrite } from "../../lib/claim-scope";

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
type Call = (path: string, init?: RequestInit) => Promise<Any>;

const VERDICT: Record<string, [string, string]> = { holds: ["Holds", "v_supports"], fails: ["Doesn't hold", "v_against"], partly: ["Partly", "v_cannot_tell"], cannot_tell: ["Data can't tell", "v_facts"] };
const STATUS: Record<string, string> = { leading: "leading", open: "open", weakened: "weakened", ruled_out: "ruled out" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const realSteps = (r: Any) => (r.steps || []).filter((s: Any) => !/Unknown tool/.test(s.summary || ""));

export type DiscussionData = { rounds: Any[]; hypotheses: Any[]; agent: Any } | null;

export function useDiscussion(caseId: string | null, call: Call, enabled: boolean) {
  const [d, setD] = useState<DiscussionData>(null);
  const load = useCallback(async () => {
    if (!caseId || !enabled) { setD(null); return; }
    try { setD(await call(`/engine/cases/${caseId}/discussion`)); } catch { setD(null); }
  }, [caseId, call, enabled]);
  useEffect(() => { load(); }, [load]);
  return { data: d, reload: load };
}

export function Together({ caseId, call, lines, data, reload, readOnly }: { caseId: string; call: Call; lines: string[]; data: DiscussionData; reload: () => Promise<void>; readOnly: boolean }) {
  const rounds = data?.rounds || [];
  const recorded = rounds.filter((r: Any) => r.recorded);
  const live = !!data?.agent?.configured && !!data?.agent?.allowed && !readOnly;
  const [shown, setShown] = useState(0);            // rounds fully shown
  const [playing, setPlaying] = useState<{ i: number; steps: number } | null>(null);
  const [msg, setMsg] = useState("");
  const [kind, setKind] = useState<"guess" | "result">("guess");
  const [job, setJob] = useState<Any>(null);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { setShown(0); setPlaying(null); }, [caseId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [shown, playing?.steps, job?.steps?.length]);

  async function play(i: number) {
    const r = rounds[i];
    const n = realSteps(r).length;
    setPlaying({ i, steps: 0 });
    await sleep(700);
    for (let k = 1; k <= n; k++) { setPlaying({ i, steps: k }); await sleep(650); }
    await sleep(400);
    setPlaying(null); setShown(i + 1);
  }

  async function send() {
    const text = msg.trim();
    if (!text) return;
    setErr(null);
    try {
      const { job_id } = await call(`/engine/cases/${caseId}/discuss`, { method: "POST", body: JSON.stringify({ message: text, kind }) });
      setMsg(""); setJob({ steps: [], message: text, kind });
      for (;;) {
        await sleep(900);
        const j = await call(`/engine/jobs/${job_id}`).catch(() => null);
        if (!j) continue;
        setJob({ ...j, message: text, kind });
        if (j.state !== "running") { if (j.state === "failed") setErr(j.error || "The round failed"); break; }
      }
      await reload(); setShown(9999); setJob(null);
    } catch (e: Any) { setErr(e.message); setJob(null); }
  }

  if (!data) return <p className={t.note}>Loading the discussion…</p>;
  const visible = rounds.slice(0, Math.min(shown, rounds.length));
  const nextIdx = visible.length;
  const done = nextIdx >= rounds.length && !playing;
  return (
    <div className={t.together}>
      <div className={t.loop} aria-label="How a round works">
        {["Your guess or result", "Veyra queries the data", "What holds", "Next check", "Your result"].map((s, i) => <span key={s}>{i > 0 && <i aria-hidden>→</i>}{s}</span>)}
      </div>
      {recorded.length > 0 && <p className={t.recNote}><b>Recorded session.</b> {recorded[0].recorded?.note || "Recorded with the live AI investigator."}</p>}
      {!rounds.length && !live && <p className={t.note}>No discussion recorded for this case. Live rounds need the AI investigator, which is off here.</p>}

      <ol className={t.rounds}>
        {visible.map((r: Any, i: number) => <Round key={r.entry_id || i} r={r} prev={i > 0 ? visible[i - 1].result?.hypotheses : null} lines={lines} n={i + 1} />)}
        {playing && <Round r={rounds[playing.i]} prev={playing.i > 0 ? rounds[playing.i - 1].result?.hypotheses : null} lines={lines} n={playing.i + 1} stepsShown={playing.steps} />}
        {job && <Round r={{ message: job.message, message_kind: job.kind, steps: job.steps || [] }} prev={null} lines={lines} n={rounds.length + 1} stepsShown={(job.steps || []).length} />}
      </ol>
      <div ref={endRef} />

      {!playing && !job && nextIdx < rounds.length && (
        <div className={t.row}>
          <button className={t.primary} onClick={() => play(nextIdx)}>{nextIdx === 0 ? "Play the recorded session" : `Round ${nextIdx + 1}: the engineer replies`}</button>
          <button className={t.ghost} onClick={() => setShown(rounds.length)}>Show all rounds</button>
        </div>)}

      {done && !job && (live ? (
        <form className={t.liveForm} onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className={t.row} role="radiogroup" aria-label="What are you telling Veyra?">
            {([["guess", "My guess"], ["result", "What I found"]] as const).map(([k, l]) => (
              <button type="button" key={k} className={kind === k ? t.chipOn : t.chip} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>))}
          </div>
          <textarea className={t.ask} rows={3} value={msg} onChange={(e) => setMsg(e.target.value)} maxLength={1500}
            placeholder={kind === "guess" ? "e.g. I think the Ethernet DMA reads the buffer before the cache is cleaned" : "e.g. Direct cable to the PC: tcpdump still shows 36 zero bytes"} />
          <div className={t.row}><button className={t.primary} type="submit" disabled={!msg.trim()}>Send to Veyra</button>
            <span className={t.fine}>Runs a live round with {data?.agent?.model || "the AI investigator"} on this case&apos;s data (about a minute).</span></div>
        </form>
      ) : rounds.length > 0 && (
        <p className={t.endNote}>The recorded session ends here: the next step would be the engineer running the check above and telling Veyra what they found. Live rounds need the AI investigator, which is off on the public demo. The quick checks (fixed, in your browser) still work.</p>
      ))}
      {err && <p className={t.bad}>{err}</p>}
    </div>
  );
}

function Round({ r, prev, lines, n, stepsShown }: { r: Any; prev: Any[] | null; lines: string[]; n: number; stepsShown?: number }) {
  const res = r.result;
  const steps = realSteps(r);
  const busy = stepsShown !== undefined;
  const before: Record<string, string> = Object.fromEntries((prev || []).map((h: Any) => [h.id, h.status]));
  const moved = res && prev ? (res.hypotheses || []).filter((h: Any) => before[h.id] && before[h.id] !== h.status) : [];
  return (
    <li className={t.roundItem}>
      <div className={t.me}>
        <span className={t.who}>Round {n} · {r.recorded?.voice === "reviewer" ? "Reviewer · challenge (paraphrased from a robotics engineer who read the case)"
          : `Engineer · ${r.message_kind === "result" ? "result of a check" : "guess"}${r.recorded ? " · scripted for this demo" : ""}`}</span>
        <p>{r.message}</p>
      </div>
      <div className={t.veyra}>
        <span className={`${t.src} ${t.srcAI}`}>AI investigator{r.model ? ` · ${r.model}` : ""}{r.recorded ? ` · recorded ${String(r.recorded.at || "").slice(0, 10)}` : " · live"}{r.seconds ? ` · ${r.seconds} s` : ""}</span>
        <ol className={t.qsteps} aria-label="What Veyra queried">
          {steps.slice(0, busy ? stepsShown : steps.length).map((s: Any) => <li key={s.id}><span>{s.label}</span><span className={t.dim}>{s.summary}</span></li>)}
          {busy && <li className={t.now}><span className={t.pulse} aria-hidden /> {steps.length && stepsShown! >= steps.length && !res ? "Writing the round" : "Querying the data"}</li>}
        </ol>
        {res && !busy && <>
          <p className={t.reply}>{res.reply}</p>
          <div className={t.parts}>
            <span className={t.partsHead}>Your message, part by part</span>
            {(res.parts || []).map((p: Any, i: number) => {
              const [label, cls] = VERDICT[p.verdict] || [p.verdict, ""];
              const why = p.kind === "observed" ? rewrite(p.why) : p.why;
              const ev = (p.evidence || []).filter((e: Any) => e.line >= 1 && e.line <= lines.length).slice(0, 2);
              return (
                <div key={i} className={t.part}>
                  <div className={t.partHead}><span className={`${t.verdict} ${t[cls] || ""}`}>{label}</span><b>{p.claim}</b></div>
                  <p className={t.partWhy}><Tag k={p.kind} />{why}{p.observed_at ? <span className={t.dim}> · seen at: {p.observed_at}</span> : null}</p>
                  {ev.length > 0 && <div className={t.quotes}>{ev.map((e: Any) => <div key={e.line} className={t.q}><code className={t.ln}>{e.line}</code><code className={t.raw}>{lines[e.line - 1]}</code></div>)}</div>}
                </div>);
            })}
          </div>
          {moved.length > 0 && <p className={t.moved}>Where the fault can be: {moved.map((h: Any) => `${h.area} ${STATUS[before[h.id]]} → ${STATUS[h.status]}`).join(" · ")}</p>}
          {!moved.length && res.hypotheses?.length > 0 && <p className={t.movedQuiet}>Leading: {(res.hypotheses.filter((h: Any) => h.status === "leading").map((h: Any) => h.area).join(", ")) || "none"} · {res.hypotheses.filter((h: Any) => h.status !== "ruled_out").length} of {res.hypotheses.length} still possible</p>}
          {res.next_check && (
            <div className={t.nextCheck}>
              <span className={t.partsHead}>Next check{res.next_check.where ? ` · ${String(res.next_check.where).replace("_", " ")}` : ""}</span>
              <b>{res.next_check.action}</b>
              {res.next_check.why && <span className={t.dim}>{res.next_check.why}</span>}
              <ul>{(res.next_check.outcomes || []).map((o: Any, i: number) => <li key={i}><b>{o.label}</b> → {o.means}</li>)}</ul>
            </div>)}
          {(res.still_unknown || []).length > 0 && <p className={t.fine}>Still unknown: {res.still_unknown.join(" · ")}</p>}
        </>}
        {r.failure && <p className={t.bad}>{r.failure}</p>}
      </div>
    </li>
  );
}

/** On the investigation: where the discussion has taken the case since, so the page never shows a stale next step. */
export function DiscussionStatus({ data, onOpen }: { data: DiscussionData; onOpen: () => void }) {
  const rounds = (data?.rounds || []).filter((r: Any) => r.result);
  if (!rounds.length) return null;
  const last = rounds[rounds.length - 1];
  const lead = (last.result.hypotheses || []).filter((h: Any) => h.status === "leading").map((h: Any) => h.area);
  const open = (last.result.still_unknown || [])[0];
  return (
    <div className={t.since}>
      <span className={t.partsHead}>Since this investigation · {rounds.length} round{rounds.length > 1 ? "s" : ""} of discussion with the AI investigator{rounds.some((r: Any) => r.recorded) ? " (recorded)" : ""}</span>
      <p><b>Latest next check (round {rounds.length}):</b> {last.result.next_check?.action}</p>
      {lead.length > 0 && <p><b>Leading now:</b> {lead.join(", ")}</p>}
      {open && <p><b>Still open:</b> {open}</p>}
      <button className={t.link} onClick={onOpen}>Open the discussion</button>
    </div>
  );
}
