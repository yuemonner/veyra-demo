#!/bin/bash
# Local preview of a PRIVATE Veyra instance: sign-in, one database per workspace, recording upload, share links.
# API on 127.0.0.1:8029, web on 127.0.0.1:3029 (the usual 8019/3019 preview is left alone). Data in .local-private/.
# Usage: scripts/private_preview.sh <python with the backend requirements + rosbags/mcap>
set -u
cd "$(dirname "$0")/.." || exit 1
py="${1:-python3}"
export VEYRA_DATA_DIR="$PWD/.local-private"
mkdir -p "$VEYRA_DATA_DIR"
for port in 8029 3029; do pid=$(lsof -ti tcp:$port -sTCP:LISTEN); [ -n "$pid" ] && kill $pid; done
sleep 1
PYTHONPATH="$PWD/backend" "$py" scripts/private_test_accounts.py > "$VEYRA_DATA_DIR/setup.log" 2>&1 || { cat "$VEYRA_DATA_DIR/setup.log"; exit 1; }
PYTHONPATH="$PWD/backend" "$py" scripts/private_seed_eval.py >> "$VEYRA_DATA_DIR/setup.log" 2>&1 || { cat "$VEYRA_DATA_DIR/setup.log"; exit 1; }
PYTHONPATH="$PWD/backend" "$py" scripts/private_recreate_qd1.py >> "$VEYRA_DATA_DIR/setup.log" 2>&1 || true
PYTHONPATH="$PWD/backend" "$py" scripts/private_load_real.py >> "$VEYRA_DATA_DIR/setup.log" 2>&1 || true
VEYRA_LOCAL_SIGNIN_AS="${VEYRA_LOCAL_SIGNIN_AS-engineer@veyra.test}" VEYRA_PRIVATE_ALLOW_AGENT="${VEYRA_PRIVATE_ALLOW_AGENT-true}" VEYRA_AGENT_BACKEND="${VEYRA_AGENT_BACKEND-claude-code}" VEYRA_AUTOPILOT="${VEYRA_AUTOPILOT-true}" VEYRA_AUTOPILOT_AGENT="${VEYRA_AUTOPILOT_AGENT-true}" VEYRA_RUNTIME_ORIGINS="http://127.0.0.1:3029,http://localhost:3029" VEYRA_PRIVATE=true VEYRA_ENABLE_RUNTIME=true DATABASE_URL="sqlite:///$VEYRA_DATA_DIR/legacy.db" PYTHONPATH="$PWD/backend" \
  nohup "$py" -m uvicorn app.main:app --host 127.0.0.1 --port 8029 > "$VEYRA_DATA_DIR/api.log" 2>&1 &
cd frontend
NEXT_PUBLIC_ENABLE_RUNTIME=true NEXT_PUBLIC_API_URL=/local-api VEYRA_LOCAL_API=http://127.0.0.1:8029 VEYRA_NEXT_DIST=.next-private WATCHPACK_POLLING=true \
  nohup npx next dev --hostname 127.0.0.1 -p 3029 > "$VEYRA_DATA_DIR/web.log" 2>&1 &
echo "Private preview: http://127.0.0.1:3029/field-case  (test accounts in .local-private/test-accounts.txt)"
