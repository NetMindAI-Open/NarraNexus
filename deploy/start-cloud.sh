#!/usr/bin/env bash
set -euo pipefail

# Load environment
if [ -f .env.cloud ]; then
    export $(grep -v '^#' .env.cloud | xargs)
fi

echo "=== NarraNexus Cloud Server ==="
echo "Mode: $APP_MODE"
echo "Database: ${DATABASE_URL%%@*}@***"
echo "Port: ${PORT:-8000}"

# Initialize database tables (idempotent)
uv run python -c "
import asyncio
from xyz_agent_context.utils.database_table_management.create_all_tables import main
asyncio.run(main())
"

# Start server
exec uv run uvicorn backend.main:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8000}" \
    --workers "${WORKERS:-4}"
