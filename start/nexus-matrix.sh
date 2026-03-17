#!/bin/bash
# Start NexusMatrix Server (port 8953)
# Auto-restart on crash: 3 attempts, exponential backoff (1s, 2s, 4s)
NEXUSMATRIX_DIR="$(dirname "$0")/../related_project/NetMind-AI-RS-NexusMatrix"

if [ ! -d "$NEXUSMATRIX_DIR" ]; then
    echo "[ERROR] NexusMatrix not found at $NEXUSMATRIX_DIR"
    echo "        Run 'bash run.sh install' to clone and set up NexusMatrix automatically."
    exit 1
fi

cd "$NEXUSMATRIX_DIR"
unset CLAUDECODE

CHILD_PID=0
SHUTTING_DOWN=false

# Signal handler: kill child process and exit cleanly (skip retry loop)
_shutdown() { SHUTTING_DOWN=true; [ $CHILD_PID -ne 0 ] && kill $CHILD_PID 2>/dev/null; exit 0; }
trap _shutdown SIGTERM SIGINT SIGHUP

MAX_RETRIES=3
attempt=0

while [ $attempt -lt $MAX_RETRIES ]; do
    uv run python -m nexus_matrix.main &
    CHILD_PID=$!
    wait $CHILD_PID
    exit_code=$?
    CHILD_PID=0

    $SHUTTING_DOWN && exit 0
    [ $exit_code -eq 0 ] && break

    attempt=$((attempt + 1))
    if [ $attempt -ge $MAX_RETRIES ]; then
        echo "[nexus-matrix] Crashed $MAX_RETRIES times, giving up."
        break
    fi
    backoff=$((1 << (attempt - 1)))
    echo "[nexus-matrix] Crashed (exit $exit_code), restarting in ${backoff}s... (attempt $((attempt + 1))/$MAX_RETRIES)"
    sleep $backoff
done
