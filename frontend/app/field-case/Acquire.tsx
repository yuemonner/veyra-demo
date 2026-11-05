"use client";
/* Evidence acquisition. Before anyone is asked a question, Veyra looks for the answer in the data: what it found is
   listed with the line that answers it, and only what the data cannot show becomes a question for a person, with the
   reason, where Veyra already looked, and how each answer would move the investigation. A re-test names the conditions
   to reproduce, the baseline to compare with, the signals to record, and whether an engineer has to approve it. */
import React from "react";
import st from "./investigation.module.css";

type Any = any; // investigator output is versioned JSON; read defensively

const ASK: Record<string, string> = { field_engineer: "Field engineer", customer: "Customer", supplier: "Supplier" };

/** Conditions, baseline, signals and approval of a re-test, inside the next-action card. */
export function RetestSpec({ next, light = false }: { next: Any; light?: boolean }) {
  if (!next || !(next.conditions || next.baseline || (next.record || []).length || next.needs_approval)) return null;
  return (
    <dl className={light ? `${st.retest} ${st.retestLight}` : st.retest}>
      {next.conditions && <><dt>Conditions</dt><dd>{next.conditions}</dd></>}
      {next.baseline && <><dt>Compare with</dt><dd>{next.baseline}</dd></>}
      {(next.record || []).length > 0 && <><dt>Record</dt><dd className={st.signals}>{next.record.map((s: string) => <code key={s}>{s}</code>)}</dd></>}
      {next.needs_approval && <><dt>Approval</dt><dd>Needs an engineer&apos;s approval before it is run</dd></>}
    </dl>
  );
}

/** What the data already answered, the few questions only a person can answer, and what no signal can show. */
export function BeforeAsking({ r, hyps = [] }: { r: Any; hyps?: Any[] }) {
  const answered: Any[] = r?.already_answered || [], questions: Any[] = r?.field_questions || [], blind: Any[] = r?.unobservable || [];
  if (!answered.length && !questions.length && !blind.length) return null;
  const area = (id: string) => (hyps.find((h: Any) => h.id === id) || {}).area || id;
  return (
    <div className={st.block}>
      <h2 className={st.h2}>Before asking the field</h2>
      <p className={st.fine}>{`Veyra answered ${answered.length} question${answered.length === 1 ? "" : "s"} from the data, so nobody has to ask them. ${questions.length ? `It asks ${questions.length} that only a person can answer.` : "It needs nothing from the field this round."}`}</p>
      {answered.length > 0 && (
        <ul className={st.answered}>
          {answered.map((a, i) => (
            <li key={i}>
              <span className={st.aq}>{a.question}</span>
              <span className={st.aa}>{a.answer}</span>
              <span className={st.aline}><code className={st.ln}>{a.line}</code><code className={st.raw}>{a.quote}</code></span>
            </li>))}
        </ul>
      )}
      {questions.length > 0 && (
        <div className={st.questions}>
          {questions.map((q, i) => (
            <div key={i} className={st.question}>
              <span className={st.qwho}>{`Ask: ${ASK[q.ask] || q.ask}`}</span>
              <b className={st.qtext}>{q.question}</b>
              <dl className={st.qwhy}>
                <dt>Why it matters</dt><dd>{q.why_needed}</dd>
                <dt>Not in the data</dt><dd>{q.not_in_data}</dd>
              </dl>
              <ul className={st.qanswers}>
                {(q.if_answers || []).map((o: Any, j: number) => (
                  <li key={j}><b>{o.answer}</b><span>{o.means}</span>
                    {((o.keeps || []).length > 0 || (o.weakens || []).length > 0) && <span className={st.qfx}>
                      {(o.keeps || []).length > 0 && <span>{`Leaves: ${o.keeps.map(area).join(", ")}`}</span>}
                      {(o.weakens || []).length > 0 && <span>{`Weakens: ${o.weakens.map(area).join(", ")}`}</span>}
                    </span>}
                  </li>))}
              </ul>
            </div>))}
        </div>
      )}
      {blind.length > 0 && (
        <div className={st.blind}>
          <span className={st.blindHead}>No signal in this data can show</span>
          <ul>{blind.map((b, i) => <li key={i}><b>{b.fact}</b>{b.why_it_matters ? <span>{b.why_it_matters}</span> : null}<span className={st.observe}>{`How to see it: ${b.how_to_observe}`}</span></li>)}</ul>
        </div>
      )}
    </div>
  );
}
