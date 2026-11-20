#!/bin/bash
# Production-mode start: FastAPI (no reload) + the built Next.js app. Postgres is a brew service (see README_ARCHITECTURE.md).
# Usage: ./prod.sh            (builds the frontend first)
set -e
cd "$(dirname "$0")"
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" && nvm use 20 > /dev/null 2>&1
trap 'kill 0' EXIT
.venv/bin/python -m uvicorn backend.main:app --port 8010 &
(cd frontend && npm run build && npm run start -- -p 3001) &
wait
