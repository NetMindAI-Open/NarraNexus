#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

export DATABASE_URL="sqlite:///$HOME/Library/Application Support/NarraNexus/nexus.db"

echo "=== NarraNexus Local Dev ==="
echo "Database: $DATABASE_URL"
echo ""

# Cleanup on exit
cleanup() {
    echo ""
    echo "Stopping all services..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "All services stopped."
}
trap cleanup EXIT

# Start all services
echo "Starting Backend API (port 8000)..."
uv run uvicorn backend.main:app --port 8000 &

echo "Starting MCP Server..."
uv run python src/xyz_agent_context/module/module_runner.py mcp &

echo "Starting Module Poller..."
uv run python -m xyz_agent_context.services.module_poller &

echo "Starting Job Trigger..."
uv run python src/xyz_agent_context/module/job_module/job_trigger.py &

echo ""
echo "All services started. Press Ctrl+C to stop all."
echo ""

# Wait for any process to exit
wait
