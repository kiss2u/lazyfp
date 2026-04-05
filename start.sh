#!/bin/bash

PORT=8000

echo "Checking for existing process on port $PORT..."
fuser -k $PORT/tcp > /dev/null 2>&1
sleep 1

echo "Starting LazyFP WebUI..."
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port $PORT
