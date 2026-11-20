# anticipate-eval-v1: sealed set

- Frozen code: tag `anticipate-eval-v1-frozen` (commit e56bfbe), branch `anticipate-surface` (worktree /home/claude/work/anticipate).
- 144 cases drawn with os.urandom after the freeze. SHA-256 `8cea8e1af878882b5780bfbcb12d50732b95b17eb019a2c22de926373833baf0`.
- Key: container `/tmp/claude-0/anteval/KEY.json`; Mac `~/Veyra-private/anticipate-eval-v1/KEY-sealed-do-not-open.json`.
- Run: `python3 research/anticipate-eval-v1/evaluate.py run --key /tmp/claude-0/anteval/KEY.json --out /tmp/claude-0/anteval/runs` (rerun-safe),
  then `score` with the same arguments.
