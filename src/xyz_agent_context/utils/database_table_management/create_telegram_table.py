#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create telegram_bot_bindings and telegram_chat_sessions tables

@file_name: create_telegram_table.py
@author: NetMind.AI
@date: 2026-03-04
@description: 创建 TelegramModule 所需的两张表：
              - telegram_bot_bindings：Bot token 绑定记录（agent_id → bot_token）
              - telegram_chat_sessions：活跃 chat session 桥接表（用于 hook_data_gathering 注入 chat_id）

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

try:
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )


# ===== telegram_bot_bindings 数据模型 =====

class TelegramBotBinding(BaseModel):
    """
    Telegram Bot 绑定记录
    每个 agent 最多绑定一个 Bot（UNIQUE agent_id）
    """
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Agent 唯一标识")
    bot_token: str = Field(..., max_length=128, description="Telegram Bot Token")
    bot_username: Optional[str] = Field(None, max_length=64, description="Bot 用户名（getMe() 获取）")
    status: str = Field(default="ACTIVE", description="状态：ACTIVE 或 DISABLED")
    created_at: Optional[datetime] = Field(default=None, description="创建时间")
    updated_at: Optional[datetime] = Field(default=None, description="更新时间")


# ===== telegram_chat_sessions 数据模型 =====

class TelegramChatSession(BaseModel):
    """
    Telegram Chat Session 桥接记录
    TelegramTrigger 收到消息时 upsert，hook_data_gathering 从此表查 chat_id
    私聊场景下 chat_id == telegram_user_id
    """
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Agent 唯一标识")
    telegram_user_id: str = Field(..., max_length=64, description="Telegram 用户 ID（裸数字字符串）")
    chat_id: int = Field(..., description="Telegram chat_id（私聊时等于 telegram_user_id）")
    bot_username: Optional[str] = Field(None, max_length=64, description="Bot 用户名")
    last_seen_at: Optional[datetime] = Field(default=None, description="最后活跃时间")


# ===== telegram_bot_bindings 表管理器 =====

class TelegramBotBindingTableManager(BaseTableManager):
    """telegram_bot_bindings 表管理器"""
    model = TelegramBotBinding
    table_name = "telegram_bot_bindings"
    field_name_mapping = {"id": "id"}
    ignored_fields = {"created_at", "updated_at"}
    protected_columns = {"id", "created_at", "updated_at"}
    new_column_defaults: Dict[str, str] = {}
    unique_id_field = "agent_id"
    json_fields: set = set()


# ===== telegram_chat_sessions 表管理器 =====

class TelegramChatSessionTableManager(BaseTableManager):
    """telegram_chat_sessions 表管理器"""
    model = TelegramChatSession
    table_name = "telegram_chat_sessions"
    field_name_mapping = {"id": "id"}
    ignored_fields = {"last_seen_at"}
    protected_columns = {"id", "last_seen_at"}
    new_column_defaults: Dict[str, str] = {}
    unique_id_field = "id"
    json_fields: set = set()


# ===== 索引定义 =====

BINDINGS_INDEXES = [
    ("idx_telegram_status", ["status"], False),
]

SESSIONS_INDEXES = [
    ("uk_agent_user", ["agent_id", "telegram_user_id"], True),
]


# ===== CLI =====

async def main():
    parser = argparse.ArgumentParser(description="Create Telegram tables")
    parser.add_argument("--force", "-f", action="store_true", help="强制删除并重建表")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("Telegram Tables Creation Tool")
    print("=" * 60)

    print("\n[1/2] Creating telegram_bot_bindings table...")
    if args.interactive:
        await create_table_interactive(TelegramBotBindingTableManager, BINDINGS_INDEXES)
    else:
        await create_table(TelegramBotBindingTableManager, BINDINGS_INDEXES, force=args.force)

    print("\n[2/2] Creating telegram_chat_sessions table...")
    if args.interactive:
        await create_table_interactive(TelegramChatSessionTableManager, SESSIONS_INDEXES)
    else:
        await create_table(TelegramChatSessionTableManager, SESSIONS_INDEXES, force=args.force)

    print("\n✅ All Telegram tables created successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
