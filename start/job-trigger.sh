#!/bin/bash
# Start Job trigger (polls every 60 seconds)
# Auto-restart on crash: 3 attempts, exponential backoff (1s, 2s, 4s)
cd "$(dirname "$0")/.."

CHILD_PID=0
SHUTTING_DOWN=false

# Signal handler: kill child process and exit cleanly (skip retry loop)
_shutdown() { SHUTTING_DOWN=true; [ $CHILD_PID -ne 0 ] && kill $CHILD_PID 2>/dev/null; exit 0; }
trap _shutdown SIGTERM SIGINT SIGHUP

MAX_RETRIES=3
attempt=0

while [ $attempt -lt $MAX_RETRIES ]; do
    uv run python src/xyz_agent_context/module/job_module/job_trigger.py &
    CHILD_PID=$!
    wait $CHILD_PID
    exit_code=$?
    CHILD_PID=0

    $SHUTTING_DOWN && exit 0
    [ $exit_code -eq 0 ] && break

    attempt=$((attempt + 1))
    if [ $attempt -ge $MAX_RETRIES ]; then
        echo "[job-trigger] Crashed $MAX_RETRIES times, giving up."
        break
    fi
    backoff=$((1 << (attempt - 1)))
    echo "[job-trigger] Crashed (exit $exit_code), restarting in ${backoff}s... (attempt $((attempt + 1))/$MAX_RETRIES)"
    sleep $backoff
done
