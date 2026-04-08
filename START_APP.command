#!/bin/zsh
set -e

echo "Stopping anything already running on ports 8000 and 5173..."

# lsof can return multiple PIDs; kill each safely
lsof -ti tcp:8000 2>/dev/null | xargs -r kill -9 2>/dev/null || true
lsof -ti tcp:5173 2>/dev/null | xargs -r kill -9 2>/dev/null || true

cd "$HOME/Desktop/lipsync_ui_mvp"

echo "Starting Backend..."
cd backend
source .venv/bin/activate
nohup python3 -m uvicorn app:app --reload --port 8000 > ../backend.log 2>&1 &
cd ..

echo "Starting UI..."
cd ui
nohup npm run dev -- --host 127.0.0.1 --port 5173 > ../ui.log 2>&1 &
cd ..

echo "Opening UI..."

# Wait until the UI server is actually responding before opening the browser
for i in {1..60}; do
  if curl -s http://127.0.0.1:5173/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

open "http://127.0.0.1:5173/"

echo ""
echo "All services started."
read