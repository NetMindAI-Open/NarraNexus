"""
@file_name: settings.py
@author: NetMind.AI
@date: 2026-02-09
@description: Unified configuration management

Uses pydantic-settings to centrally manage all environment variables, replacing
scattered load_dotenv() + os.getenv() calls throughout the codebase.

Priority: .env file > system environment variables.
When users configure API keys through the desktop app or run.sh, those values
are written to .env and MUST take precedence over pre-existing shell env vars.

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


def _read_dotenv_raw(env_file: Path) -> dict[str, str]:
    """Read .env file and return raw key-value pairs (no variable expansion).

    This is used to determine which values the user explicitly configured,
    so we can give .env priority over pre-existing shell environment variables.
    """
    result: dict[str, str] = {}
    if not env_file.is_file():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip optional surrounding quotes
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


# Pre-load .env values and inject them into os.environ BEFORE pydantic-settings
# reads them. pydantic-settings' default priority is env_var > .env file, but
# we want the opposite for API keys: the user explicitly configured these in .env
# (via desktop app or run.sh), so they should override any pre-existing shell vars.
_dotenv_values = _read_dotenv_raw(_PROJECT_ROOT / ".env")
_API_KEY_FIELDS = {"OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
for _k, _v in _dotenv_values.items():
    if _k in _API_KEY_FIELDS and _v:
        os.environ[_k] = _v


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
    anthropic_model: str = ""  # Empty = let Claude Code CLI use its default model

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
    skip_module_decision_llm: bool = True

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
    if _val:
        # Unconditionally write: settings already reflects .env > shell priority
        # (pre-injection above ensures .env API keys override shell env vars).
        os.environ[_key] = _val
    elif _key in os.environ and not os.environ[_key]:
        # Clean up empty values in os.environ (may come from .env blank lines
        # or desktop getExecEnv). An empty ANTHROPIC_API_KEY would make
        # Claude CLI think an API key is configured and skip OAuth fallback.
        del os.environ[_key]
