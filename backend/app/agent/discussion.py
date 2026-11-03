"""Conversational debugging: the engineer brings a guess or a bench result, Veyra tests it against the data, says which
parts hold, updates where the fault can be, and proposes the next check. One call is one round.

  engineer guess  ->  Veyra queries the original data (read-only tools)  ->  each part of the guess: holds / fails /
  partly / can't tell, with the lines that decide it  ->  fault space updated  ->  next check (an experiment with
  outcomes)  ->  engineer's result  ->  next round.

Held to the same rules as the investigator, enforced here and not left to the prompt:
- every quoted line must be the original line; every number must appear in a cited tool output or line;
- claim scope: an observed claim may only say what the evidence could observe (an agent-side log shows what the agent
  received and sent, never what the board transmitted);
- a hypothesis is ruled out only on direct exclusion visible in the data; an engineer's report can weaken, not exclude;
- the engineer's message is evidence only for what they say they observed; a guess is a claim to test, not a fact.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from app.agent import investigator as inv

DISCUSSION_VERSION = "veyra-discussion-v1"
MAX_TURNS = 12

SYSTEM_PROMPT = """You are Veyra, a field-investigation engineer, debugging a failure WITH an engineer. They bring either a
guess (what they think the cause is, or a question) or a result from a check they ran. You do the data work.

Each round:
1. Read case_context and the current state (the investigation so far, earlier rounds of this discussion).
2. Split the engineer's message into its separate, testable parts. A guess like "the agent rejects the board because of
   the port, then the board retries forever" is three parts. A bench result is evidence for what they say they
   OBSERVED (and only that): restate each observation as a claim and check it against the data where the data can see it.
3. Test each part with the tools on the ORIGINAL data: read lines, search, measure counts and intervals, decode the
   packets. Typically 2-6 tool calls. For each part give a verdict: holds, fails, partly, cannot_tell, with the lines
   or tool results that decide it, and where that evidence was observed. Say plainly when the data cannot see the
   thing the claim is about (for example the board's transmit buffer, the cable, the switch).
4. Update where the fault can be: the full list of hypotheses with ids kept from earlier, status leading / open /
   weakened / ruled_out and what decided it. ruled_out needs DIRECT exclusion visible in the data (basis direct,
   excluded_by data). An engineer's report can weaken an area or make one leading, never rule one out.
5. Propose the ONE next check that best splits what is still standing: an experiment a field engineer can do, with
   2-3 possible results and what each would keep, rule out or weaken. Never propose what you could check yourself.
6. reply: two or three sentences to the engineer, direct and specific. Disagree when the data disagrees.

Claim scope: the evidence has a vantage point. A log written by the receiving side shows what it received and sent;
it never observes what the sender transmitted or what was on the cable. Write observed claims from the vantage point
("the agent received 36 zero bytes"), and mark anything beyond it as inferred.
Every number you write must come from a tool result you cite or a line you quote. Quote lines exactly.
Never claim a confirmed root cause. Name hypotheses by area in text people read; ids only in keeps / rules_out / weakens.
Finish by calling submit_round exactly once."""

SUBMIT_ROUND = {
    "name": "submit_round",
    "description": "Submit this round of the discussion (once).",
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "To the engineer, 2-3 sentences, under 70 words"},
            "parts": {"type": "array", "minItems": 1, "maxItems": 5, "description": "Each testable part of the engineer's message", "items": {
                "type": "object", "properties": {
                    "claim": {"type": "string", "description": "The part, restated as a testable claim, under 20 words"},
                    "verdict": {"type": "string", "enum": ["holds", "fails", "partly", "cannot_tell"]},
                    "why": {"type": "string", "description": "What decided it, under 40 words"},
                    "kind": {"type": "string", "enum": ["observed", "inferred", "reported"], "description": "observed: visible in the data you cite; inferred: reasoning from it; reported: rests on the engineer's report"},
                    "observed_at": {"type": "string", "description": "Where the deciding evidence was observed, e.g. 'agent log on the host'"},
                    "evidence": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
                        "line": {"type": "integer"}, "quote": {"type": "string"}}, "required": ["line", "quote"]}},
                    "calls": {"type": "array", "items": {"type": "string"}, "description": "Tool call ids that tested it"}},
                "required": ["claim", "verdict", "why", "kind", "calls"]}},
            "hypotheses": inv.SUBMIT_SPEC["input_schema"]["properties"]["hypotheses"],
            "next_check": inv.SUBMIT_SPEC["input_schema"]["properties"]["next_action"],
            "still_unknown": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        },
        "required": ["reply", "parts", "hypotheses", "next_check"],
    },
}

PACKETS_SPEC = {"name": "packets", "description": "For a micro-ROS agent log (-v6): every packet the agent received or sent, decoded from the hex lines: "
                "line, time, direction (received/sent), length, whether every byte is zero, the XRCE header fields (session id, stream, "
                "client key) and first submessage (id, length), and whether that fits the packet.",
                "input_schema": {"type": "object", "properties": {}}}
TOOLS = [t for t in inv.TOOL_SPECS if t["name"] != "lessons"] + [PACKETS_SPEC]
STEP_LABEL = {**inv.STEP_LABEL, "packets": "Decoded every packet in the log"}

# Claim scope for agent-side logs (mirrors frontend/lib/claim-scope.ts OUT_OF_SCOPE).
OUT_OF_SCOPE = re.compile(r"\b[Bb]oard (?:then )?(?:only |still |also )?(?:sent|sends|keeps (?:re)?sending|kept (?:re)?sending|transmits|transmitted|repeated|repeats|resent|resends)\b"
                          r"|\bon the wire\b|\breaches the wire\b|\bleaving the board\b|\bsent by the board\b")

_EVT = re.compile(r"^\[(\d+(?:\.\d+)?)\]\s+\w+\s*\|[^|]*\|\s*(recv_message|send_message)\s*\|[^|]*\|\s*(.*)$")
_HEX = re.compile(r"^[0-9A-Fa-f]{4}:\s*((?:[0-9A-Fa-f]{2}\s*)+)$")
_SUB = {0: "CREATE_CLIENT", 1: "CREATE", 2: "GET_INFO", 3: "DELETE", 4: "STATUS_AGENT", 5: "STATUS", 6: "INFO", 7: "WRITE_DATA", 8: "READ_DATA",
        9: "DATA", 10: "ACKNACK", 11: "HEARTBEAT", 12: "RESET", 13: "FRAGMENT", 14: "TIMESTAMP", 15: "TIMESTAMP_REPLY"}


def decode_packets(raw: List[str]) -> Dict[str, Any]:
    """The packets of a micro-ROS agent verbose log (same reading as the page's decoder)."""
    out, cur = [], None
    for n, text in enumerate(raw, 1):
        h = _HEX.match(text.strip())
        if h and cur is not None:
            cur["bytes"] += [int(x, 16) for x in h.group(1).split()]
            cur["hex_lines"].append(n)
            continue
        m = _EVT.match(text.strip())
        if m:
            fields = dict(re.findall(r"(\w+):\s*([^,]*)", m.group(3)))
            cur = {"line": n, "t": float(m.group(1)), "direction": "received" if m.group(2) == "recv_message" else "sent",
                   "length": int(fields.get("len", "0") or 0), "client_key_logged": fields.get("client_key", "").strip(), "bytes": [], "hex_lines": []}
            out.append(cur)
        else:
            cur = None
    for p in out:
        b = p.pop("bytes")
        p["bytes_in_log"] = len(b)
        p["all_zero"] = bool(b) and all(x == 0 for x in b)
        p["first_bytes"] = " ".join(f"{x:02X}" for x in b[:16])
        if len(b) >= 8:
            sess = b[0]
            hl = 8 if sess < 0x80 else 4
            p["session_id"] = f"0x{sess:02X}"
            if sess < 0x80:
                p["client_key_in_bytes"] = "0x" + "".join(f"{x:02X}" for x in b[4:8])
            if len(b) >= hl + 4:
                sid, ln = b[hl], b[hl + 2] | (b[hl + 3] << 8)
                p["submessage"] = {"id": sid, "name": _SUB.get(sid, f"unknown {sid}"), "length": ln, "fits": hl + 4 + ln <= len(b)}
    return {"packets": out, "note": "Directions are from the agent's side: what the agent received and sent. What the other end transmitted is not observed here."}


def _state(store, case_id: str) -> Dict[str, Any]:
    latest = inv.latest(store, case_id)
    r = (latest or {}).get("result") or {}
    rounds = rounds_of(store, case_id)
    return {"investigation": {k: r.get(k) for k in ("headline", "explanation", "status")} | {"hypotheses": inv.reviewed_hypotheses(r.get("hypotheses"))[0],
                                                                                        "next_action": (r.get("next_action") or {}).get("action")},
            "discussion_so_far": [{"engineer": x["message"], "kind": x["message_kind"],
                                   "veyra": {"reply": (x.get("result") or {}).get("reply"), "next_check": ((x.get("result") or {}).get("next_check") or {}).get("action"),
                                             "parts": [{k: p.get(k) for k in ("claim", "verdict")} for p in (x.get("result") or {}).get("parts") or []]}}
                                  for x in rounds],
            "current_hypotheses": current_hypotheses(store, case_id)}


def rounds_of(store, case_id: str) -> List[Dict[str, Any]]:
    return [{**e["payload"], "entry_id": e["id"], "recorded_at": e["recorded_at"]} for e in store.ledger(case_id)
            if e["kind"] == "agent_report" and e["payload"].get("kind") == "discussion_round"]


def current_hypotheses(store, case_id: str) -> List[Dict[str, Any]]:
    rounds = [r for r in rounds_of(store, case_id) if (r.get("result") or {}).get("hypotheses")]
    if rounds:
        return inv.reviewed_hypotheses(rounds[-1]["result"]["hypotheses"])[0]
    r = (inv.latest(store, case_id) or {}).get("result") or {}
    return inv.reviewed_hypotheses(r.get("hypotheses"))[0]


def verify_round(report: Dict[str, Any], raw: List[str], outputs: Dict[str, str]) -> Dict[str, Any]:
    problems: List[str] = []
    cleaned = json.loads(json.dumps(report))
    pool_all = " ".join(outputs.values())
    keep = []
    for i, p in enumerate(cleaned.get("parts") or []):
        calls = p.get("calls") or []
        unknown = [c for c in calls if c not in outputs]
        err = None
        if unknown:
            err = f"parts[{i}]: unknown tool call id(s) {unknown}"
        for j, e in enumerate(p.get("evidence") or []):
            n, q = e.get("line"), inv._norm(e.get("quote"))
            if not isinstance(n, int) or not 1 <= n <= len(raw):
                err = f"parts[{i}].evidence[{j}]: line {n} does not exist"
            elif q and q.rstrip(".…") not in inv._norm(raw[n - 1]):
                err = f"parts[{i}].evidence[{j}]: the quote is not in line {n} (it reads {raw[n - 1][:120]!r})"
        if not err:
            lines_ = [e.get("line") for e in p.get("evidence") or []]
            pool = " ".join(outputs.get(c, "") for c in calls) + " " + " ".join(raw[n - 1] for n in lines_ if isinstance(n, int) and 1 <= n <= len(raw))
            bad = [x for x in inv._NUM.findall(f"{p.get('claim', '')} {p.get('why', '')}") if len(x) > 1 and x not in pool and x.lstrip("-") not in pool and x not in pool_all]
            if bad and p.get("kind") != "reported":
                err = f"parts[{i}]: number(s) {', '.join(bad[:4])} not found in the cited tool results or lines"
        if not err and p.get("kind") == "observed" and OUT_OF_SCOPE.search(f"{p.get('claim', '')} {p.get('why', '')}"):
            err = (f"parts[{i}]: claim scope. This log is recorded by the agent on the host; it shows what the agent received, not what the board "
                   "sent or what was on the wire. Rewrite it from the agent's side, or mark it inferred")
        (problems.append(err) or None) if err else keep.append(p)
    cleaned["parts"] = keep
    if not keep:
        problems.append("parts: at least one tested part is required")
    ids = {h.get("id") for h in cleaned.get("hypotheses") or []}
    for h in cleaned.get("hypotheses") or []:
        if h.get("status") == "ruled_out" and (h.get("basis") != "direct" or h.get("excluded_by") != "data"):
            problems.append(f"hypothesis '{h.get('area')}' is ruled_out without direct exclusion visible in the data: mark it weakened")
    for i, o in enumerate(((cleaned.get("next_check") or {}).get("outcomes")) or []):
        unknown = [x for x in (o.get("keeps") or []) + (o.get("rules_out") or []) + (o.get("weakens") or []) if x not in ids]
        if unknown:
            problems.append(f"next_check.outcomes[{i}]: unknown hypothesis id(s) {unknown}")
    if inv._CONFIRMED.search(cleaned.get("reply") or ""):
        problems.append("Do not state a confirmed root cause in the reply")
    cleaned["hypotheses"] = inv.reviewed_hypotheses(cleaned.get("hypotheses"))[0]
    return {"problems": problems, "cleaned": cleaned}


def discuss(store, case_id: str, message: str, *, kind: str = "guess", model=None, actor: str = "Engineer",
            on_step: Callable[[Dict[str, Any]], None] = lambda s: None, max_turns: int = MAX_TURNS, lang: Optional[str] = None) -> Dict[str, Any]:
    """One round. kind: 'guess' (a suspected cause or a question) or 'result' (what the engineer found)."""
    from app.agent.runner import egress_allowed
    started = time.time()
    message = (message or "").strip()[:1500]
    if not message:
        raise ValueError("Say what you think, or what you found")
    tools = inv.LogTools(store, case_id)
    if model is None or not model.available():
        raise ValueError("The AI investigator is not configured on this instance")
    if not tools.available():
        raise ValueError("The original log is not stored for this case")
    if not egress_allowed(store, case_id, model.provider):
        raise ValueError(f"No permission recorded to send this case's data to {model.provider}")
    run = store.latest_run(case_id)
    if run is None:
        raise ValueError("Investigate the case first")
    if kind == "result":
        # A bench result is the engineer's report: it feeds later investigation rounds like any engineer finding.
        last = rounds_of(store, case_id)
        action = (((last[-1].get("result") or {}).get("next_check") or {}).get("action") if last else None) or "Engineer check (from the discussion)"
        store.append(case_id, "engineer_finding", {"action": action[:500], "result": "found", "observations": message}, actor=actor, source="discussion")
    steps: List[Dict[str, Any]] = []
    outputs: Dict[str, str] = {}
    calls_log: List[Dict[str, Any]] = []
    first = (f"The engineer's {'result from a check they ran' if kind == 'result' else 'guess or question'}:\n\"{message}\"\n\n"
             "Current state (investigation so far and this discussion):\n" + json.dumps(_state(store, case_id), default=str)[:12000] +
             "\n\nTest it with the tools, then call submit_round.")
    messages: List[Dict[str, Any]] = [{"role": "user", "content": first}]
    result, attempts, failure = None, 0, None
    try:
        for _ in range(max_turns):
            t0 = time.time()
            reply = model.create(system=inv.prompt_for(SYSTEM_PROMPT, lang), messages=messages, tools=TOOLS + [SUBMIT_ROUND])
            calls_log.append(inv.model_call_record(len(calls_log) + 1, reply, int((time.time() - t0) * 1000), getattr(model, "model", None)))
            content = reply.get("content") or []
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": "Test it with the tools, then call submit_round."})
                continue
            results = []
            for u in uses:
                name, args = u["name"], dict(u.get("input") or {})
                if name == "submit_round":
                    attempts += 1
                    checked = verify_round(args, tools.raw, outputs)
                    if checked["problems"] and attempts <= inv.MAX_FIX_ATTEMPTS:
                        results.append({"type": "tool_result", "tool_use_id": u["id"], "is_error": True,
                                        "content": "Fix these and call submit_round again: " + "; ".join(checked["problems"][:8])})
                        continue
                    result = checked["cleaned"]
                    results.append({"type": "tool_result", "tool_use_id": u["id"], "content": "Accepted."})
                    continue
                t1 = time.time()
                try:
                    if name == "case_context":
                        value = inv._case_context(store, case_id)
                    elif name == "packets":
                        value = decode_packets(tools.raw)
                    elif name in ("overview", "read_lines", "search", "measure"):
                        value = getattr(tools, name)(**args)
                    else:
                        raise ValueError(f"Unknown tool {name}")
                    text, error = json.dumps(value, default=str)[:24000], None
                except Exception as exc:  # noqa: BLE001
                    value, text, error = None, f"{type(exc).__name__}: {exc}", True
                outputs[u["id"]] = text
                step = {"id": u["id"], "tool": name, "input": args, "label": STEP_LABEL.get(name, name).format(**{"start": "", "end": "", "pattern": "", **args}),
                        "summary": ("error: " + text[:120]) if error else (f"{len(value['packets'])} packets" if name == "packets" else inv._summary(name, value)),
                        "ms": int((time.time() - t1) * 1000)}
                steps.append(step)
                on_step(step)
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": text, **({"is_error": True} if error else {})})
            messages.append({"role": "user", "content": results})
            if result is not None:
                break
        if result is None:
            failure = "Veyra did not finish this round within its step budget"
    except Exception as exc:  # noqa: BLE001
        failure = f"The model step failed: {exc}"[:300]
    payload = {"kind": "discussion_round", "version": DISCUSSION_VERSION, "run_id": run["id"], "message": message, "message_kind": kind,
               "model": getattr(model, "model", None), "steps": steps, "result": result, "failure": failure,
               "usage": inv.usage_totals(calls_log), "model_calls": calls_log, "seconds": round(time.time() - started, 1),
               "boundary": "Read-only checks over the original data. The engineer's message is a claim to test; their observations are reported, not verified."}
    entry = store.append_trusted(case_id, "agent_report", payload, actor="Veyra", source=DISCUSSION_VERSION)
    return {**payload, "entry_id": entry["id"], "recorded_at": entry["recorded_at"]}
