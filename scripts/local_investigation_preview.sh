#!/bin/sh
# Separate local preview. Existing 3019/3029 previews and deployments are untouched.
set -eu
preview_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$preview_repo"
preview_python=${VEYRA_PYTHON:-"$preview_repo/backend/.venv/bin/python"}
if [ ! -x "$preview_python" ]; then echo 'Set VEYRA_PYTHON to the backend Python environment.' >&2; exit 1; fi
for preview_port in 8049 3049; do
  if lsof -ti tcp:"$preview_port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $preview_port is occupied. Existing process was not stopped." >&2; exit 1
  fi
done
export VEYRA_DATA_DIR="$preview_repo/.local-private"
mkdir -p "$VEYRA_DATA_DIR"
export VEYRA_ENABLE_RUNTIME=true VEYRA_PRIVATE=true VEYRA_PRIVATE_ALLOW_AGENT=true
export VEYRA_LOCAL_SIGNIN_AS=engineer@veyra.test VEYRA_AGENT_BACKEND=openai
export VEYRA_INVESTIGATION_LAB=true
# User explicitly requested real Agent Blocked. No model is called by this preview's investigation lab.
export VEYRA_INVESTIGATION_AGENT_BLOCKED="${VEYRA_INVESTIGATION_AGENT_BLOCKED:-true}"
export VEYRA_AUTOPILOT_AGENT=false VEYRA_AUTO_SEED=false
export DATABASE_URL="sqlite:///$VEYRA_DATA_DIR/investigation-legacy.db" PYTHONPATH="$preview_repo/backend"
export VEYRA_RUNTIME_ORIGINS=http://127.0.0.1:3049,http://localhost:3049
"$preview_python" -m uvicorn app.main:app --host 127.0.0.1 --port 8049 > "$VEYRA_DATA_DIR/investigation-api.log" 2>&1 &
preview_api_pid=$!
trap 'kill "$preview_api_pid" 2>/dev/null || true' EXIT INT TERM
cd frontend
export NEXT_PUBLIC_ENABLE_RUNTIME=true NEXT_PUBLIC_INVESTIGATION_LAB=true NEXT_PUBLIC_API_URL=/local-api
export VEYRA_LOCAL_API=http://127.0.0.1:8049 VEYRA_NEXT_DIST=.next-investigation
export WATCHPACK_POLLING=true
echo 'Local investigation preview: http://127.0.0.1:3049/field-case?view=investigation'
./node_modules/.bin/next dev --webpack --hostname 127.0.0.1 -p 3049
