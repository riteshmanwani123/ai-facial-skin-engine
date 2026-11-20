#!/bin/bash
# Starts the FastAPI backend and Next.js frontend with HTTPS for camera testing across LAN / mobile devices.
set -e
cd "$(dirname "$0")"

trap 'kill 0' EXIT

export CLARITY_CORS_ORIGINS="http://localhost:3001,https://localhost:3001,https://192.168.1.112:3001,http://192.168.1.112:3001"
.venv/bin/python -m uvicorn backend.main:app --reload --port 8010 &

(
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" && nvm use 20 > /dev/null 2>&1
  cd frontend && npm run dev:https
) &

wait
