"""
@file_name: settings.py
@author: NetMind.AI
@date: 2026-02-09
@description: Unified configuration management

Uses pydantic-settings to centrally manage all environment variables, replacing
scattered load_dotenv() + os.getenv() calls throughout the codebase.

Usage:
    from xyz_agent_context.settings import settings

    api_key = settings.google_api_key
    db_host = settings.db_host
"""

import os
from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (3 levels up from src/xyz_agent_context/settings.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application global configuration, automatically loaded from .env file and environment variables"""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== LLM API Keys =====
    openai_api_key: str = ""
    google_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""

    # ===== Database =====
    database_url: Optional[str] = None
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""

    # SSL (optional)
    db_ssl_ca: Optional[str] = None
    db_ssl_cert: Optional[str] = None
    db_ssl_key: Optional[str] = None
    db_ssl_verify_cert: Optional[str] = None

    # ===== Workspace =====
    # Absolute path under user home; immune to cwd differences between
    # dev server, Electron bundle, and CLI scripts.
    base_working_path: str = str(Path.home() / ".nexusagent" / "workspaces")

    # ===== Embedding =====
    openai_embedding_model: str = "text-embedding-3-small"

    # ===== Export Paths =====
    narrative_markdown_path: str = str(Path.home() / ".nexusagent" / "data" / "narratives")
    trajectory_path: str = str(Path.home() / ".nexusagent" / "data" / "trajectories")

    # ===== Auth =====
    admin_secret_key: str = ""

    # ===== Speed Optimization =====
    # When True, skip the LLM instance decision call in Step 2 and always load
    # all capability modules directly.  This saves ~2.5-3s per turn since the
    # LLM call currently always returns the same 4 modules.
    skip_module_decision_llm: bool = False

    # ===== Telegram Bot =====
    # TelegramTrigger DB 轮询间隔（秒）
    telegram_trigger_poll_interval: int = 30

    @model_validator(mode="after")
    def _expand_user_paths(self) -> "Settings":
        """Expand ~ in path settings so callers don't need to handle it."""
        for field in ("base_working_path", "narrative_markdown_path", "trajectory_path"):
            raw = getattr(self, field)
            expanded = str(Path(raw).expanduser())
            if expanded != raw:
                object.__setattr__(self, field, expanded)
        return self


settings = Settings()

# Sync key variables to os.environ for direct use by third-party SDKs (e.g., OpenAI Agents SDK).
# pydantic-settings only loads values into the Settings object and does not automatically write to os.environ.
_ENV_SYNC = {
    "OPENAI_API_KEY": settings.openai_api_key,
    "GOOGLE_API_KEY": settings.google_api_key,
    "ANTHROPIC_API_KEY": settings.anthropic_api_key,
    "ANTHROPIC_BASE_URL": settings.anthropic_base_url,
}
for _key, _val in _ENV_SYNC.items():
    if _val and not os.environ.get(_key):
        os.environ[_key] = _val
    elif not _val and _key in os.environ and not os.environ[_key]:
        # 清理 os.environ 中的空值（可能由 .env 空行或 desktop getExecEnv 传入）。
        # Claude CLI 子进程继承 os.environ，空字符串的 ANTHROPIC_API_KEY 会被 CLI
        # 视为"已配置 API Key"而不回退到 OAuth，导致认证失败。
        del os.environ[_key]
