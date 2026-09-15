# Veyra V0 Demo

Decision infrastructure for Physical AI.

Veyra reconstructs machine evidence into sealed decision records: what happened, what was knowable, who approved action, what changed afterward and what the next decision should inherit.

This repo is a production-shaped live demo scenario. Ingestion, reconstruction, comparison, deterministic Decision Package generation, human decision recording, sealing, standalone verification and outcome-linked precedent run through the backend.

## Run

```bash
cd veyra-v0-demo
python -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
make demo
make backend
```

In another terminal:

```bash
cd veyra-v0-demo/frontend
npm install
NEXT_PUBLIC_API_URL=http://127.0.0.1:8050 npm run dev
```

Open:

- Frontend: http://127.0.0.1:3005
- Cinematic demo: http://127.0.0.1:3005/cinematic
- API docs: http://127.0.0.1:8050/docs
- Demo control: http://127.0.0.1:3005/demo-control

## Demo Scenario

The seed data creates a six-robot manipulation-policy test.

- 14:02: all six robots have a healthy state snapshot.
- 14:04: policy v0.9 is rolled out to all six robots.
- 14:06: calibration C and gripper firmware 7.3 are applied to a subset.
- 14:11: the first grip pose drift signal appears.
- 14:18: two affected robots are detected.
- 14:26: an engineer note records human discovery.
- Two robots show the signal; four remain stable.
- One stable robot shares the same calibration/firmware exposure and should be watched.
- A delayed edge-buffer event can later arrive with an event_time before the engineer note but a later known_at timestamp.
- Runtime producers only need to provide `event_time`; Veyra assigns `known_at` and `ingested_at` when evidence enters the reconstruction layer.

Veyra reconstructs last-known healthy state, what changed before the incident, where else the same pattern appears, affected-vs-healthy comparison, Decision Package, sealed decision record and outcome-linked precedent.

The product loop is:

> Evidence → Belief → Decision → Action → Outcome

The intended live-demo line is:

> Same model. Same task. Different behavior.

The ending:

> The first run took 42 minutes to understand. The next run took 42 seconds.

## Boundaries

Veyra runs as a read-only context layer alongside telemetry, logs, tickets and fleet management systems.

Machine action stays in customer systems. Human decisions stay with the team. Veyra preserves the decision record.
