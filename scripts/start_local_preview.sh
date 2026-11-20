#!/bin/sh
# Local-only launcher: no deployment, source publication or cloud mutation.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
preview_python=${VEYRA_PYTHON:-"$repo_dir/backend/.venv/bin/python"}
if [ ! -x "$preview_python" ]; then
  echo 'Set VEYRA_PYTHON to an environment with backend/requirements.txt installed.' >&2
  exit 1
fi
mkdir -p .local-runtime
export VEYRA_ENABLE_RUNTIME=true VEYRA_AUTO_SEED=true
export DATABASE_URL="sqlite:///$repo_dir/.local-runtime/veyra.db" PYTHONPATH="$repo_dir/backend"
"$preview_python" -m uvicorn app.main:app --host 127.0.0.1 --port 8019 &
preview_api_pid=$!
trap 'kill "$preview_api_pid" 2>/dev/null || true' EXIT INT TERM
cd frontend
export NEXT_PUBLIC_ENABLE_RUNTIME=true NEXT_PUBLIC_API_URL=/local-api
npm run build
npx next start --hostname 127.0.0.1 -p 3019
