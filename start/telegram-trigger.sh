#!/bin/bash
# 启动 TelegramTrigger（多 Bot 后台管理服务）
cd "$(dirname "$0")/.."
uv run python -m xyz_agent_context.module.telegram_module.telegram_trigger
