#!/bin/bash
cd "$(dirname "$0")"

# Start ollama if not already running
if ! pgrep -x "ollama" > /dev/null 2>&1; then
    echo "[regwatch] Starting ollama in background..."
    ollama serve > /dev/null 2>&1 &
fi

source .venv/bin/activate

# Open browser after server has time to start
(sleep 5 && open http://localhost:8000) &

echo "[regwatch] Starting server at http://localhost:8000"
uvicorn src.main:app --host 0.0.0.0 --port 8000
