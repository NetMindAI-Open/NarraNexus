#!/bin/bash
# Start TelegramTrigger (multi-bot manager, replaces telegram_bot.py)
# 单例保证：先 kill 已有进程，再启动新的
cd "$(dirname "$0")/.."

# 停止已有的 TelegramTrigger 进程
pkill -f "xyz_agent_context.module.telegram_module.telegram_trigger" 2>/dev/null
sleep 1

uv run python -m xyz_agent_context.module.telegram_module.telegram_trigger
