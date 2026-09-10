# Veyra V0 Demo

Operational context for Physical AI.

Veyra reconstructs what machines did, what changed, what people knew, and what happened next, giving teams better context for every decision.

This repo is a production-shaped live demo scenario. Ingestion, reconstruction, comparison, Decision Package generation, sealing and operational memory run through the backend.

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

The seed data creates a 120-robot AMR rollout.

- 14:02: all 120 robots have a healthy state snapshot.
- 14:04: application v2.4 is deployed to all 120 robots.
- 14:06: Network Profile C is applied to the rollout subset.
- 14:11: the first abnormal navigation signal appears.
- 14:18: 37 affected robots are detected.
- 14:26: the customer ticket arrives.
- 37 robots show the signal; 83 remain healthy.
- 21 healthy robots share the same Network Profile C exposure and should be watched.
- A delayed edge-buffer event can later arrive with an event_time before the ticket but a later known_at timestamp.
- Runtime producers only need to provide `event_time`; Veyra assigns `known_at` and `ingested_at` when evidence enters the reconstruction layer.

Veyra reconstructs last-known healthy state, what changed before the incident, where else the same pattern appears, affected-vs-healthy comparison, Decision Package, sealed package and operational memory.

The product loop is:

> Evidence → What changed → Where else → Compare → Decision → Outcome → Memory

The intended live-demo line is:

> Software changed everywhere. The failure did not.

The ending:

> The first incident took 42 minutes to understand. The second took 42 seconds.

## Boundaries

Veyra runs as a read-only context layer alongside telemetry, logs, tickets and fleet management systems.

Machine action stays in customer systems. Human decisions stay with the team. AI is downstream of structured evidence.
