"""
@file_name: registry.py
@author: Bin Liang
@date: 2026-03-23
@description: LLM Provider configuration management

Manages the llm_config.json file that stores provider definitions and
slot assignments. Provides atomic provider addition, validation, and
connection testing.

Usage:
    from narranexus.platform.agent_framework.providers.registry import provider_registry

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

from narranexus.platform.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
    SlotName,
)
from narranexus.platform.agent_framework.providers.framework_binding import (
    SLOT_REQUIRED_PROTOCOLS,
)
from narranexus.platform.agent_framework.providers.model_catalog import get_default_models
from narranexus.platform.utils import deployment_mode
from narranexus.platform.utils.url_safety import (
    assert_public_http_url_in_cloud,
    is_obviously_non_public_url,
)


# Shown to the user whenever a user-supplied base_url fails the egress gate.
# Deliberately generic: never echo the resolved address (it would turn the
# gate into a DNS oracle for the internal network).
UNSAFE_BASE_URL_MESSAGE = (
    "Base URL is not allowed: in the cloud it must be a public http(s) endpoint"
)


async def assert_user_base_url_allowed(base_url: str) -> None:
    """SSRF egress gate for a USER-supplied provider base_url.

    Cloud only, decided by the shared deployment-aware wrapper
    ``assert_public_http_url_in_cloud`` (the backend shares a network with the
    metadata endpoint and every internal service; local/desktop is a single
    trusted user who legitimately points at localhost — Ollama, LM Studio).
    An empty base_url means "provider default" (hard-coded official hosts),
    not user-controlled, so it is not gated.

    Raises ``UnsafeUrlError`` (a ``ValueError``) — or a bare ``ValueError`` for
    an unparseable URL — when the URL is refused. Callers fail closed on any
    exception.
    """
    if not base_url:
        return
    await assert_public_http_url_in_cloud(base_url)


# Every platform-owned value the ``user_providers.source`` column can hold:
# rows whose base_url WE wrote (netmind_free legitimately points at our
# internal gateway). This set, not ProviderSource membership, is what the
# egress gate trusts. The enum is NOT the column's value domain: ``system``
# (driver/derive.py → system_pool driver, system_quota billing) and
# ``system_pool`` (model_sync.apply_ledger_to_db) are live values for the
# cloud free-tier pool with no enum member. The two spellings are an existing
# inconsistency between derive.py and model_sync; both are trusted here.
# Listed explicitly (not derived from the enum) so a new source is gated until
# someone decides it is platform-owned — fail closed.
PLATFORM_PROVIDER_SOURCES: frozenset[str] = frozenset({
    ProviderSource.NETMIND.value,
    ProviderSource.NETMIND_FREE.value,
    ProviderSource.YUNWU.value,
    ProviderSource.OPENROUTER.value,
    ProviderSource.CLAUDE_OAUTH.value,
    ProviderSource.CODEX_OAUTH.value,
    "system",
    "system_pool",
})


def is_platform_provider_source(source: Optional[str]) -> bool:
    """True when a provider row's source is platform-owned (trusted base_url).

    The single criterion every egress decision keyed by source uses (save-time
    Test, saved-row Test, read path, runtime builder). ``user``, a missing
    source and any value not in ``PLATFORM_PROVIDER_SOURCES`` return False —
    the user-controlled, gated side (fail closed).
    """
    if isinstance(source, ProviderSource):
        source = source.value
    return (source or "").strip().lower() in PLATFORM_PROVIDER_SOURCES


# provider_ids already reported by the read-path screen. Pure log dedup, NOT
# state: it is per-process (each worker may log once), never shared, and never
# caches the block verdict — is_stored_user_base_url_blocked recomputes that on
# every call. Size is bounded by the number of offending legacy cards.
_REPORTED_BLOCKED_PROVIDER_IDS: set[str] = set()


def is_stored_user_base_url_blocked(
    source: Optional[str], base_url: Optional[str], provider_id: str
) -> bool:
    """Read-path egress screen for a STORED provider row (cloud only).

    The save path and the Test button run the full DNS-resolving gate, but rows
    saved before that gate existed can still point at an internal host and
    would keep driving server-side requests from the agent loop / helper LLM.
    This screen runs where a stored row becomes usable config, so it must be
    cheap: DNS-free ``is_obviously_non_public_url`` (literal private/metadata
    IPs, localhost, ``.local``, single-label docker service names such as
    ``llm-gateway``). It narrows the legacy-row gap; it is not a substitute
    for the save-time gate.

    Decided by the STORED source via ``is_platform_provider_source``, like the
    Test path: platform rows (netmind_free, the system / system_pool free-tier
    pool — all on our internal gateway) are trusted; ``user``, missing and
    genuinely unknown sources are screened (fail closed). An empty base_url
    means "provider default", not user-controlled. Local/desktop never blocks.
    ``provider_id`` keys the once-per-card warning.
    """
    if not base_url or not deployment_mode.is_cloud_mode():
        return False
    if is_platform_provider_source(source):
        return False
    if not is_obviously_non_public_url(base_url):
        return False
    if provider_id not in _REPORTED_BLOCKED_PROVIDER_IDS:
        _REPORTED_BLOCKED_PROVIDER_IDS.add(provider_id)
        logger.warning(
            f"[providers] stored user card {provider_id!r} has a non-public "
            f"base_url; treated as unusable in cloud (delete and re-add it "
            f"with a public endpoint)"
        )
    return True


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
        thinking: str = "",
        reasoning_effort: str = "",
    ) -> LLMConfig:
        """
        Assign a provider + model (+ neutral reasoning params) to a slot.

        Validates that the provider's protocol matches the slot's requirements.
        PUT semantics for the reasoning params: omitted = reset to "" (auto).
        Must stay behaviorally aligned with UserProviderService.set_slot
        (rule #7: the local file backend and the cloud DB backend are the
        same feature in two run modes).

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

        config.slots[slot_str] = SlotConfig(
            provider_id=provider_id,
            model=model,
            thinking=thinking,  # type: ignore[arg-type]
            reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
        )
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

    async def test_provider(
        self, provider: ProviderConfig, *, stored_source: Optional[str] = None
    ) -> tuple[bool, str]:
        """
        Test connectivity to a provider with a minimal API call.

        ``stored_source`` is the raw ``user_providers.source`` of the saved row
        this probe stands in for. When given, it — not ``provider.source`` —
        decides the egress gate, because the column holds platform values the
        ProviderSource enum cannot express (``system`` / ``system_pool``).
        Omitted for transient configs, whose ``provider.source`` decides.

        Returns:
            (success: bool, message: str)
        """
        if provider.auth_type == AuthType.OAUTH:
            # Fail CLOSED: a transient ProviderConfig carries no driver card,
            # so there is nothing this path can honestly verify. The old
            # unconditional pass here is the same lie the 2026-07-31 codex P0
            # exposed in user_service (dead CLI credentials testing green).
            # Saved OAuth rows are verified by UserProviderService.test_provider
            # → driver.verify_live; this transient path only ever sees api-key
            # onboarding probes today.
            return False, (
                "OAuth providers cannot be verified from a transient config — "
                "save the provider and use its Test action (live CLI check)"
            )

        # Egress gate BEFORE any request. Decided by the source: only a
        # user-supplied base_url is gated. Platform-issued cards (netmind,
        # netmind_free on our own gateway, yunwu, openrouter, the system pool)
        # carry fixed base_urls we wrote ourselves — the free-tier ones are
        # internal by design. Same criterion as the read-path screen.
        gate_source = provider.source if stored_source is None else stored_source
        if not is_platform_provider_source(gate_source):
            try:
                await assert_user_base_url_allowed(provider.base_url)
            except Exception as e:  # noqa: BLE001 — any decision failure fails closed
                logger.warning(
                    f"[provider-test] refused user base_url ({type(e).__name__}): {e}"
                )
                return False, UNSAFE_BASE_URL_MESSAGE

        try:
            if provider.protocol == ProviderProtocol.OPENAI:
                return await self._test_openai_provider(provider)
            elif provider.protocol == ProviderProtocol.ANTHROPIC:
                return await self._test_anthropic_provider(provider)
            else:
                return False, f"Unknown protocol: {provider.protocol}"
        except Exception as e:
            # Exception text can carry internal addresses / upstream detail —
            # log it, show the user a generic verdict.
            logger.warning(f"[provider-test] connection failed: {type(e).__name__}: {e}")
            return False, "Connection failed (could not reach the provider)"

    async def _test_openai_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """Test an OpenAI-protocol provider.

        Strategy:
        - Official OpenAI: GET /v1/models (zero token cost, lists available models)
        - Non-official (proxy): POST /chat/completions with the provider's first
          configured model and max_tokens=1 (minimal real request)
        """
        import httpx
        from narranexus.platform.agent_framework.providers.model_catalog import is_official_provider

        base_url = provider.base_url or "https://api.openai.com/v1"
        headers: dict[str, str] = {"Authorization": f"Bearer {provider.api_key}"}

        if is_official_provider("openai", provider.base_url):
            # Official: GET /models — no token cost
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
            if resp.status_code == 200:
                return True, "Connected successfully"
            elif resp.status_code == 401:
                return False, "Authentication failed (invalid API key)"
            elif resp.status_code == 403:
                return False, "Access denied (check API key permissions)"
            else:
                return self._unexpected_status(resp)
        else:
            # Non-official: minimal chat completion with a real model name
            test_model = provider.models[0] if provider.models else "test"
            headers["Content-Type"] = "application/json"
            payload = {"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}

            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
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
        from narranexus.platform.agent_framework.providers.model_catalog import is_official_provider

        base_url = provider.base_url or "https://api.anthropic.com"
        headers: dict[str, str] = {"anthropic-version": "2023-06-01"}
        if provider.auth_type == AuthType.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        else:
            headers["X-Api-Key"] = provider.api_key

        if is_official_provider("anthropic", provider.base_url):
            # Official: GET /models — no token cost
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                resp = await client.get(f"{base_url}/v1/models", headers=headers)
            if resp.status_code == 200:
                return True, "Connected successfully"
            elif resp.status_code == 401:
                return False, "Authentication failed (invalid API key)"
            elif resp.status_code == 403:
                return False, "Access denied (check API key permissions)"
            else:
                return self._unexpected_status(resp)
        else:
            # Non-official: minimal messages request with a real model name
            test_model = provider.models[0] if provider.models else "test"
            headers["Content-Type"] = "application/json"
            payload = {"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}

            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                resp = await client.post(f"{base_url}/v1/messages", json=payload, headers=headers)

            return self._interpret_test_response(resp)

    @staticmethod
    def _interpret_test_response(resp) -> tuple[bool, str]:
        """Interpret HTTP response from a real model test request."""
        if resp.status_code == 200:
            return True, "Connected successfully"
        elif resp.status_code in (400, 404, 422):
            # Normally a 400/404/422 means "auth passed, just a model/payload
            # issue" → the KEY works, so it's reachable. BUT some providers
            # report a self-serviceable, won't-run-until-fixed condition at these
            # codes too — NetMind's "balance not enough" is a 400, model-not-found
            # a 404, context-too-small a 400. Those are NOT "reachable/runnable":
            # a job readiness check must NOT treat them as ready, or it re-arms a
            # balance-dead job into the same wall. Detected via the shared
            # classifier (single source of truth) so this can't drift from the
            # Job/real-time layers. (Onboarding still proceeds — it only hard-
            # rejects on auth phrases; the "test connection" button now shows the
            # real cause instead of a misleading "OK".)
            from narranexus.platform.agent_framework.llm.failure import (
                classify_self_serviceable,
            )
            ss = classify_self_serviceable(None, getattr(resp, "text", "") or "")
            if ss is not None:
                return False, f"Provider reports {ss.replace('_', ' ')} (not runnable)"
            return True, "Authentication verified (API reachable)"
        elif resp.status_code == 401:
            return False, "Authentication failed (invalid API key)"
        elif resp.status_code == 403:
            return False, "Access denied (check API key permissions)"
        elif resp.status_code in (502, 503, 529):
            return True, "API reachable (upstream temporarily unavailable)"
        else:
            return ProviderRegistry._unexpected_status(resp)

    @staticmethod
    def _unexpected_status(resp) -> tuple[bool, str]:
        """Verdict for a status we don't interpret: status code only.

        The upstream body stays in the log — echoing it to the user is what
        turns a blind server-side fetch into a readable one.
        """
        body = (getattr(resp, "text", "") or "")[:200]
        logger.info(f"[provider-test] unexpected HTTP {resp.status_code}: {body}")
        return False, f"HTTP {resp.status_code} from the provider"


# =============================================================================
# Singleton Instance
# =============================================================================

provider_registry = ProviderRegistry()
