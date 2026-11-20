# dynamics-eval-v1: sealed set

- Frozen code: tag `dynamics-eval-v1-frozen` (commit 4421daf), branch `signal-dynamics`.
- Old agent: branch `old-agent-matched` (da85f7e) = 73fd22e + the two ingest commits (matched data access, no dynamics).
- Key drawn with os.urandom after the freeze: 108 engine cases, 10 agent cases.
  SHA-256 `1771a31f0242837822f45e7f9225417fb9961dfa6b2edd08a7b39e34238b0d15`.
- Key location: container `/tmp/claude-0/dyneval/KEY.json`; Mac `~/Veyra-private/dynamics-eval-v1/KEY-sealed-do-not-open.json`.
  Read only by `evaluate.py score` after the runs.
