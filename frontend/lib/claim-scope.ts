// Claim scope: a statement may only claim, as observed, what the evidence could have observed. A micro-ROS agent log
// is recorded by the agent on the host: it shows what the agent received and sent. "The board sent zeros" or "zeros
// on the wire" are not in it; they are inferences, or a later engineer report about a capture made elsewhere.
//
// The guard runs on every investigation before it is shown, written into a report or used in the discussion, so a
// recorded investigation (written before this guard existed) and a live one are held to the same rule. It rewrites
// observed-fact statements that overreach, keeps a record of each rewrite, and labels what every statement is.

type Any = any; // eslint-disable-line @typescript-eslint/no-explicit-any
export type Kind = "observed" | "inferred" | "reported" | "hypothesis";
export type Vantage = { id: string; label: string; observer: string; remote: string };
export type Rewrite = { field: string; before: string; after: string };
export type Scope = { vantage: Vantage; rewrites: Rewrite[]; residual: { field: string; text: string }[] };

export const AGENT_HOST: Vantage = { id: "agent_host", label: "the micro-ROS agent's log, recorded on the host", observer: "agent", remote: "board" };

// Ordered: specific phrasings first. Each turns a claim about the board's transmission (not observable here) into
// what the agent's log does show.
const RULES: [RegExp, string | ((...a: string[]) => string)][] = [
  [/\b[Tt]he board's create-client handshake was valid\b/g, "The create-client handshake the agent received was valid"],
  [/\bThe next packet from the board is\b/g, "The next packet the agent received from the board's address is"],
  [/\b(?:[Tt]he )?[Bb]oard ((?:then |only |still |also )?)(sent|sends|transmitted|transmits|repeated|repeats|resent|resends|kept (?:re)?sending|keeps (?:re)?sending|kept (?:re)?transmitting|keeps (?:re)?transmitting)\b/g,
    (_m: string, adv: string, verb: string) => `the agent ${adv}${/^(sent|transmitted|resent)$/.test(verb) ? "received" : /^(sends|transmits|resends)$/.test(verb) ? "receives" : /^(repeated|kept)/.test(verb) ? "kept receiving" : "keeps receiving"}`],
  [/\bsent by the board\b/g, "received by the agent"],
  [/\b[Bb]oard's (\d+-byte) (packet|datagram)\b/g, "The $1 $2"],
  [/\bon the wire at the agent host\b/g, "at the agent host"],
  [/\breaches the wire\b/g, "arrives at the agent host"],
  [/\bthe wire shows zeros\b/g, "the capture at the agent host shows zeros"],
  [/\b(already )?(all )?zeros on the wire\b/g, "$1$2zeros at the agent host"],
  [/\bon the wire\b/g, "at the agent host"],
  [/\bdata leaving the board\b/g, "data arriving from the board's address"],
  [/\bfrom the board\b(?!'s)/g, "from the board's address"],
];
// What may not stand as an observed fact in an agent-side log, even after rewriting.
export const OUT_OF_SCOPE = /\b[Bb]oard (?:then )?(?:only |still |also )?(?:sent|sends|keeps (?:re)?sending|kept (?:re)?sending|transmits|transmitted|repeated|repeats|resent|resends)\b|\bon the wire\b|\breaches the wire\b|\bleaving the board\b|\bsent by the board\b/;

const SENT_START = (s: string) => s.replace(/(^|[.!?]\s+)the agent/g, (_m, p) => `${p}The agent`);

export function rewrite(text: string): string {
  let s = text;
  for (const [re, to] of RULES) s = typeof to === "string" ? s.replace(re, to) : s.replace(re, to as (...a: string[]) => string);
  return SENT_START(s);
}

/** What a statement is: read in the log, inferred from it, or reported by a person. Hypotheses are labelled by where they sit. */
export function kindOf(text: string | undefined): Kind {
  const s = String(text || "");
  if (/\b(engineer|reported|reports|tcpdump|debugger|according to|they saw|the reporter)\b/i.test(s)) return "reported";
  if (/\b(so|thus|therefore|points?|pointing|suggests?|likely|probabl\w*|means?|indicat\w*|consistent|consequence|effect|leading|cause[sd]?|because|explain\w*|fits?|implies|since|reaction|did not|not the cause|worked|never becomes)\b/i.test(s)) return "inferred";
  return "observed";
}

const FACT_FIELDS = ["headline", "explanation"];
/** The guard over one investigation result: rewrites fact statements, records each change, flags what is left. */
export function guardResult(result: Any, vantage: Vantage = AGENT_HOST): { result: Any; scope: Scope } {
  const scope: Scope = { vantage, rewrites: [], residual: [] };
  if (!result || typeof result !== "object") return { result, scope };
  const r = JSON.parse(JSON.stringify(result));
  const fix = (field: string, text: string | undefined) => {
    if (!text) return text;
    const after = rewrite(text);
    if (after !== text) scope.rewrites.push({ field, before: text, after });
    return after;
  };
  for (const f of FACT_FIELDS) r[f] = fix(f, r[f]);
  (r.evidence || []).forEach((e: Any, i: number) => { e.shows = fix(`evidence ${i + 1}`, e.shows); e.kind = kindOf(e.shows); });
  (r.checked || []).forEach((c: Any, i: number) => { c.result = fix(`check ${i + 1}`, c.result); c.kind = kindOf(`${c.check} ${c.result}`); });
  (r.effects || []).forEach((e: Any, i: number) => { e.why = fix(`effect ${i + 1}`, e.why); });
  (r.events || []).forEach((e: Any, i: number) => { e.what = fix(`event ${i + 1}`, e.what); e.why = fix(`event ${i + 1} note`, e.why); });
  (r.hypotheses || []).forEach((h: Any) => { h.because = fix(`${h.area}: why`, h.because); h.kind = "hypothesis"; });
  r.headline_kind = kindOf(r.headline);
  r.explanation_kind = kindOf(r.explanation);
  // Whatever still overreaches in a statement presented as observed is flagged, not hidden.
  const facts: [string, string | undefined][] = [["headline", r.headline_kind === "observed" ? r.headline : undefined],
    ...((r.evidence || []).filter((e: Any) => e.kind === "observed").map((e: Any, i: number) => [`evidence ${i + 1}`, e.shows]) as [string, string][]),
    ...((r.checked || []).filter((c: Any) => c.kind === "observed").map((c: Any, i: number) => [`check ${i + 1}`, c.result]) as [string, string][]),
    ...((r.events || []).map((e: Any, i: number) => [`event ${i + 1}`, e.what]) as [string, string][])];
  for (const [field, text] of facts) if (text && OUT_OF_SCOPE.test(text)) scope.residual.push({ field, text });
  return { result: r, scope };
}

/** An investigation record (live or recorded), guarded. */
export function guardInvestigation(inv: Any, vantage: Vantage = AGENT_HOST): Any {
  if (!inv?.result || typeof inv.result !== "object") return inv;
  const { result, scope } = guardResult(inv.result, vantage);
  return { ...inv, result, scope };
}

/** A recorded walkthrough: every round guarded. */
export function guardPlayback(pb: Any, vantage: Vantage = AGENT_HOST): Any {
  if (!pb?.investigations) return pb;
  return { ...pb, investigations: pb.investigations.map((i: Any) => guardInvestigation(i, vantage)) };
}

export const KIND_LABEL: Record<Kind, string> = { observed: "Observed", inferred: "Inferred", reported: "Engineer report", hypothesis: "Hypothesis" };
