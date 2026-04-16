"""
@file_name: system_provider_service.py
@author: Bin Liang
@date: 2026-04-16
@description: Load the system-default LLMConfig from environment variables.

Activates ONLY in cloud mode AND when all required env vars are present.
In local mode or when disabled, is_enabled() returns False and every
caller should short-circuit — this preserves the local `bash run.sh`
experience unchanged.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


_SYSTEM_ANTHROPIC_PROVIDER_ID = "system_default_anthropic"
_SYSTEM_OPENAI_PROVIDER_ID = "system_default_openai"


def _is_cloud_mode() -> bool:
    """Mirror backend/auth.py's cloud-mode detection logic."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return not db_url.startswith("sqlite")
    return bool(os.environ.get("DB_HOST", ""))


class SystemProviderService:
    """Module-level singleton. Env is read once at first `instance()` call."""

    _instance: Optional["SystemProviderService"] = None

    def __init__(self, enabled: bool, config: Optional[LLMConfig]):
        self._enabled = enabled
        self._config = config

    @classmethod
    def instance(cls) -> "SystemProviderService":
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> "SystemProviderService":
        if not _is_cloud_mode():
            return cls(enabled=False, config=None)
        if os.environ.get("SYSTEM_DEFAULT_LLM_ENABLED", "").lower() != "true":
            return cls(enabled=False, config=None)

        api_key = os.environ.get("SYSTEM_DEFAULT_LLM_API_KEY", "").strip()
        if not api_key:
            return cls(enabled=False, config=None)

        agent_model = os.environ.get("SYSTEM_DEFAULT_LLM_AGENT_MODEL", "").strip()
        embedding_model = os.environ.get("SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL", "").strip()
        helper_model = os.environ.get("SYSTEM_DEFAULT_LLM_HELPER_MODEL", "").strip()
        if not (agent_model and embedding_model and helper_model):
            return cls(enabled=False, config=None)

        source_str = os.environ.get("SYSTEM_DEFAULT_LLM_SOURCE", "netmind").strip()
        try:
            source = ProviderSource(source_str)
        except ValueError:
            return cls(enabled=False, config=None)

        anthropic_base = os.environ.get(
            "SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL", ""
        ).strip()
        openai_base = os.environ.get(
            "SYSTEM_DEFAULT_LLM_OPENAI_BASE_URL", ""
        ).strip()

        anthropic_provider = ProviderConfig(
            provider_id=_SYSTEM_ANTHROPIC_PROVIDER_ID,
            name="System Default (Anthropic)",
            source=source,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            api_key=api_key,
            base_url=anthropic_base,
            models=[agent_model],
            linked_group="system_default",
            is_active=True,
            supports_anthropic_server_tools=False,
        )
        openai_provider = ProviderConfig(
            provider_id=_SYSTEM_OPENAI_PROVIDER_ID,
            name="System Default (OpenAI)",
            source=source,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=api_key,
            base_url=openai_base,
            models=[embedding_model, helper_model],
            linked_group="system_default",
            is_active=True,
        )

        cfg = LLMConfig(
            providers={
                _SYSTEM_ANTHROPIC_PROVIDER_ID: anthropic_provider,
                _SYSTEM_OPENAI_PROVIDER_ID: openai_provider,
            },
            slots={
                "agent": SlotConfig(
                    provider_id=_SYSTEM_ANTHROPIC_PROVIDER_ID,
                    model=agent_model,
                ),
                "embedding": SlotConfig(
                    provider_id=_SYSTEM_OPENAI_PROVIDER_ID,
                    model=embedding_model,
                ),
                "helper_llm": SlotConfig(
                    provider_id=_SYSTEM_OPENAI_PROVIDER_ID,
                    model=helper_model,
                ),
            },
        )
        return cls(enabled=True, config=cfg)

    def is_enabled(self) -> bool:
        return self._enabled

    def get_config(self) -> LLMConfig:
        if not self._enabled or self._config is None:
            raise RuntimeError(
                "SystemProviderService is disabled; check is_enabled() first"
            )
        return self._config

    def get_initial_quota(self) -> Tuple[int, int]:
        """Read SYSTEM_DEFAULT_QUOTA_* from env. Safe to call even when disabled."""
        inp = int(os.environ.get("SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS", "0"))
        out = int(os.environ.get("SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS", "0"))
        return inp, out
