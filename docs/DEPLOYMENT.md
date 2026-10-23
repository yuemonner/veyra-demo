# Deploying Veyra

This setup is for a small deployment. It avoids enterprise integrations at the start and lets Veyra work with exported files, read-only evidence and a persistent Field Case database.

## What This Gives You

- local development with SQLite and local upload storage
- optional Neon Postgres for persistent hosted state
- optional Cloudflare R2 or S3-compatible storage for uploaded evidence files
- a self-serve upload path at `/try`
- the existing Product Demo at `/product-demo`

## 1. Run Locally First

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8050
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://127.0.0.1:8050 npm run dev
```

Open:

- `http://127.0.0.1:3000/product-demo`
- `http://127.0.0.1:3000/try`

## 2. Use Neon For Hosted State

Create a free Neon project, then copy the pooled connection string.

Use the SQLAlchemy psycopg form:

```bash
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
```

The API creates prototype tables on startup. For production, add migrations before changing schema.

## 3. Use Cloudflare R2 For Uploaded Evidence

Create an R2 bucket and an R2 API token with object read/write permissions for that bucket.

Set:

```bash
FILE_STORAGE_BACKEND=r2
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=veyra-uploads
AWS_ACCESS_KEY_ID=<r2-access-key-id>
AWS_SECRET_ACCESS_KEY=<r2-secret-access-key>
AWS_REGION=auto
S3_PREFIX=field-case-uploads
```

Veyra stores raw uploads in R2 and keeps the Field Case, provenance and parsed events in the database.

## 4. Deploy Frontend On Vercel

Use:

```text
Root directory: frontend
Framework: Next.js
Build command: npm run build
```

Set this only when the frontend should talk to a deployed backend:

```bash
NEXT_PUBLIC_API_URL=https://<your-api-host>
```

If this is not set, the Product Demo uses local fallback data.

## 5. Backend Hosting Notes

For a live meeting, the most reliable free option is often running the backend locally on your laptop and using the Vercel frontend for the visual product demo.

For remote self-serve tests, deploy the backend on a hosted service and set `DATABASE_URL` and R2 env vars there.

Render free instances can sleep and cold-start slowly. That is acceptable for testing, but not ideal for a live demo.

## 6. Data To Start With

Start with a small export from one historical incident:

```text
timestamp
asset_id
event_type
release or software_version
configuration or localization_profile
zone or environment
status
operator_note
action
outcome
```

Supported upload formats today:

- `.csv`
- `.json`
- `.jsonl`
- `.ndjson`

Not yet supported:

- MCAP
- ROS bag
- video replay
- S3/GCS sync jobs
- edge folder watcher
- production auth and tenancy

## 7. Scope

Veyra is meant for:

```text
Try Veyra on one exported field case.
Use read-only evidence.
See whether the investigation gets faster and clearer.
```

It is not a full data platform, robot control system or production incident manager.
