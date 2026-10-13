#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x backend/.venv-vc/bin/python ]; then
  echo "Ejecuta ./install_ai_engine.sh primero."
  exit 1
fi
backend/.venv-vc/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM
cd frontend
npm run dev -- --hostname 0.0.0.0 --port 3000
