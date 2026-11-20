#!/bin/sh
# Local guided engineering replay (physical checks, hypotheses, next decisive check, scenarios, outcomes).
#   API  http://127.0.0.1:8058   (loopback only; /engineering/* answers only with VEYRA_ENGINEERING_LOCAL=true)
#   UI   http://127.0.0.1:3058/case-replay
# Private cases are read from VEYRA_PRIVATE_CASES_DIR (default ~/Veyra-private) and never leave this machine.
# The engineering ledger (scenarios, outcomes) defaults to ~/Veyra-private/engineering.db; uploaded recordings go to ~/Veyra-private/recordings.
# Existing previews (3019 / 3029 / 3049) and deployments are untouched.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
py=${VEYRA_PYTHON:-"$repo/backend/.venv/bin/python"}
if [ ! -x "$py" ]; then echo 'Set VEYRA_PYTHON to a Python with the backend requirements (fastapi, uvicorn, numpy).' >&2; exit 1; fi
for port in 8058 3058; do
  if lsof -ti tcp:"$port" -sTCP:LISTEN >/dev/null 2>&1; then echo "Port $port is in use; nothing was stopped." >&2; exit 1; fi
done
logdir="${VEYRA_PRIVATE_CASES_DIR:-$HOME/Veyra-private}"
mkdir -p "$logdir"
export VEYRA_ENGINEERING_LOCAL=true PYTHONPATH="$repo/backend"
export DATABASE_URL="sqlite:///$logdir/case-replay-legacy.db" VEYRA_AUTO_SEED=false VEYRA_PUBLIC_DEMO=false
# recording ingestion (upload from the Recordings view); uploads are stored under $logdir/recordings, never in the repository
export VEYRA_ENABLE_RUNTIME=true VEYRA_ALLOW_RECORDINGS=true VEYRA_ENGINE_DB="${VEYRA_ENGINE_DB:-$logdir/engine.db}"
# Each upload is investigated on arrival. The AI investigator runs through the local Claude Code CLI (your own sign-in)
# and only on SYNTHETIC data unless VEYRA_PRIVATE_ALLOW_AGENT=true (set it only after the data owner agreed).
# VEYRA_AUTO_INVESTIGATE=false keeps the automatic scan but skips the automatic investigation.
export VEYRA_AGENT_BACKEND="${VEYRA_AGENT_BACKEND:-claude-code}"
"$py" -m uvicorn app.main:app --host 127.0.0.1 --port 8058 > "$logdir/case-replay-api.log" 2>&1 &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM
cd frontend
export NEXT_PUBLIC_ENABLE_RUNTIME=true NEXT_PUBLIC_API_URL=/local-api VEYRA_LOCAL_API=http://127.0.0.1:8058 VEYRA_NEXT_DIST=.next-case-replay
echo 'Guided replay: http://127.0.0.1:3058/case-replay   (API log: '"$logdir"'/case-replay-api.log)'
./node_modules/.bin/next dev --webpack --hostname 127.0.0.1 -p 3058
