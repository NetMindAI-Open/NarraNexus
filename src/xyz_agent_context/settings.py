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
#
# Two whitelists drive the injection:
#   _API_KEY_FIELDS     — LLM provider keys, original use case
#   _DOTENV_PASSTHROUGH — other .env-only service secrets / tuning knobs that
#                         backend code reads via `os.environ.get()` directly
#                         (rather than through the Settings object). Add a
#                         var here whenever you introduce one, otherwise it
#                         silently has no effect on os.environ and
#                         `bash run.sh` / `make dev-backend` won't pick it up.
_dotenv_values = _read_dotenv_raw(_PROJECT_ROOT / ".env")
_API_KEY_FIELDS = {"OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
_DOTENV_PASSTHROUGH = {
    "BUNDLE_FETCH_ALLOWED_HOSTS",  # backend/routes/bundle.py — /import/from-url SSRF guard
}
for _k, _v in _dotenv_values.items():
    if not _v:
        continue
    if _k in _API_KEY_FIELDS or _k in _DOTENV_PASSTHROUGH:
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

    # ===== LLM runtime resilience (#7) =====
    # All env-tunable so ops can adjust without a code change. These address
    # "agent run hangs on an API problem with no retry, occupying a runtime
    # slot" WITHOUT violating 铁律 #14 (no agent_loop force-stop / total cap) or
    # #15 (no governing the user's model choice).
    #
    # Injected into the Claude Code CLI subprocess env by `to_cli_env()`:
    #   API_TIMEOUT_MS         — per-REQUEST timeout (NOT a run total). A stalled
    #                            request errors after this and the CLI auto-
    #                            retries. 10 min is generous enough that a
    #                            legitimately-streaming long thinking pass (which
    #                            keeps emitting tokens) is not cut.
    #   CLAUDE_CODE_MAX_RETRIES — CLI's built-in retry count for transient
    #                            errors (429 / 5xx / connection). Same provider,
    #                            count-bounded (not time-bounded).
    llm_api_timeout_ms: int = 600000          # 10 min per request
    llm_max_retries: int = 10                 # CLI default; explicit = deterministic
    # Health-probe diagnostic: when a run produces NO events at all for this
    # long (true silence, subprocess still alive), probe the provider base_url
    # out-of-band and log whether it's reachable — distinguishing "model is
    # thinking" (provider up) from "connection is dead" (provider down). This is
    # diagnostic only; it never force-stops the run (铁律 #14).
    llm_stall_probe_after_seconds: int = 600
    llm_stall_probe_timeout_seconds: int = 10

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

    # ===== Transcription (audio → text) =====
    # Externally-reachable base URL for this NarraNexus deployment. Used by
    # the NetMind transcription backend to mint signed audio URLs that
    # NetMind's worker can fetch. Empty disables system-default NetMind
    # transcription (the resolver downgrades to "unavailable" instead of
    # minting URLs that NetMind can't reach).
    public_base_url: str = ""

    # HMAC-SHA256 secret used to sign transcription audio URLs. In cloud
    # mode this MUST be set explicitly — we refuse to derive a secret in
    # production. In local mode an unset value falls back to admin_secret_key.
    transcription_hmac_secret: str = ""

    # System-default NetMind credentials for the cloud free tier. When
    # present and SystemProviderService is enabled, the transcription
    # resolver appends NetMind as the last fallback (after user providers
    # and settings.openai_api_key) without consulting the LLM token quota.
    system_default_netmind_api_key: str = ""
    system_default_netmind_base_url: str = "https://api.netmind.ai"

    @property
    def is_cloud_mode(self) -> bool:
        """True when DATABASE_URL points at a non-sqlite backend (mysql in prod).

        Mirrors backend.auth._is_cloud_mode but without the cross-package
        import — settings is a leaf module and mustn't depend on backend.
        """
        url = (self.database_url or os.environ.get("DATABASE_URL") or "").strip()
        return bool(url) and not url.startswith("sqlite")

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
