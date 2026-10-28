# Veyra v2: end-to-end architecture, and a review of the reuse plan

Status: implemented locally (not deployed). Date: 2026-10-28.

## 1. Decision in one paragraph

Do not assemble Veyra from RosHD + AURA + LangGraph + AgentScope. The parts of those projects that matter already exist
in Veyra in a stronger form, and the parts that do not exist in Veyra are not in those projects either. Log reading and
agentic root-cause analysis are now a commodity (Roboto Agents shipped triage and RCA over rosbags / MCAP in July 2026;
Hexar.ai, ROS Help Desk, mcp-ros2-logs). Veyra's product is the part nobody else does: evidence discipline (where a
signal was measured, what came first, what is chance, what is a label), the decision ledger, and lessons that are
re-used only where they apply. So v2 adds four things on top of the existing engine: text logs, lessons with
applicability checks, an MCP interface so any agent can use Veyra's evidence engine, and an upload-to-answer flow that
gives an engineer results in seconds without an API key.

## 2. Fact check of the reuse plan (repositories read on 2026-10-28)

| Claim in the plan | What the code shows | Consequence |
|---|---|---|
| RosHD is BSD-3 and reusable | True: `LICENSE` grants BSD-3-Clause (CSIRO). | Licence is fine. |
| RosHD gives a "Data & Evidence Toolkit" (read data, align timelines, compare runs) | Not in the code. Its 17 tools drive a LIVE simulated PenguinPi through ROS 1: `move_forward`, `turn_left`, `get_ros_topics` (`subprocess rostopic list`), `get_topic_frequency` (`rostopic hz`)... Nothing reads a bag, MCAP or a log file. `ROSHD.py` (1,107 lines) is mostly a Gradio user-study UI (consent, demographics, post-survey). The agent is JPL ROSA (LangChain ReAct), installed with manual edits to site-packages. | It cannot investigate a field incident offline. Nothing to copy; the idea of explanations tailored to the user's expertise is worth keeping. |
| AURA code can be studied, licence unclear | True: public, no `LICENSE` file. | Cannot be copied into a commercial product. |
| AURA's Agent A characterises anomalies | Agent A (332 lines) sorts the 6 largest differences between the real AUV's state vector and its digital twin's, writes them into a prompt, adds ChromaDB retrieval and calls a local Ollama model. Agent B is a RAG chat. | Needs a digital twin; Veyra's deterministic characterisation (reference ranges, known-good runs, onset order, signal chain, discovery) is stronger and testable. Keep the idea of a verified-diagnosis feedback loop: that is what lessons do. |
| manastone-diag (Apache-2.0, MCAP parsing, rules, experience base) | Not found in public search. | Treat as unverified until someone has the URL. MCAP / bag parsing in Veyra uses `rosbags` and `mcap` (Apache-2.0 / MIT) already. |
| Use LangGraph (+ AgentScope) for orchestration | Both are capable. Veyra already has what they would provide: durable investigation state, human approval steps, resume, and an append-only, hash-chained record of every decision (the ledger), which a LangGraph checkpoint does not give. | Neither now. Revisit if Veyra needs multi-agent workflows that the ledger cannot express. |
| Build an agent on RosHD + AURA | Veyra's agent (`app/agent`, v3.3) already runs a tool loop with 24 read-only tools, sandboxed Python, data-egress authorisation per case, and refuses reports whose numbers are not in the cited tool output. | Extend it (done: four new tools) instead of replacing it. |
| ROS Help Desk paper result (92.85 % task success) | A user study on a simulated robot. | Not evidence about field incidents; agree with the plan's own caveat. |

The plan's strongest point (its first version) stands: one investigation agent over a deterministic evidence engine,
and a controlled comparison against a general coding agent on the same data with the answers sealed. That experiment
is the next step (section 6).

## 3. Architecture

```
 INPUT            any recording or log: ROS 1/2 bag, MCAP, PX4 ULog, CSV/JSON tables, TEXT LOGS (console, syslog, rosout)
   |              + what the engineer saw and already checked (free text)
   v
 INGEST           app/ingest: bags.py, ulog.py, textlog.py (new), pipeline.py  -> canonical rows, fingerprinted imports
   v
 EVIDENCE ENGINE  deterministic, testable, no model
   investigation run      app/engine/investigate.py   reference ranges, known-good runs, outputs, tracking, timing
   evidence discovery     app/discovery/              every stream of the ORIGINAL file by role; label guard; probes
   log characterisation   app/discovery/logs.py (new) restarts, stated reset cause, faults, lines before, absences
   signal chain           app/engine/mechanisms.py    measurement point, consequences, 6 mechanisms, order, chance
   questions              app/engine/questions.py     what the records answer before anyone is asked
   lessons                app/engine/lessons.py (new) earlier closed cases: retrieve, then check applicability
   v
 AGENTS           optional, on top of the engine, never instead of it
   Veyra agent            app/agent  (Anthropic API or the Claude CLI sign-in), cited reports, no confirmed causes
   any other agent        app/mcp_server.py (new): the same read-only tools over MCP (Claude Code / Desktop / Agent SDK,
                          LangGraph or any MCP client). Real cases need a recorded model authorisation.
   v
 DECISION LEDGER  app/ledger: checks, approvals, outcomes, closure (cause status, fix, held?, decisive check,
                  'applies when' (new)), append-only and hash-chained
   v
 OPERATIONAL LEARNING   closure -> lesson (verified / candidate / no cause) -> retrieved for the next case with its
                        conditions, missing signature parts and counterexamples
```

### Lessons: how reuse stays safe

1. Signature tokens per case: anomalies at named points (`anomaly:STUCK:rudder_rc4`), the mechanism the records
   supported (`mechanism:controller_behaviour`), the reset cause and fault location a log stated (`reset:watchdog`,
   `at:queue.c:1429`), the reported symptom.
2. Retrieve by weighted overlap (specific tokens weigh 3, general 1).
3. Applicability, deterministic: every specific token of the lesson present here and no context difference ->
   `applies`; some shared, some missing (or context differs / unverified) -> `check_first` (reuse its decisive check,
   not its fix); only general overlap -> `not_applicable`, shown so nobody re-uses that fix by mistake.
4. If the engineer reports the lesson's decisive check as already done ("stack overflow ruled out"), the lesson says so
   and marks this case as a likely counterexample.
5. A lesson never edits the case's own findings or hypotheses (tested).

## 4. The demo: from a log to an answer in seconds

Local preview -> Upload data -> drop a console log, or "Try a sample: a drone that restarted in flight".

The sample is the Crazyflie pair you asked about, reconstructed (synthetic, labelled) from public reports:
- an earlier, closed case modelled on crazyflie-firmware issue #531: watchdog restart with a failed FreeRTOS queue
  assert; cause a stack overflow of the high-level commander task; fix held;
- the new incident: restart in flight, watchdog only, no assert; the engineer already ruled out stack overflow.

What Veyra shows, with no model call: the restart and the cause the log states (watchdog), the lines before it, that no
stack-overflow or low-voltage message exists (with why that proves little), the earlier case as `check before reusing`
(shared: watchdog restart; not seen here: the assert at queue.c:1429), that its decisive check was already done here
(so this is likely a counterexample), and the next check: halt the board with a debugger while it hangs and see where
each task waits, which is the step that found the UART / DMA wait loop in the real report.

### Can the real Crazyflie case be used?

- As a demo of investigating with sparse evidence and of a lesson that must NOT be re-used blindly: yes, in
  reconstructed form, labelled synthetic. The forum thread has no timestamped telemetry and its trigger was never found,
  so it cannot score accuracy. Copying the thread's text into the product is not appropriate; the logs here are written
  in the firmware's message formats, not copied.
- As a benchmark: no. There is no ground truth and no data file. Issue #531 is better for that (cause found).

## 5. What runs where

| Piece | Needs | Works without a model / key |
|---|---|---|
| Upload, ingest, engine, discovery, logs, chain, questions, lessons | Python backend | Yes |
| Veyra agent report and follow-up questions | `ANTHROPIC_API_KEY` or the Claude CLI sign-in | No (optional layer) |
| MCP server | Python only (`python -m app.mcp_server`) | Yes (the client brings its own model) |

## 6. Next steps (in order)

1. The controlled comparison: the same ALFA flights and the Crazyflie pair, answers sealed; (a) a general coding agent
   with the raw files, (b) the same agent with Veyra's MCP tools, (c) Veyra's agent. Score: correct anomaly, no wrong
   cause claimed, next check that would separate the open causes, when it stops, cost. Without this, "Veyra is better
   than a coding agent" is a belief.
2. Real customer text logs: the classifier vocabulary (`textlog.KINDS`) is generic; measure it on 20 real logs before
   claiming coverage.
3. Lessons at scale: today's retrieval is exact token overlap. Add embedding retrieval only when there are enough
   closed cases for exact overlap to miss real matches; applicability stays deterministic.
4. PostgreSQL and object storage when a pilot customer needs multi-user workspaces.
