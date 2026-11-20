#!/bin/bash
# Starts the FastAPI backend and Next.js frontend together.
# Postgres is a persistent brew service and is not started here —
# check it with: brew services list | grep postgresql
set -e
cd "$(dirname "$0")"

trap 'kill 0' EXIT

.venv/bin/python -m uvicorn backend.main:app --reload --port 8010 &

(
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" && nvm use 20 > /dev/null 2>&1
  cd frontend && npm run dev
) &

wait
