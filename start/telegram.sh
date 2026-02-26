#!/bin/bash
# Start Telegram Bot service (long polling, optional)
cd "$(dirname "$0")/.."
uv run python -m xyz_agent_context.services.telegram_bot
