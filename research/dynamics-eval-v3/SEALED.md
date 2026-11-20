# dynamics-eval-v3 sealed set

- Drawn 2026-11-18 (after tag `dynamics-eval-v3-frozen` = 31932e9) with os.urandom by `evaluate.py seal`.
- Key: `~/Veyra-private/dynamics-eval-v3/KEY-sealed-do-not-open.json` (outside the repository, mode 600).
- SHA-256: `7306ea48dba9847a84de0901d5cbc6a751b5ce29bddb140965b3243a27e40571`
- Contents: 108 engine cases (6 seeds x 9 classes x 2 domains), 10 agent cases (5 event classes x 2 domains), seeds in [0, 1e9).
- Not the v2 key, not dev seeds 2001-2004 / 5001-5004 / 6001-6004 (a collision with the random draw is checked by score).
- Opened only by the harness commands that generate cases and by `score`. Not run when this file was committed.
