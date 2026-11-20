"""Predict -> Test -> Update: the investigator's causal-inquiry loop, recorded in a hash-chained ledger.

The model proposes; this module records and decides what stands:

  1. Competing hypotheses are seeded from the deterministic physical assessment (its upstream causes and contributing
     factors); the model may add its own (including combinations: several causes can coexist).
  2. PREDICT: before a test runs, the model registers, for at least two hypotheses, the value each one predicts for one
     quantity of one read-only computation (app.physchecks.compute). Predictions that do not differ are refused (the test
     would not discriminate).
  3. TEST: the registered computation runs. A test whose exact computation the model had already run while exploring is
     recorded as post_hoc: kept, but it does not count as a discriminating test.
  4. UPDATE: deterministic. Each prediction is compared with the computed value: consistent / contradicted / no_result.
     Nothing here changes a hypothesis status in the physical assessment (only verified outcomes do); the ledger records
     which hypotheses each test separated.

On submit_report (problems()):
  * at least one executed, non-post-hoc prediction test is required when two or more hypotheses compete;
  * a ranking (tiers of hypotheses, most likely first) is accepted only where an executed test separated every
    hypothesis placed above another (upper consistent, lower contradicted) and no test separated them the other way;
    hypotheses no test separates share a tier (they may coexist);
  * a main cause marked leading over another main cause needs the same separation;
  * a hypothesis contradicted by a test cannot be leading.

Every record carries what kind of knowledge it is: observation, reported_value, assumption, inferred_parameter,
conditional, verified_fact. Every entry is chained by SHA-256 (prev hash + entry), so the order of predictions and tests
is tamper-evident.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import anticipate as ANT, compute, effects as FX

INQUIRY_VERSION = "inquiry-v4"
STRATEGIES = ("free", "coverage", "infogain")


def dynamics_enabled() -> bool:
    import os
    return (os.environ.get("VEYRA_DYNAMICS") or "on").strip().lower() not in ("off", "0", "false")


def strategy() -> str:
    """Which investigation policy holds the agent (the tools are the same under every strategy):
      free      at least one executed prediction test when causes compete (inquiry-v1's rule);
      coverage  every pair of competing causes is separated by a test, or declared not discriminable after every
                applicable computation has been run;
      infogain  the agent runs the tests with the highest expected reduction of uncertainty among the causes still tied,
                and decides when to stop: every pair still tied must have been tested, or declared not discriminable."""
    import os
    v = (os.environ.get("VEYRA_INQUIRY_STRATEGY") or "coverage").strip().lower()
    return v if v in STRATEGIES else "coverage"

COMPETING_STATUSES = ("open", "leading")
PREFIX = "inquiry_"
SEED_ROLES = ("upstream_cause", "contributing_factor")

_EXPECT = {"type": "object", "description": "Exactly one of: between [lo, hi]; lt x; gt x; approx x with tol; eq x (numbers; true/false count as 1/0)",
           "properties": {"between": {"type": "array", "items": {"type": "number"}}, "lt": {"type": "number"}, "gt": {"type": "number"},
                          "approx": {"type": "number"}, "tol": {"type": "number"}, "eq": {}}}
_TEST = {"type": "object", "properties": {"op": {"type": "string", "enum": sorted(compute.OPS)}, "args": {"type": "object"}}, "required": ["op", "args"]}

SPECS: List[Dict[str, Any]] = [
    {"name": "inquiry_signals", "description": "What can be computed on this recording: windows with time spans, signal roles (unit, kind, sign, provenance: which are measured and which are derived from others), stated parameters with their source, and the computation ops with their arguments.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "inquiry_compute", "description": "Run one read-only computation to EXPLORE (not a test). Ops: " + " | ".join(compute.OP_DOC.values()) + " " + compute.WHERE_DOC
                                               + " Every number comes with its kind, assumptions, inputs and source rows. A prediction registered later for the same computation is post-hoc and does not count as a test.",
     "input_schema": {"type": "object", "properties": {"op": {"type": "string", "enum": sorted(compute.OPS)}, "args": {"type": "object"}}, "required": ["op", "args"]}},
    {"name": "inquiry_hypothesis", "description": "Add a competing hypothesis to the inquiry ledger (the physical assessment's upstream causes and contributing factors are already there). Use combines for a combination of hypotheses that act together.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "statement": {"type": "string"}, "combines": {"type": "array", "items": {"type": "string"}},
                                                                                         "mechanism_of": {"type": "string", "description": "This is one distinct way an existing hypothesis can act; tested on its own"},
                                                                                         "effects": {"type": "object", "description": "What it does to the measurements: {role: effect}, effect one of "
                                                                                                     + ", ".join(f"{k} ({v})" for k, v in FX.EFFECT_KINDS.items())}},
                      "required": ["id", "statement"]}},
    {"name": "inquiry_predict", "description": "PREDICT, before testing: register one computation (test) and, for at least two hypotheses, the value each predicts for one quantity of its result, with why. Refused if every hypothesis predicts the same region. Returns a prediction id and its hash.",
     "input_schema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "What this test separates, under 25 words"},
         "test": _TEST,
         "quantity": {"type": "string", "description": "Path into the result's quantities, e.g. median_ratio or anchors.1.implied_capacity_ah"},
         "predictions": {"type": "array", "minItems": 2, "items": {"type": "object", "properties": {
             "hypothesis": {"type": "string"}, "expect": _EXPECT, "why": {"type": "string", "description": "The physical reason, under 30 words"}},
             "required": ["hypothesis", "expect", "why"]}},
         "assumptions": {"type": "array", "items": {"type": "string"}, "description": "What the predictions assume that the data does not show"}},
         "required": ["question", "test", "quantity", "predictions"]}},
    {"name": "inquiry_test", "description": "TEST and UPDATE: run a registered prediction's computation and compare each hypothesis's prediction with the value (deterministic: consistent / contradicted / no_result). Returns the full computation with source rows and the update.",
     "input_schema": {"type": "object", "properties": {"prediction_id": {"type": "string"}}, "required": ["prediction_id"]}},
    {"name": "inquiry_ledger", "description": "The inquiry so far: hypotheses with the tests each survived or failed, which pairs of hypotheses an executed test has separated (and which no test has), predictions, tests and every record by kind.",
     "input_schema": {"type": "object", "properties": {}}},
]
SPECS += [
    {"name": "inquiry_plan", "description": "The causal distinctions still unresolved (every pair of competing causes no executed test has separated), each one's state, "
                                           "the computations applicable to this recording with which you have not run yet, and your untested predictions ranked by how many open distinctions they address. "
                                           "Start here, and come back after each test.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "inquiry_distinguishable", "description": "Record that a distinction between two causes CANNOT be discriminated by the existing evidence: why, which of your computations "
                                                      "(X/T ids) looked, and what record or measurement would separate them. Accepted at submit only after every applicable "
                                                      "computation has been run (inquiry_plan lists them).",
     "input_schema": {"type": "object", "properties": {"pair": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                                                       "reason": {"type": "string", "description": "Why no quantity in this recording differs between them, under 50 words"},
                                                       "looked_at": {"type": "array", "items": {"type": "string"}, "description": "compute / test ids"},
                                                       "would_separate": {"type": "string", "description": "The record or measurement that would, and what each cause predicts for it"}},
                      "required": ["pair", "reason", "looked_at", "would_separate"]}},
]
SPECS += [
    {"name": "inquiry_dynamics", "description": "Veyra's signal-dynamics screen of every group of comparable signals in this recording (e.g. cells of a pack, "
                                               "actuators under one command): rates of the limiting and far members, the spread's departure from its baseline trend "
                                               "and load relation, when it became observable and its lead before each protective event, the limiting member's "
                                               "excursion split into group-wide and own parts, identity of the extreme component, timing-skew and baseline "
                                               "sensitivity; plus the mechanism-class hypotheses and the discriminating tests Veyra generated and ran (G ids), "
                                               "the pairs still unresolved and the next check that would separate them.",
     "input_schema": {"type": "object", "properties": {}}},
]
NAMES = {s["name"] for s in SPECS}

POLICY = {
    "free": """- Test the competing causes with the computations you judge most useful before you conclude: at least one registered
  prediction must be executed. Batch several tool calls per turn.""",
    "coverage": """- For every unresolved causal distinction, establish whether existing evidence can discriminate it, and pursue the
  highest-value available test before concluding. Call inquiry_plan first and again after each test: it lists every pair
  of competing causes not yet separated, the computations applicable to this recording that you have not run, and your
  untested predictions ranked by how many open distinctions they address. A distinction is closed only by an executed test that separates it, or by
  inquiry_distinguishable (why not, what you looked at, what would separate it) after every applicable computation has
  been run. submit_report is returned to you while a distinction is open. Batch several tool calls per turn.""",
    "infogain": """- Spend the budget where it removes the most uncertainty. Register predictions for the candidate tests you can think of
  (each says what every still-tied cause predicts); inquiry_plan ranks them by expected_bits: how much each would shrink
  the set of causes still tied, given what has been tested. Run the highest-value test, read the update, call
  inquiry_plan again, and repeat while some test can still split the remaining tie. Stop when none can: record each pair
  still tied with inquiry_distinguishable (why no computation on this recording splits it, what you looked at, and which
  record or measurement would). A computation that cannot split anything is wasted budget. submit_report is returned to
  you while a tied pair was neither tested nor declared. Batch several tool calls per turn.""",
}

PROMPT_BODY = """
- The competing causes are in inquiry_ledger (seeded from the physical assessment). Several can be true together; add a
  hypothesis or a combination with inquiry_hypothesis when the evidence suggests one.
- For the causes still competing, find quantities you can compute from THIS recording on which they predict different
  values: relationships between signals (a voltage step against a current step, a state change with or without a change
  in measured current), charge counted between two points, the same quantity in different windows, a machine parameter
  inferred from the data, or a statement's value under different assumptions. inquiry_signals lists what can be computed.
- Before you look at a test's result, register what each hypothesis predicts with inquiry_predict (the physical reason,
  and the assumptions the prediction needs). Then run it with inquiry_test. Explore with inquiry_compute when you need to
  learn the data's shape, but a prediction for a computation you already ran is post-hoc and does not count.
- A number in a deterministic statement may rest on an assumption it does not state (a reference point, an anchor, a
  parameter). When a conclusion depends on such a choice, compute it under the alternatives and say which it needs.
- A cause that can act in several distinct ways is tested mechanism by mechanism (inquiry_ledger lists them; add one with
  inquiry_hypothesis mechanism_of). A test of one mechanism says nothing about the others, so predict for each mechanism.
- Point each computation at the signals the mechanism acts through (e.g. the state of the load it names, not any state).
- Keep separate: what was observed in rows, what the machine reports about itself, what you assume, what you infer
  (a parameter inferred from data is not a specification), and what is verified. A signal agreeing with its own inputs is
  not evidence.
- Rank causes only after executed tests separate them. ranking lists tiers, most likely first; put in one tier the
  causes no executed test has separated (they remain indistinguishable on this data, or may act together). Each tier
  cites the tests (inquiry test ids) that placed it. A test contradicting a cause weakens it; it does not rule it out.
- Then name the one record or measurement that would separate what is still tied, with what each cause predicts for it.
- Signal dynamics: rate, changepoint, departure, divergence and quality compute how signals change over time and relative
  to comparable signals (actual timestamps, time windows, measurement floor, segments). When the recording has groups of
  comparable signals, Veyra has already screened them and run generated tests (inquiry_dynamics): read them, and use the
  dynamics ops for what they leave open. A trend that precedes an event is a candidate precursor, not a cause; separate
  the event (the protective response), the condition that triggered it, and the mechanisms that could produce that condition.
- When inquiry_dynamics gives findings for a group, say in the explanation what the group did, with its numbers: which member
  limited, its rate and when it steepened, how the spread grew and how long before the event it departed, how much of the
  excursion was that member's own vs the group's, whether it followed the load, what the machine recorded as the reason,
  and the next check. Reconcile them with the other causes: name which can act together.
"""


def prompt() -> str:
    return ("\nCAUSAL INQUIRY (inquiry_* tools): Predict -> Test -> Update. Do the investigation the data allows before you ask anyone.\n"
            + POLICY[strategy()] + PROMPT_BODY)


PROMPT = prompt()

RANKING_SPEC = {"type": "array", "maxItems": 5, "description": "Only when inquiry_* tools are offered: causes in tiers, most likely first. A tier holds causes no executed test separated. Placing one cause above another needs an executed inquiry test that separated them.",
                "items": {"type": "object", "properties": {
                    "hypotheses": {"type": "array", "items": {"type": "string"}, "description": "Inquiry ledger ids (the physical_hypotheses ids, or ids you added)"},
                    "tests": {"type": "array", "items": {"type": "string"}, "description": "Inquiry test ids (T1, T2...) that placed this tier"},
                    "basis": {"type": "string", "description": "Why this tier, under 40 words"},
                    "coexist": {"type": "boolean", "description": "True if the causes in this tier may act together"}},
                    "required": ["hypotheses", "tests", "basis"]}}

STEP_LABEL = {"inquiry_dynamics": "Read the signal-dynamics screen", "inquiry_plan": "Reviewed the open causal distinctions", "inquiry_distinguishable": "Recorded that {pair} cannot be separated on this data", "inquiry_signals": "Listed what can be computed", "inquiry_compute": "Computed {op}", "inquiry_hypothesis": "Added hypothesis {id}",
              "inquiry_predict": "Registered a prediction", "inquiry_test": "Tested prediction {prediction_id}", "inquiry_ledger": "Read the inquiry ledger"}


def summary(name: str, value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if name == "inquiry_predict":
        return f"{value.get('prediction_id')}: {value.get('question', '')[:120]}" + (" (post-hoc)" if value.get("post_hoc") else "")
    if name == "inquiry_test":
        u = value.get("update") or {}
        return f"{value.get('test_id')}: {value.get('quantity')} = {value.get('value')}; " + ", ".join(f"{h} {o}" for h, o in (u.get("outcomes") or {}).items())
    if name == "inquiry_compute":
        return f"{value.get('op')}: " + ", ".join(f"{k}={v}" for k, v in list((value.get("quantities") or {}).items())[:4] if not isinstance(v, (list, dict)))[:200]
    if name == "inquiry_plan":
        return f"{len(value.get('open') or [])} open distinction(s); {len(value.get('not_run') or [])} applicable computation(s) not run"
    if name == "inquiry_ledger":
        s = value.get("summary") or {}
        return f"{s.get('tests_executed', 0)} test(s), {s.get('discriminating', 0)} discriminating"
    return ""


def _canon(op: str, args: Dict[str, Any]) -> str:
    return json.dumps({"op": op, "args": args or {}}, sort_keys=True, default=str)


def _interval(expect: Dict[str, Any]) -> Tuple[float, float]:
    e = dict(expect or {})
    keys = [k for k in ("between", "lt", "gt", "approx", "eq") if k in e]
    if len(keys) != 1:
        raise ValueError("expect needs exactly one of between, lt, gt, approx (with tol), eq")
    k = keys[0]
    if k == "between":
        lo, hi = (float(x) for x in e["between"])
        return (min(lo, hi), max(lo, hi))
    if k == "lt":
        return (-math.inf, float(e["lt"]))
    if k == "gt":
        return (float(e["gt"]), math.inf)
    if k == "approx":
        tol = abs(float(e.get("tol") or 0)) or abs(float(e["approx"])) * 0.1
        return (float(e["approx"]) - tol, float(e["approx"]) + tol)
    v = e["eq"]
    v = 1.0 if v is True else 0.0 if v is False else float(v)
    return (v, v)


def _inside(v: float, iv: Tuple[float, float], strict: Dict[str, Any]) -> bool:
    lo, hi = iv
    if "lt" in strict:
        return v < hi
    if "gt" in strict:
        return v > lo
    return lo - 1e-12 <= v <= hi + 1e-12


def _extract(result: Dict[str, Any], path: str) -> Any:
    cur: Any = result.get("quantities") or {}
    p = path[len("quantities."):] if path.startswith("quantities.") else path
    for part in [x for x in p.split(".") if x]:
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise ValueError(f"quantity path {path!r}: no element {part!r}") from None
        elif isinstance(cur, dict):
            if part not in cur:
                raise ValueError(f"quantity path {path!r}: no key {part!r} (keys: {', '.join(cur)})")
            cur = cur[part]
        else:
            raise ValueError(f"quantity path {path!r}: {part!r} is below a value")
    return cur


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return float(v)
    return None


def _kind_of(result: Dict[str, Any], path: str) -> str:
    top = (path[len("quantities."):] if path.startswith("quantities.") else path).split(".")[0]
    return (result.get("kinds") or {}).get(top, "observation")


class Inquiry:
    def __init__(self, rec, profile, assessment: Dict[str, Any], *, verified: Optional[List[Dict[str, Any]]] = None):
        self.ctx = compute.Context(rec, profile)
        self.assessment = assessment
        self.entries: List[Dict[str, Any]] = []
        self.hypotheses: Dict[str, Dict[str, Any]] = {}
        self.predictions: Dict[str, Dict[str, Any]] = {}
        self.tests: Dict[str, Dict[str, Any]] = {}
        self.explored: Dict[str, str] = {}          # canonical computation -> entry id of the exploration
        self.records: List[Dict[str, Any]] = []
        self.call_ids: Dict[str, str] = {}          # test / prediction / compute id -> tool call id
        self._n = {"P": 0, "T": 0, "X": 0}
        self.declarations: Dict[str, Dict[str, Any]] = {}       # "A|B" -> not discriminable on existing evidence
        for h in assessment.get("hypotheses") or []:
            if h.get("role") in SEED_ROLES and h.get("status") != "excluded":
                mech = h.get("mechanisms") or []
                self._add_h(h["id"], f"{h.get('label')}: {h.get('statement', '')}".strip(": "), "physical_assessment", physical_ref=h["id"],
                            role=h.get("role"), assessment_status=h.get("status"), family="assessment", **({"mechanisms": [m["id"] for m in mech]} if mech else {}),
                            **({"effects": h["effects"]} if h.get("effects") else {}))
                for m in mech:
                    self._add_h(m["id"], f"{h.get('label')}, mechanism: {m['statement']}", "physical_assessment", physical_ref=h["id"],
                                role=h.get("role"), assessment_status=h.get("status"), parent=h["id"], family="assessment",
                                **({"effects": m["effects"]} if m.get("effects") else {}))
        for v in verified or []:
            self._record("verified_fact", v.get("statement") or json.dumps(v, default=str)[:200], None, "verified_outcome", [])
        self.dyn: Optional[Dict[str, Any]] = None
        self._screen: Optional[Dict[str, Any]] = None
        if dynamics_enabled():
            try:
                self._seed_dynamics()
            except compute.ComputeError as e:
                self._record("assumption", f"signal-dynamics screen not run: {e}", None, "dynamics", [])

    # ---- generated by Veyra: mechanism classes of comparable signals and the tests that separate them ---------------
    def _seed_dynamics(self) -> None:
        g = ANT.focus(self.ctx)
        if not g:
            return
        prefix = "L"
        cls_of: Dict[str, str] = {}
        for h in ANT.hypotheses(g, prefix):
            mech = h.get("mechanisms") or []
            self._add_h(h["id"], f"{h['label']}: {h['statement']}", "dynamics", family=h["family"], cls=h["cls"], coexists_with=h.get("coexists_with") or [],
                        relates_to=h.get("relates_to"), **({"mechanisms": [m["id"] for m in mech]} if mech else {}),
                        **({"effects": h["effects"]} if h.get("effects") else {}))
            cls_of[h["id"]] = h["cls"]
            for m in mech:
                self._add_h(m["id"], f"{h['label']}, mechanism: {m['label']}: {m['statement']}", "dynamics", family=h["family"], parent=h["id"], cls=m["cls"],
                            relates_to=m.get("relates_to"), effects=m["effects"])
                cls_of[m["id"]] = m["cls"]
        gen = []
        for spec in ANT.generated_tests(g, prefix):
            r = self._predict({k: spec[k] for k in ("question", "test", "quantity", "predictions")}, None, source="veyra_generated")
            gen.append((r["prediction_id"], spec))
        tests = []
        for pid, spec in gen:
            t = self._test(pid, None)
            tests.append({"test_id": t["test_id"], "prediction_id": pid, "question": spec["question"], "quantity": spec["quantity"], "value": t["value"],
                          "update": {k: t["update"][k] for k in ("outcomes", "consistent", "contradicted", "discriminating")}, "pairs": spec["pair"]})
        # a class pair is resolved when ANY discriminating test placed one class consistent and the other contradicted
        # (inquiry-v4: a pair one test abstained on but another separated is not unresolved)
        def ids_of(c: str) -> List[str]:
            return [h for h, k in cls_of.items() if k == c or (c == "component" and k in ANT.MECHANISMS)]

        def resolved(a: str, b: str) -> bool:
            for x in tests:
                o = x["update"]["outcomes"]
                ca = {o.get(h) for h in ids_of(a)} - {None}
                cb = {o.get(h) for h in ids_of(b)} - {None}
                if x["update"]["discriminating"] and (("consistent" in ca and cb == {"contradicted"}) or ("consistent" in cb and ca == {"contradicted"})):
                    return True
            return False
        unresolved = []
        for x in tests:
            for pr in x["pairs"]:
                if not resolved(*pr) and tuple(pr) not in unresolved:
                    unresolved.append(tuple(pr))
        nxt = ANT.next_check(g, unresolved)
        for x in tests:                        # a test that could not be computed: the pair is not discriminable on this recording
            if x["value"] is None:
                for a, b in x["pairs"]:
                    ia, ib = ids_of(a), ids_of(b)
                    ia = [h for h in ia if not self.hypotheses[h].get("mechanisms")]
                    ib = [h for h in ib if not self.hypotheses[h].get("mechanisms")]
                    if not ia or not ib or resolved(a, b):
                        continue
                    ch = ANT.next_check(g, [(a, b)])
                    for ha in ia:
                      for hb in ib:
                        if ha == hb or self._key(ha, hb) in self.declarations:
                            continue
                        self.declarations[self._key(ha, hb)] = d = {"pair": [ha, hb], "reason": f"{x['quantity']} returned no result on this recording "
                                                                  "(not computable, or its materiality is undetermined: see the divergence result's notes)",
                                                                  "looked_at": [x["test_id"]],
                                                                  "would_separate": (ch or {}).get("record") or "a record that varies the load or measures the member directly",
                                                                  "not_run_at_declaration": [], "source": "veyra_generated"}
                    self._append("declaration", d)
        dq = next((t["result"]["quantities"] for t in self.tests.values() if t["result"].get("op") == "divergence"), None)
        rq = next((t["result"]["quantities"] for t in self.tests.values() if t["result"].get("op") == "recorded_reason"), None)
        try:
            fnd = ANT.findings(self.ctx, g, dq, rq, nxt) if dq else []
        except Exception as e:                                         # noqa: BLE001 - findings are a summary; never block the inquiry
            fnd = [f"findings not written: {e}"]
        self.dyn = {"group": {k: g[k] for k in ("domain", "group", "title", "window", "members", "driver", "index_roles", "limit_side")},
                    "event": g["event"], "classes": cls_of, "generated_tests": tests, "unresolved_class_pairs": [list(p) for p in unresolved], "next_check": nxt,
                    "findings": fnd}
        self._append("dynamics", self.dyn)

    # ---- ledger ---------------------------------------------------------------------------------------------------
    def _append(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self.entries[-1]["hash"] if self.entries else "0" * 64
        body = {"seq": len(self.entries) + 1, "kind": kind, "at": round(time.time(), 3), "payload": payload, "prev": prev}
        body["hash"] = hashlib.sha256((prev + json.dumps(body["payload"], sort_keys=True, default=str) + kind).encode()).hexdigest()
        self.entries.append(body)
        return body

    def _record(self, kind: str, statement: str, value: Any, source: str, evidence: List[Dict[str, Any]], assumptions: Optional[List[str]] = None) -> None:
        self.records.append({"kind": kind, "statement": statement, "value": value, "source": source,
                             "evidence": [{k: e.get(k) for k in ("file", "row", "field", "value")} for e in evidence[:4]],
                             **({"assumptions": assumptions} if assumptions else {})})

    def _add_h(self, hid: str, statement: str, source: str, **extra: Any) -> Dict[str, Any]:
        h = {"id": hid, "statement": statement, "source": source, **extra}
        self.hypotheses[hid] = h
        self._append("hypothesis", h)
        return h

    def _next(self, k: str) -> str:
        self._n[k] += 1
        return f"{k}{self._n[k]}"

    def _compute(self, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return compute.run(self.ctx, op, args)

    def _harvest(self, result: Dict[str, Any], source: str) -> None:
        ev = result.get("evidence") or []
        for k, v in (result.get("quantities") or {}).items():
            kind = (result.get("kinds") or {}).get(k, "observation")
            if kind in ("inferred_parameter", "conditional", "verified_fact", "reported_value") and v is not None:
                self._record(kind, f"{result['op']}.{k}", v, source, ev, result.get("assumptions") if kind == "conditional" else None)
        for a in result.get("assumptions") or []:
            self._record("assumption", a, None, source, [])

    # ---- tools ----------------------------------------------------------------------------------------------------
    def call(self, name: str, args: Dict[str, Any], call_id: Optional[str] = None) -> Any:
        if name == "inquiry_signals":
            return compute.signals(self.ctx)
        if name == "inquiry_ledger":
            return self.view()
        if name == "inquiry_plan":
            return self.plan()
        if name == "inquiry_dynamics":
            if self._screen is None:
                self._screen = ANT.screen(self.ctx)
            return {"screen": self._screen, "investigation": self.dyn,
                    "note": "Generated tests (registered by Veyra before they ran) are in the ledger as ordinary tests; the hypotheses L1 (group-wide), "
                            "L2 with mechanisms L2a/L2b/L2c (one member: drift / load / sensing) and L3 (initiated outside the group). L1 and L2 can act "
                            "together. A departure that precedes an event is a candidate precursor, not a cause."}
        if name == "inquiry_distinguishable":
            return self._declare(args)
        if name == "inquiry_compute":
            op, a = args.get("op"), args.get("args") or {}
            result = self._compute(op, a)
            xid = self._next("X")
            self.explored.setdefault(_canon(op, a), xid)
            self.call_ids[xid] = call_id or ""
            self._append("compute", {"id": xid, "op": op, "args": a, "quantities": result["quantities"], "kinds": result["kinds"],
                                     "assumptions": result["assumptions"], "inputs": result["inputs"], "evidence": result["evidence"]})
            self._harvest(result, xid)
            ran = self._run_registered(op, a, result, xid)
            return {"compute_id": xid, **result, **({"registered_predictions_tested": ran,
                    "note": "these predictions were registered before this computation ran, so it is their test (registration and hash kept; "
                            "the test records that it ran through this exploration)"} if ran else {})}
        if name == "inquiry_hypothesis":
            hid = str(args.get("id") or "").strip()
            if not hid:
                raise ValueError("id is required")
            if hid in self.hypotheses:
                raise ValueError(f"hypothesis {hid} already exists: {self.hypotheses[hid]['statement'][:120]}")
            eff = {str(k): str(v) for k, v in (args.get("effects") or {}).items()}
            bad = [f"{k}: {v}" for k, v in eff.items() if v not in FX.EFFECT_KINDS or not self.ctx.profile.has_role(k)]
            if bad:
                raise ValueError(f"effects must map a role of this recording to one of {sorted(FX.EFFECT_KINDS)}; bad: {bad}")
            extra_eff = {"effects": eff} if eff else {}
            parent = args.get("mechanism_of")
            if parent:
                if parent not in self.hypotheses or self.hypotheses[parent].get("parent"):
                    raise ValueError(f"mechanism_of must name a hypothesis ({', '.join(self.hypotheses)})")
                self.hypotheses[parent].setdefault("mechanisms", []).append(hid)
                h = self._add_h(hid, str(args.get("statement") or ""), "agent", parent=parent, role=self.hypotheses[parent].get("role"),
                                assessment_status=self.hypotheses[parent].get("assessment_status"), **extra_eff)
                return {**h, "note": f"{parent} is now tested through its mechanisms {self.hypotheses[parent]['mechanisms']}: predict for each, not for {parent}"}
            combines = [c for c in args.get("combines") or []]
            unknown = [c for c in combines if c not in self.hypotheses]
            if unknown:
                raise ValueError(f"combines unknown hypothesis id(s) {unknown}")
            return self._add_h(hid, str(args.get("statement") or ""), "agent", **({"combines": combines} if combines else {}), **extra_eff)
        if name == "inquiry_predict":
            return self._predict(args, call_id)
        if name == "inquiry_test":
            return self._test(str(args.get("prediction_id") or ""), call_id)
        raise ValueError(f"Unknown tool {name}")

    def _predict(self, args: Dict[str, Any], call_id: Optional[str], source: str = "agent") -> Dict[str, Any]:
        test = args.get("test") or {}
        op, a = test.get("op"), test.get("args") or {}
        if op not in compute.OPS:
            raise ValueError(f"test.op must be one of {sorted(compute.OPS)}")
        preds = args.get("predictions") or []
        if len(preds) < 2:
            raise ValueError("register predictions for at least two competing hypotheses")
        seen, ivs = set(), []
        for p in preds:
            hid = p.get("hypothesis")
            if hid not in self.hypotheses:
                raise ValueError(f"unknown hypothesis {hid!r}; ledger ids: {', '.join(self.hypotheses)} (add one with inquiry_hypothesis)")
            if hid in seen:
                raise ValueError(f"hypothesis {hid} has two predictions in one test")
            if self.hypotheses[hid].get("mechanisms"):
                raise ValueError(f"{hid} acts through distinct mechanisms {self.hypotheses[hid]['mechanisms']}: predict for each mechanism, since a test "
                                 f"of one says nothing about the others")
            seen.add(hid)
            ivs.append(_interval(p.get("expect") or {}))
        if len(set(ivs)) == 1:
            raise ValueError("every hypothesis predicts the same region: this test would not discriminate; find a quantity on which they differ")
        audit = FX.audit(op, a, args.get("quantity") or "", preds, {h: self.hypotheses[h].get("effects") or {} for h in seen},
                         sorted(self.ctx.profile.parameters))
        if audit["errors"]:
            raise ValueError("prediction refused by the measurement-fault algebra: " + "; ".join(audit["errors"]) +
                             ". Re-derive what each fault does to this quantity (a sensor that reads low divides a per-measured-unit ratio by k < 1)")
        key = _canon(op, a)
        post_hoc = key in self.explored or any(t["key"] == key for t in self.tests.values())
        if source == "veyra_generated":
            post_hoc = key in self.explored          # generated tests share one computation, all registered before it runs
        pid = self._next("P")
        rec = {"id": pid, "question": args.get("question") or "", "test": {"op": op, "args": a}, "key": key, "quantity": args.get("quantity") or "",
               "predictions": [{"hypothesis": p["hypothesis"], "expect": p["expect"], "why": p.get("why") or ""} for p in preds],
               "assumptions": list(args.get("assumptions") or []) + audit["notes"], "post_hoc": post_hoc,
               "conditional_pairs": audit["conditional_pairs"],
               "post_hoc_reason": (f"the same computation was already run ({self.explored.get(key) or 'an earlier test'}) before this prediction") if post_hoc else None,
               "status": "registered", "source": source}
        entry = self._append("prediction", {k: v for k, v in rec.items() if k != "key"})
        rec["hash"] = entry["hash"]
        self.predictions[pid] = rec
        self.call_ids[pid] = call_id or ""
        for x in rec["assumptions"]:
            self._record("assumption", x, None, pid, [])
        rec["addresses"] = self._addresses(rec)
        rec["expected_bits_at_registration"] = self.expected_bits(rec)
        return {"prediction_id": pid, "hash": entry["hash"], "question": rec["question"], "post_hoc": post_hoc, "post_hoc_reason": rec["post_hoc_reason"],
                "addresses_open_distinctions": rec["addresses"], "expected_bits": rec["expected_bits_at_registration"],
                "next": f"run it with inquiry_test {{prediction_id: {pid}}}"}

    def _run_registered(self, op: str, a: Dict[str, Any], result: Dict[str, Any], xid: str) -> List[str]:
        """inquiry-v4: an exploration whose computation equals a registered, untested prediction's test IS that test: the
        prediction was registered (and hashed) before the computation ran, so it counts; nothing about it is re-registered."""
        key = _canon(op, a)
        out = []
        for p in list(self.predictions.values()):
            if p["status"] == "registered" and p["key"] == key:
                out.append(self._test(p["id"], None, result=result, via=xid)["test_id"])
        return out

    def _test(self, pid: str, call_id: Optional[str], result: Optional[Dict[str, Any]] = None, via: Optional[str] = None) -> Dict[str, Any]:
        p = self.predictions.get(pid)
        if p is None:
            raise ValueError(f"unknown prediction {pid!r}; registered: {', '.join(self.predictions) or 'none'}")
        if p["status"] == "tested":
            raise ValueError(f"{pid} was already tested as {p['test_id']}")
        if result is None:
            result = self._compute(p["test"]["op"], p["test"]["args"])
        tid = self._next("T")
        try:
            raw = _extract(result, p["quantity"])
            err = None
        except ValueError as e:
            raw, err = None, str(e)
        v = _number(raw)
        outcomes: Dict[str, str] = {}
        for pr in p["predictions"]:
            if v is None:
                outcomes[pr["hypothesis"]] = "no_result"
            else:
                outcomes[pr["hypothesis"]] = "consistent" if _inside(v, _interval(pr["expect"]), pr["expect"]) else "contradicted"
        cond = {h for pair in p.get("conditional_pairs") or [] for h in pair}
        for h in list(outcomes):
            if outcomes[h] == "contradicted" and h in cond:
                outcomes[h] = "contradicted_if_assumed"      # rests on an assumed baseline magnitude: weakens, does not separate
        cons = sorted(h for h, o in outcomes.items() if o == "consistent")
        contra = sorted(h for h, o in outcomes.items() if o == "contradicted")
        discriminating = bool(cons and contra) and not p["post_hoc"]
        group = sorted({(i["role"], i["window"]) for i in result.get("inputs") or []})
        update = {"outcomes": outcomes, "consistent": cons, "contradicted": contra, "discriminating": discriminating,
                  "separated": [[u, l] for u in cons for l in contra] if discriminating else [],
                  "counts": not p["post_hoc"], "evidence_group": [list(g) for g in group],
                  "conditional": sorted(h for h, o in outcomes.items() if o == "contradicted_if_assumed"),
                  "rule": "deterministic: the computed value inside a hypothesis's predicted region is consistent with it, outside contradicts it. "
                          "A contradiction weakens a hypothesis under the prediction's assumptions; it never rules it out. A contradiction whose "
                          "separation rests on an assumed baseline magnitude (an unstated machine parameter) is contradicted_if_assumed: it does not "
                          "place one cause below another."}
        if err:
            update["error"] = err
        test = {"id": tid, "prediction_id": pid, "key": p["key"], "quantity": p["quantity"], "value": raw, "value_kind": _kind_of(result, p["quantity"]),
                "result": result, "update": update, "assumptions": p["assumptions"] + list(result.get("assumptions") or []),
                "registered_at": p.get("hash"), "via_exploration": via, "post_hoc": p["post_hoc"]}
        self._append("test", {k: v2 for k, v2 in test.items() if k not in ("key",)})
        self._append("update", {"test_id": tid, **update})
        p["status"], p["test_id"] = "tested", tid
        self.tests[tid] = test
        self.call_ids[tid] = call_id or ""
        self._harvest(result, tid)
        self._record("observation" if test["value_kind"] == "observation" else test["value_kind"], f"{tid}: {p['quantity']}", raw, tid,
                     result.get("evidence") or [], test["assumptions"] if test["value_kind"] == "conditional" else None)
        return {"test_id": tid, "prediction_id": pid, "question": p["question"], "quantity": p["quantity"], "value": raw, "value_kind": test["value_kind"],
                "predictions": p["predictions"], "update": update, "computation": result}

    # ---- distinctions and the computations that could discriminate them ---------------------------------------------
    def competing(self) -> List[str]:
        """Causes still competing: the assessment's open upstream causes, and hypotheses the agent added (not combinations)."""
        return [h["id"] for h in self.hypotheses.values() if not h.get("mechanisms") and
                ((h["source"] == "physical_assessment" and h.get("role") == "upstream_cause" and h.get("assessment_status") in COMPETING_STATUSES)
                 or (h["source"] in ("agent", "dynamics") and not h.get("combines")))]

    @staticmethod
    def _key(a: str, b: str) -> str:
        return "|".join(sorted((a, b)))

    def distinctions(self) -> List[Dict[str, Any]]:
        ids = self.competing()
        out = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ha, hb = self.hypotheses[a], self.hypotheses[b]
                pa, pb = ha.get("parent"), hb.get("parent")
                if pa and pa == pb:
                    continue                       # two mechanisms of one cause: they may act together; not a rival pair
                fa, fb = ha.get("family"), hb.get("family")
                if fa and fb and fa != fb:
                    continue                       # different questions (e.g. why the estimate was wrong vs which element limited): may both hold
                ca = set(ha.get("coexists_with") or []) | set(self.hypotheses.get(pa or "", {}).get("coexists_with") or [])
                cb = set(hb.get("coexists_with") or []) | set(self.hypotheses.get(pb or "", {}).get("coexists_with") or [])
                if b in ca or a in cb or (pb and pb in ca) or (pa and pa in cb):
                    continue                       # declared able to act together (e.g. group-wide and one member)
                k = self._key(a, b)
                sep = self.separated(a, b) + self.separated(b, a)
                tested = [t["id"] for t in self.counted() if self._differs(self.predictions[t["prediction_id"]], a, b)]
                state = "separated" if sep else "declared_not_discriminable" if k in self.declarations else "tested_not_separated" if tested else "open"
                out.append({"pair": [a, b], "state": state, "separated_by": sep, "tested_by": tested, "declaration": self.declarations.get(k)})
        return out

    @staticmethod
    def _differs(pred: Dict[str, Any], a: str, b: str) -> bool:
        ex = {x["hypothesis"]: _interval(x["expect"]) for x in pred.get("predictions") or []}
        return a in ex and b in ex and ex[a] != ex[b]

    def _addresses(self, pred: Dict[str, Any]) -> List[List[str]]:
        return [d["pair"] for d in self.distinctions() if d["state"] != "separated" and self._differs(pred, *d["pair"])]

    def instruments(self) -> List[Dict[str, Any]]:
        """The computations applicable to this recording's signals (by role kind and dimension, never by name of a machine)."""
        p, rec = self.ctx.profile, self.ctx.rec
        dims: Dict[str, List[str]] = {}
        for r, spec in p.roles.items():
            if spec.get("kind", "measurement") == "measurement":
                dims.setdefault(compute._dimension(spec.get("unit", "")), []).append(r)
        cur, volt = dims.get("current", []), dims.get("voltage", [])
        cells = [r for r in volt if "cell" in r]
        packs = [r for r in volt if "cell" not in r]
        states = [r for r, spec in p.roles.items() if spec.get("kind") == "state"]
        soc = [r for r, spec in p.roles.items() if spec.get("kind") == "estimate" and compute._dimension(spec.get("unit", "")) == "fraction"]
        ran = {t["result"]["op"] for t in self.tests.values()} | {json.loads(k)["op"] for k in self.explored}
        cand = [("integrate", bool(cur), "charge counted between two points (a current over time)"),
                ("capacity", bool(cur and soc), "capacity implied by counted charge under declared SOC anchors"),
                ("step_response", bool(cur and volt), "voltage change per current step: source resistance, sensor scale"),
                ("transitions", bool(states and (cur or volt)), "what current and voltage do when a state (a load, a mode) changes"),
                ("unexplained_changes", bool(cur and volt), "voltage moving with no change in measured current"),
                ("topology", bool(packs and cells), "series count from pack and cell voltages"),
                ("crosscheck", len(cur) >= 2 or len(packs) >= 2, "two signals of the same quantity: agreement and independence"),
                ("compare_windows", len(rec.windows) >= 2, "the same quantity in different windows")]
        if dynamics_enabled():
            meas = [r for r, spec in p.roles.items() if spec.get("kind", "measurement") == "measurement"]
            cand += [("rate", bool(meas), "rate of change on actual timestamps (adaptive window), acceleration"),
                     ("departure", bool(meas), "when a signal leaves its baseline relation to its drivers, and how long before an event"),
                     ("divergence", bool(self.dyn) or len(cells) >= 2, "comparable signals: group-wide vs one member, onset, lead, load coupling, jumps")]
        return [{"op": op, "measures": why, "run": op in ran} for op, ok, why in cand if ok]

    def tied(self) -> List[str]:
        """The competing causes no counted test has contradicted (the set the next test should split); all of them if every one was."""
        comp = self.competing()
        alive = [h for h in comp if self.standing(h)["standing"] != "contradicted"]
        return alive or comp

    def expected_bits(self, pred: Dict[str, Any]) -> float:
        """Expected reduction of uncertainty (bits, uniform over the tied causes) if this prediction's test runs: causes whose
        predicted regions overlap cannot be told apart by it; a tied cause it makes no prediction for stays with every group."""
        tied = self.tied()
        n = len(tied)
        if n < 2:
            return 0.0
        ex = {x["hypothesis"]: _interval(x["expect"]) for x in pred.get("predictions") or []}
        named = [h for h in tied if h in ex]
        groups: List[List[str]] = []
        for h in sorted(named, key=lambda h: ex[h][0]):
            if groups and ex[h][0] < max(ex[g][1] for g in groups[-1]):        # touching bounds (gt x vs lt/between ..x) do not overlap
                groups[-1].append(h)
            else:
                groups.append([h])
        unnamed = n - len(named)
        rest = sum((len(g) / n) * math.log2(len(g) + unnamed) for g in groups) + (unnamed / n) * math.log2(n)
        return round(max(0.0, math.log2(n) - rest), 3)

    def plan(self) -> Dict[str, Any]:
        ds = self.distinctions()
        opened = [d for d in ds if d["state"] in ("open", "tested_not_separated")]
        inst = self.instruments()
        pending = [{"prediction_id": p["id"], "question": p["question"], "addresses_open_distinctions": self._addresses(p),
                    "expected_bits": self.expected_bits(p)}
                   for p in self.predictions.values() if p["status"] == "registered"]
        pending.sort(key=lambda x: (-x["expected_bits"], -len(x["addresses_open_distinctions"])))
        return {"strategy": strategy(), "competing": self.competing(), "tied": self.tied(), "distinctions": ds, "open": [d["pair"] for d in opened],
                "registered_not_tested": pending, "not_run": [i for i in inst if not i["run"]], "applicable": inst,
                "rule": "Close each open distinction with an executed test that separates it, or with inquiry_distinguishable after every applicable "
                        "computation has been run. Highest value next: a registered prediction that addresses the most open distinctions, else an "
                        "applicable computation not run yet, chosen for the distinction it could split."}

    def _declare(self, args: Dict[str, Any]) -> Dict[str, Any]:
        pair = [str(x) for x in args.get("pair") or []]
        comp = self.competing()
        if len(pair) != 2 or pair[0] == pair[1] or any(x not in comp for x in pair):
            raise ValueError(f"pair must name two competing causes ({', '.join(comp)})")
        looked = [x for x in args.get("looked_at") or []]
        known = set(self.tests) | {e["payload"]["id"] for e in self.entries if e["kind"] == "compute"}
        if not looked or any(x not in known for x in looked):
            raise ValueError(f"looked_at must cite your computations or tests (known: {', '.join(sorted(known)) or 'none yet'})")
        if self.separated(*pair) or self.separated(pair[1], pair[0]):
            raise ValueError(f"{pair} was already separated by a test")
        d = {"pair": pair, "reason": args.get("reason") or "", "looked_at": looked, "would_separate": args.get("would_separate") or "",
             "not_run_at_declaration": [i["op"] for i in self.instruments() if not i["run"]]}
        if not d["reason"].strip() or not d["would_separate"].strip():
            raise ValueError("reason and would_separate are required")
        self.declarations[self._key(*pair)] = d
        self._append("declaration", d)
        self._record("assumption", f"{pair[0]} vs {pair[1]} not discriminable on this recording: {d['reason']}", None, "declaration", [])
        return {"recorded": d, "note": "It closes the distinction at submit only once every applicable computation has been run" +
                                       (f"; not run yet: {', '.join(d['not_run_at_declaration'])}" if d["not_run_at_declaration"] else "")}

    # ---- state ----------------------------------------------------------------------------------------------------
    def counted(self) -> List[Dict[str, Any]]:
        return [t for t in self.tests.values() if t["update"]["counts"]]

    def _leaf_separated(self, upper: str, lower: str) -> List[str]:
        return [t["id"] for t in self.counted() if upper in t["update"]["consistent"] and lower in t["update"]["contradicted"]]

    def separated(self, upper: str, lower: str) -> List[str]:
        """Ids of counted tests placing upper above lower. A hypothesis with mechanisms is above another when ANY of its mechanisms
        is; below another only when EVERY mechanism is (a test of one mechanism says nothing about the others)."""
        um = self.hypotheses.get(upper, {}).get("mechanisms") or [upper]
        lm = self.hypotheses.get(lower, {}).get("mechanisms") or [lower]
        per_lower = []
        for l in lm:
            ids = sorted({x for u in um for x in self._leaf_separated(u, l)})
            if not ids:
                return []
            per_lower.append(ids)
        return sorted({x for ids in per_lower for x in ids})

    def standing(self, hid: str) -> Dict[str, Any]:
        mech = self.hypotheses.get(hid, {}).get("mechanisms")
        if mech:
            subs = {m: self.standing(m) for m in mech}
            st = "contradicted" if all(x["standing"] == "contradicted" for x in subs.values()) else \
                 "survived" if any(x["standing"] == "survived" for x in subs.values()) else "untested"
            return {"consistent_with": sorted({t for x in subs.values() for t in x["consistent_with"]}), "contradicted_by": [] if st != "contradicted" else
                    sorted({t for x in subs.values() for t in x["contradicted_by"]}), "independent_contradictions": 0, "standing": st,
                    "mechanisms": {m: x["standing"] for m, x in subs.items()}}
        ts = self.counted()
        cons = [t["id"] for t in ts if hid in t["update"]["consistent"]]
        contra = [t["id"] for t in ts if hid in t["update"]["contradicted"]]
        groups_c = {json.dumps(t["update"]["evidence_group"]) for t in ts if hid in t["update"]["contradicted"]}
        ph = [t for t in self.tests.values() if not t["update"]["counts"]]
        return {"consistent_with": cons, "contradicted_by": contra, "independent_contradictions": len(groups_c),
                "standing": "contradicted" if contra else "survived" if cons else "untested",
                "post_hoc_consistent": [t["id"] for t in ph if hid in t["update"]["consistent"]],
                "post_hoc_contradicted": [t["id"] for t in ph if hid in t["update"]["contradicted"]]}

    def assessment_after_tests(self) -> Dict[str, Any]:
        """inquiry-v4: what every executed test did to every hypothesis, deterministically. The physical assessment's status
        is the prior; a counted contradiction WEAKENS (never excludes); a counted test that separated a hypothesis from a rival
        SUPPORTS it relative to that rival; post-hoc results (prediction written after the computation was seen) are listed
        and lower the confidence of the label, but never separate. Provenance: test ids, their registration hashes, and the
        exploration a test ran through."""
        out = []
        ids = [h for h in self.hypotheses]
        for hid, h in self.hypotheses.items():
            st = self.standing(hid)
            over = sorted({r for r in ids if r != hid and self.separated(hid, r)})
            under = sorted({r for r in ids if r != hid and self.separated(r, hid)})
            if st["standing"] == "contradicted":
                upd = "weakened"
            elif over and not under:
                upd = "supported"
            elif st["standing"] == "survived":
                upd = "consistent"
            elif st.get("post_hoc_contradicted"):
                upd = "weakened_post_hoc"
            elif st.get("post_hoc_consistent"):
                upd = "consistent_post_hoc"
            else:
                upd = "untested"
            tids = sorted(set(st["consistent_with"]) | set(st["contradicted_by"]) | set(st.get("post_hoc_consistent") or []) |
                          set(st.get("post_hoc_contradicted") or []))
            out.append({"id": hid, "statement": h.get("statement", "")[:200], "source": h.get("source"), "prior_status": h.get("assessment_status"),
                        "after_tests": upd, "above": over, "below": under, **{k: st.get(k) for k in ("consistent_with", "contradicted_by",
                        "post_hoc_consistent", "post_hoc_contradicted", "mechanisms")},
                        "provenance": [{"test": t, "prediction": self.tests[t]["prediction_id"], "registered_at": self.tests[t].get("registered_at"),
                                        "via_exploration": self.tests[t].get("via_exploration"), "source": self.predictions[self.tests[t]["prediction_id"]].get("source")}
                                       for t in tids if t in self.tests]})
        return {"rule": "prior = the physical assessment's status; counted contradiction -> weakened (never excluded); separated above a rival by a "
                        "counted test and below none -> supported; post-hoc results listed, never separating", "hypotheses": out}

    def view(self) -> Dict[str, Any]:
        ids = list(self.hypotheses)
        pairs, unseparated = [], []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ab, ba = self.separated(a, b), self.separated(b, a)
                if ab or ba:
                    pairs.append({"pair": [a, b], f"{a}_over_{b}": ab, f"{b}_over_{a}": ba, "conflicting": bool(ab and ba)})
                else:
                    unseparated.append([a, b])
        by_kind: Dict[str, int] = {}
        for r in self.records:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        return {"version": INQUIRY_VERSION,
                "hypotheses": [{**h, **self.standing(h["id"])} for h in self.hypotheses.values()],
                "separations": pairs, "not_separated": unseparated,
                "predictions": [{k: v for k, v in p.items() if k != "key"} for p in self.predictions.values()],
                "tests": [{"id": t["id"], "prediction_id": t["prediction_id"], "quantity": t["quantity"], "value": t["value"], "value_kind": t["value_kind"],
                           "update": t["update"]} for t in self.tests.values()],
                "records_by_kind": by_kind, "distinctions": self.distinctions(), "instruments": self.instruments(),
                "summary": {"tests_executed": len(self.tests), "counted": len(self.counted()), "post_hoc": sum(not t["update"]["counts"] for t in self.tests.values()),
                            "discriminating": sum(t["update"]["discriminating"] for t in self.tests.values()), "explorations": self._n["X"],
                            "distinctions": len(self.distinctions()), "separated": sum(d["state"] == "separated" for d in self.distinctions()),
                            "declared": sum(d["state"] == "declared_not_discriminable" for d in self.distinctions()),
                            "open": sum(d["state"] in ("open", "tested_not_separated") for d in self.distinctions())}}

    def export(self) -> Dict[str, Any]:
        return {**self.view(), "assessment_after_tests": self.assessment_after_tests(), "strategy": strategy(), "records": self.records, "entries": self.entries, "chain_head": self.entries[-1]["hash"] if self.entries else None,
                "call_ids": self.call_ids}

    # ---- held on submit -------------------------------------------------------------------------------------------
    def _ledger_id(self, h: Dict[str, Any]) -> Optional[str]:
        ref = (h.get("physical_ref") or "").strip()
        if ref and ref.lower() != "none" and ref in self.hypotheses:
            return ref
        return h.get("id") if h.get("id") in self.hypotheses else None

    def problems(self, report: Dict[str, Any], outputs: Optional[Dict[str, str]] = None) -> List[str]:
        out: List[str] = []
        if outputs is not None:
            import re
            num = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
            for i, tier in enumerate(report.get("ranking") or []):
                pool = " ".join(outputs.get(self.call_ids.get(t, ""), "") for t in tier.get("tests") or [])
                bad = [x for x in num.findall(tier.get("basis") or "") if len(x) > 1 and x not in pool and x.lstrip("-") not in pool]
                if bad:
                    out.append(f"ranking[{i}].basis: number(s) {', '.join(bad[:4])} are not in the cited tests' results")
        competing = len(self.hypotheses) >= 2
        if competing and not self.counted():
            out.append("inquiry: no prediction test was executed. Register what the competing causes predict for a quantity computed from this "
                       "recording (inquiry_predict) and run it (inquiry_test) before you submit; a prediction for a computation you already ran does not count")
        not_run = [i["op"] for i in self.instruments() if not i["run"]]
        strat = strategy()
        for d in ([] if strat == "free" else self.distinctions()):
            a, b = d["pair"]
            if d["state"] == "separated":
                continue
            if strat == "infogain":
                if d["state"] == "open":
                    out.append(f"inquiry: {a} vs {b} is still tied and no executed test predicted differently for them. Run the test with the "
                               "highest expected_bits that splits them (inquiry_plan), or, if no computation on this recording can, record it with "
                               "inquiry_distinguishable")
                continue
            if d["state"] != "declared_not_discriminable":
                out.append(f"inquiry: the distinction {a} vs {b} is unresolved. Establish whether this recording can discriminate it: register what each "
                           f"predicts for a computable quantity and test it (applicable computations not run yet: {', '.join(not_run) or 'none'}), "
                           "or, if none can, record it with inquiry_distinguishable")
            elif not_run:
                out.append(f"inquiry: {a} vs {b} is declared not discriminable, but applicable computations were never run: {', '.join(not_run)}. "
                           "Run them (as tests where the causes predict differently) before concluding")
        ranking = report.get("ranking") or []
        tiers: List[List[str]] = []
        for i, tier in enumerate(ranking):
            hs = [str(x) for x in tier.get("hypotheses") or []]
            unknown = [x for x in hs if x not in self.hypotheses]
            if unknown:
                out.append(f"ranking[{i}]: unknown inquiry hypothesis id(s) {unknown} (ledger: {', '.join(self.hypotheses)})")
            bad_tests = [t for t in tier.get("tests") or [] if t not in self.tests]
            if bad_tests:
                out.append(f"ranking[{i}]: unknown inquiry test id(s) {bad_tests}")
            tiers.append([x for x in hs if x in self.hypotheses])
        flat = [x for t in tiers for x in t]
        dup = sorted({x for x in flat if flat.count(x) > 1})
        if dup:
            out.append(f"ranking: {dup} placed in more than one tier")
        for i, upper in enumerate(tiers):
            for lower in tiers[i + 1:]:
                for u in upper:
                    for l in lower:
                        if u == l:
                            continue
                        if not self.separated(u, l):
                            out.append(f"ranking: {u} is ranked above {l}, but no executed test separated them ({u} consistent, {l} contradicted): "
                                       "put them in one tier (indistinguishable on this data, or acting together) or run a test that separates them")
                        elif self.separated(l, u):
                            out.append(f"ranking: tests disagree on {u} vs {l} ({', '.join(self.separated(u, l))} vs {', '.join(self.separated(l, u))}): "
                                       "put them in one tier and say which assumption each test depends on")
        if tiers:                                  # the first tier may not keep a cause that a test contradicted while a rival in it survived
            for u in tiers[0]:
                st = self.standing(u)
                if st["standing"] != "contradicted":
                    continue
                above = [o for o in tiers[0] if o != u and self.separated(o, u) and not self.separated(u, o)]
                if above:
                    out.append(f"ranking[0]: {u} is in the first tier, but executed test(s) {', '.join(st['contradicted_by'])} contradicted it while "
                               f"{', '.join(above)} in the same tier survived them: place {u} in a lower tier, or show with a further test which "
                               "assumption of those tests fails")
        hyps = report.get("hypotheses") or []
        mains = [(h, self._ledger_id(h)) for h in hyps if h.get("role") == "main_cause" and h.get("status") != "ruled_out"]
        for h, lid in mains:
            if lid is None or h.get("status") != "leading":
                continue
            st = self.standing(lid)
            if st["contradicted_by"]:
                out.append(f"hypothesis {h.get('id')} ({lid}) is leading but test(s) {', '.join(st['contradicted_by'])} contradicted it: "
                           "weaken it, or show with a further test which assumption of those tests fails")
            for g, gid in mains:
                if g is h or gid is None or gid == lid:
                    continue
                if not self.separated(lid, gid):
                    out.append(f"hypothesis {h.get('id')} ({lid}) is leading over main cause {g.get('id')} ({gid}) but no executed test separated them: "
                               "keep both open (or one ranking tier) or run a test that separates them")
        return out
