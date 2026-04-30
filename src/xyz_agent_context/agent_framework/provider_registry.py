"""
@file_name: provider_registry.py
@author: Bin Liang
@date: 2026-03-23
@description: LLM Provider configuration management

Manages the llm_config.json file that stores provider definitions and
slot assignments. Provides atomic provider addition, validation, and
connection testing.

Usage:
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    # Add a provider (atomic operation)
    config, ids = provider_registry.add_provider("netmind", api_key="xxx")

    # Validate all slots are configured
    errors = provider_registry.validate(config)
"""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)
from xyz_agent_context.agent_framework.model_catalog import get_default_models


# =============================================================================
# Constants
# =============================================================================

CONFIG_DIR = Path.home() / ".nexusagent"
CONFIG_FILE = CONFIG_DIR / "llm_config.json"

# NetMind endpoint URLs
NETMIND_ANTHROPIC_BASE_URL = "https://api.netmind.ai/inference-api/anthropic"
NETMIND_OPENAI_BASE_URL = "https://api.netmind.ai/inference-api/openai/v1"

# Yunwu endpoint URLs (proxies official Claude & OpenAI)
YUNWU_ANTHROPIC_BASE_URL = "https://yunwu.ai"
YUNWU_OPENAI_BASE_URL = "https://yunwu.ai/v1"

# OpenRouter endpoint URLs (proxies official Claude & OpenAI)
OPENROUTER_ANTHROPIC_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_OPENAI_BASE_URL = "https://openrouter.ai/api/v1"

# Default base URLs for known providers
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}


# =============================================================================
# ID Generation
# =============================================================================

def _generate_provider_id() -> str:
    """Generate a unique provider ID with 'prov_' prefix + 8 random chars"""
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(8))
    return f"prov_{suffix}"


def _generate_group_id() -> str:
    """Generate a unique linked_group ID"""
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(8))
    return f"grp_{suffix}"


# =============================================================================
# Provider Builders
# =============================================================================

def _build_netmind_providers(api_key: str) -> list[ProviderConfig]:
    """Build two providers from a single NetMind API key (anthropic + openai protocol)"""
    now = datetime.now(timezone.utc)
    group_id = _generate_group_id()

    anthropic_models = get_default_models("netmind", "anthropic")
    openai_models = get_default_models("netmind", "openai")

    return [
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="NetMind (Anthropic)",
            source=ProviderSource.NETMIND,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            api_key=api_key,
            base_url=NETMIND_ANTHROPIC_BASE_URL,
            models=anthropic_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="NetMind (OpenAI)",
            source=ProviderSource.NETMIND,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=api_key,
            base_url=NETMIND_OPENAI_BASE_URL,
            models=openai_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
    ]


def _build_yunwu_providers(api_key: str) -> list[ProviderConfig]:
    """Build two providers from a single Yunwu API key (anthropic + openai protocol)"""
    now = datetime.now(timezone.utc)
    group_id = _generate_group_id()

    anthropic_models = get_default_models("yunwu", "anthropic")
    openai_models = get_default_models("yunwu", "openai")

    return [
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="Yunwu (Anthropic)",
            source=ProviderSource.YUNWU,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            api_key=api_key,
            base_url=YUNWU_ANTHROPIC_BASE_URL,
            models=anthropic_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="Yunwu (OpenAI)",
            source=ProviderSource.YUNWU,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=api_key,
            base_url=YUNWU_OPENAI_BASE_URL,
            models=openai_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
    ]


def _build_openrouter_providers(api_key: str) -> list[ProviderConfig]:
    """Build two providers from a single OpenRouter API key (anthropic + openai protocol)"""
    now = datetime.now(timezone.utc)
    group_id = _generate_group_id()

    anthropic_models = get_default_models("openrouter", "anthropic")
    openai_models = get_default_models("openrouter", "openai")

    return [
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="OpenRouter (Anthropic)",
            source=ProviderSource.OPENROUTER,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            api_key=api_key,
            base_url=OPENROUTER_ANTHROPIC_BASE_URL,
            models=anthropic_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="OpenRouter (OpenAI)",
            source=ProviderSource.OPENROUTER,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=api_key,
            base_url=OPENROUTER_OPENAI_BASE_URL,
            models=openai_models,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
    ]


def _build_claude_oauth_provider() -> ProviderConfig:
    """Build a provider for Claude Code OAuth (no key needed).

    OAuth bounces through the official Anthropic API, so server-side tools
    (WebSearch, text editor, computer use) all work.
    """
    now = datetime.now(timezone.utc)
    models = get_default_models("claude_oauth", "anthropic")
    return ProviderConfig(
        provider_id=_generate_provider_id(),
        name="Claude Code (OAuth)",
        source=ProviderSource.CLAUDE_OAUTH,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.OAUTH,
        api_key="",
        base_url="",
        models=models,
        supports_anthropic_server_tools=True,
        created_at=now,
        updated_at=now,
    )


def _build_user_provider(
    name: str,
    protocol: ProviderProtocol,
    auth_type: AuthType,
    api_key: str,
    base_url: str,
    models: list[str],
    supports_anthropic_server_tools: Optional[bool] = None,
) -> ProviderConfig:
    """Build a user-configured provider (Anthropic or OpenAI protocol).

    ``supports_anthropic_server_tools``:
        None  → auto-detect (True iff Anthropic protocol AND base_url
                 points at the official api.anthropic.com).
        bool  → honour caller's explicit value.
    """
    now = datetime.now(timezone.utc)

    # Use default base_url if empty
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(protocol.value, "")

    # Pre-populate models from suggestions if none provided
    if not models:
        models = get_default_models("user", protocol.value)

    if supports_anthropic_server_tools is None:
        # Heuristic: only the official Anthropic endpoint has server tools.
        # Users who front official with a proxy can still flip this via the
        # explicit parameter (or later through the provider-edit flow).
        supports_anthropic_server_tools = (
            protocol == ProviderProtocol.ANTHROPIC
            and "api.anthropic.com" in (base_url or "").lower()
        )

    return ProviderConfig(
        provider_id=_generate_provider_id(),
        name=name or f"Custom ({protocol.value.title()})",
        source=ProviderSource.USER,
        protocol=protocol,
        auth_type=auth_type,
        api_key=api_key,
        base_url=base_url,
        models=models,
        supports_anthropic_server_tools=supports_anthropic_server_tools,
        created_at=now,
        updated_at=now,
    )


# =============================================================================
# Provider Registry
# =============================================================================

class ProviderRegistry:
    """
    Manages the LLM provider configuration file (~/.nexusagent/llm_config.json).

    Responsible for:
    - Loading/saving the config file
    - Atomic provider addition (4 card types)
    - Removing providers (with linked group support)
    - Slot assignment and validation
    - Connection testing
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or CONFIG_FILE

    # ---- File I/O ----

    def load(self) -> Optional[LLMConfig]:
        """
        Load configuration from disk.

        Returns:
            LLMConfig if the file exists and is valid, None otherwise
        """
        if not self._config_path.is_file():
            return None
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            return LLMConfig.model_validate(raw)
        except Exception as e:
            logger.exception(f"Failed to load LLM config from {self._config_path}: {e}")
            return None

    def save(self, config: LLMConfig) -> None:
        """Save configuration to disk."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = config.model_dump(mode="json")
        self._config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"LLM config saved to {self._config_path}")

    def config_exists(self) -> bool:
        """Check if a configuration file exists on disk."""
        return self._config_path.is_file()

    # ---- Atomic Provider Addition ----

    def add_provider(
        self,
        card_type: str,
        name: str = "",
        api_key: str = "",
        base_url: str = "",
        auth_type: str = "api_key",
        models: Optional[list[str]] = None,
    ) -> tuple[LLMConfig, list[str]]:
        """
        Atomic add of provider(s). Loads config, adds provider(s), saves, returns.

        Card types:
        - "netmind": Creates 2 providers (anthropic + openai). Unique — removes existing NetMind first.
        - "claude_oauth": Creates 1 anthropic-protocol OAuth provider. Unique — removes existing CC first.
        - "anthropic": Creates 1 anthropic-protocol provider. Can have multiple.
        - "openai": Creates 1 openai-protocol provider. Can have multiple.

        Args:
            card_type: One of "netmind", "claude_oauth", "anthropic", "openai"
            name: Display name (for anthropic/openai cards)
            api_key: API key (not needed for claude_oauth)
            base_url: Base URL (defaults to official if empty)
            auth_type: "api_key" or "bearer_token" (for anthropic/openai cards)
            models: Model ID list (pre-populated from catalog if None)

        Returns:
            (Updated LLMConfig, list of new provider_ids)
        """
        config = self.load() or LLMConfig()
        new_ids: list[str] = []

        if card_type == "netmind":
            # Unique: remove existing NetMind providers first
            self._remove_by_source(config, ProviderSource.NETMIND)
            providers = _build_netmind_providers(api_key)
            for prov in providers:
                config.providers[prov.provider_id] = prov
                new_ids.append(prov.provider_id)

        elif card_type == "yunwu":
            # Unique: remove existing Yunwu providers first
            self._remove_by_source(config, ProviderSource.YUNWU)
            providers = _build_yunwu_providers(api_key)
            for prov in providers:
                config.providers[prov.provider_id] = prov
                new_ids.append(prov.provider_id)

        elif card_type == "openrouter":
            # Unique: remove existing OpenRouter providers first
            self._remove_by_source(config, ProviderSource.OPENROUTER)
            providers = _build_openrouter_providers(api_key)
            for prov in providers:
                config.providers[prov.provider_id] = prov
                new_ids.append(prov.provider_id)

        elif card_type == "claude_oauth":
            # Unique: remove existing Claude OAuth providers first
            self._remove_by_source(config, ProviderSource.CLAUDE_OAUTH)
            prov = _build_claude_oauth_provider()
            config.providers[prov.provider_id] = prov
            new_ids.append(prov.provider_id)

        elif card_type in ("anthropic", "openai"):
            protocol = ProviderProtocol(card_type)
            auth = AuthType(auth_type)
            prov = _build_user_provider(
                name=name,
                protocol=protocol,
                auth_type=auth,
                api_key=api_key,
                base_url=base_url,
                models=models or [],
            )
            config.providers[prov.provider_id] = prov
            new_ids.append(prov.provider_id)

        else:
            raise ValueError(f"Unknown card_type: '{card_type}'")

        self.save(config)
        return config, new_ids

    def _remove_by_source(self, config: LLMConfig, source: ProviderSource) -> None:
        """Remove all providers with a given source, clearing affected slots."""
        ids_to_remove = {
            pid for pid, p in config.providers.items()
            if p.source == source
        }
        for pid in ids_to_remove:
            config.providers.pop(pid, None)
        # Clear slots referencing removed providers
        for slot_name, slot_cfg in list(config.slots.items()):
            if slot_cfg.provider_id in ids_to_remove:
                del config.slots[slot_name]

    # ---- Provider Removal ----

    def remove_provider(self, config: LLMConfig, provider_id: str) -> LLMConfig:
        """
        Remove a provider. For linked groups (NetMind), removes all in the group.
        Clears any slots referencing the removed provider(s).

        Args:
            config: Existing configuration
            provider_id: The provider to remove

        Returns:
            Updated LLMConfig
        """
        if provider_id not in config.providers:
            return config

        ids_to_remove: set[str] = set()
        group = config.providers[provider_id].linked_group
        if group:
            ids_to_remove = {
                pid for pid, p in config.providers.items()
                if p.linked_group == group
            }
        else:
            ids_to_remove = {provider_id}

        for pid in ids_to_remove:
            config.providers.pop(pid, None)

        # Clear slots referencing removed providers
        for slot_name, slot_cfg in list(config.slots.items()):
            if slot_cfg.provider_id in ids_to_remove:
                del config.slots[slot_name]

        return config

    # ---- Provider Model Update ----

    def update_provider_models(
        self, config: LLMConfig, provider_id: str, models: list[str]
    ) -> LLMConfig:
        """
        Update the available models list for a provider.

        Args:
            config: Existing configuration
            provider_id: The provider to update
            models: New list of model IDs

        Returns:
            Updated LLMConfig

        Raises:
            ValueError: If provider not found
        """
        if provider_id not in config.providers:
            raise ValueError(f"Provider '{provider_id}' not found")

        config.providers[provider_id].models = models
        config.providers[provider_id].updated_at = datetime.now(timezone.utc)
        return config

    # ---- Slot Assignment ----

    def set_slot(
        self,
        config: LLMConfig,
        slot_name: str | SlotName,
        provider_id: str,
        model: str,
    ) -> LLMConfig:
        """
        Assign a provider + model to a slot.

        Validates that the provider's protocol matches the slot's requirements.

        Raises:
            ValueError: If protocol mismatch or provider not found
        """
        slot_str = slot_name.value if isinstance(slot_name, SlotName) else slot_name

        if provider_id not in config.providers:
            raise ValueError(f"Provider '{provider_id}' not found")

        provider = config.providers[provider_id]
        required = SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])
        if required and provider.protocol not in required:
            raise ValueError(
                f"Slot '{slot_str}' requires protocol {[p.value for p in required]}, "
                f"but provider '{provider.name}' uses '{provider.protocol.value}'"
            )

        config.slots[slot_str] = SlotConfig(provider_id=provider_id, model=model)
        return config

    # ---- Validation ----

    def validate(self, config: LLMConfig) -> list[str]:
        """
        Validate that all slots are properly configured.

        Returns:
            List of error messages. Empty list means all OK.
        """
        errors: list[str] = []

        for slot_name in SlotName:
            slot_str = slot_name.value
            if slot_str not in config.slots:
                errors.append(f"Slot '{slot_str}' is not configured")
                continue

            slot_cfg = config.slots[slot_str]

            if slot_cfg.provider_id not in config.providers:
                errors.append(
                    f"Slot '{slot_str}' references non-existent provider '{slot_cfg.provider_id}'"
                )
                continue

            provider = config.providers[slot_cfg.provider_id]

            if not provider.is_active:
                errors.append(f"Slot '{slot_str}' references disabled provider '{provider.name}'")

            required = SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])
            if required and provider.protocol not in required:
                errors.append(
                    f"Slot '{slot_str}' requires {[p.value for p in required]}, "
                    f"but provider '{provider.name}' uses '{provider.protocol.value}'"
                )

            if not slot_cfg.model:
                errors.append(f"Slot '{slot_str}' has no model specified")

            if provider.auth_type != AuthType.OAUTH and not provider.api_key:
                errors.append(f"Provider '{provider.name}' for slot '{slot_str}' has no API key")

        return errors

    # ---- Connection Test ----

    async def test_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """
        Test connectivity to a provider with a minimal API call.

        Returns:
            (success: bool, message: str)
        """
        if provider.auth_type == AuthType.OAUTH:
            return True, "OAuth provider (managed by Claude Code CLI)"

        try:
            if provider.protocol == ProviderProtocol.OPENAI:
                return await self._test_openai_provider(provider)
            elif provider.protocol == ProviderProtocol.ANTHROPIC:
                return await self._test_anthropic_provider(provider)
            else:
                return False, f"Unknown protocol: {provider.protocol}"
        except Exception as e:
            return False, f"Connection failed: {e}"

    async def _test_openai_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """Test an OpenAI-protocol provider.

        Strategy:
        - Official OpenAI: GET /v1/models (zero token cost, lists available models)
        - Non-official (proxy): POST /chat/completions with the provider's first
          configured model and max_tokens=1 (minimal real request)
        """
        import httpx
        from xyz_agent_context.agent_framework.model_catalog import is_official_provider

        base_url = provider.base_url or "https://api.openai.com/v1"
        headers: dict[str, str] = {"Authorization": f"Bearer {provider.api_key}"}

        if is_official_provider("openai", provider.base_url):
            # Official: GET /models — no token cost
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
            if resp.status_code == 200:
                return True, "Connected successfully"
            elif resp.status_code == 401:
                return False, "Authentication failed (invalid API key)"
            elif resp.status_code == 403:
                return False, "Access denied (check API key permissions)"
            else:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        else:
            # Non-official: minimal chat completion with a real model name
            test_model = provider.models[0] if provider.models else "test"
            headers["Content-Type"] = "application/json"
            payload = {"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)

            return self._interpret_test_response(resp)

    async def _test_anthropic_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """Test an Anthropic-protocol provider.

        Strategy:
        - Official Anthropic: GET /v1/models (zero token cost)
        - Non-official (proxy): POST /v1/messages with the provider's first
          configured model and max_tokens=1 (minimal real request)
        """
        import httpx
        from xyz_agent_context.agent_framework.model_catalog import is_official_provider

        base_url = provider.base_url or "https://api.anthropic.com"
        headers: dict[str, str] = {"anthropic-version": "2023-06-01"}
        if provider.auth_type == AuthType.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        else:
            headers["X-Api-Key"] = provider.api_key

        if is_official_provider("anthropic", provider.base_url):
            # Official: GET /models — no token cost
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{base_url}/v1/models", headers=headers)
            if resp.status_code == 200:
                return True, "Connected successfully"
            elif resp.status_code == 401:
                return False, "Authentication failed (invalid API key)"
            elif resp.status_code == 403:
                return False, "Access denied (check API key permissions)"
            else:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        else:
            # Non-official: minimal messages request with a real model name
            test_model = provider.models[0] if provider.models else "test"
            headers["Content-Type"] = "application/json"
            payload = {"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{base_url}/v1/messages", json=payload, headers=headers)

            return self._interpret_test_response(resp)

    @staticmethod
    def _interpret_test_response(resp) -> tuple[bool, str]:
        """Interpret HTTP response from a real model test request."""
        if resp.status_code == 200:
            return True, "Connected successfully"
        elif resp.status_code in (400, 404, 422):
            # Auth passed, model/payload issue — auth works
            return True, "Authentication verified (API reachable)"
        elif resp.status_code == 401:
            return False, "Authentication failed (invalid API key)"
        elif resp.status_code == 403:
            return False, "Access denied (check API key permissions)"
        elif resp.status_code in (502, 503, 529):
            return True, "API reachable (upstream temporarily unavailable)"
        else:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


# =============================================================================
# Singleton Instance
# =============================================================================

provider_registry = ProviderRegistry()
