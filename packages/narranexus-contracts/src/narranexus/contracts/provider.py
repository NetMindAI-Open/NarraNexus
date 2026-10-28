"""
@file_name: provider.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for LLM provider drivers (slot ``model.providers``).

One driver per provider type (custom_anthropic, netmind, openrouter, ...). A
driver is a thin, stateless translator from a provider card (credential +
endpoint + model list) to the per-slot config objects the frameworks and the
helper LLM consume. The concrete config types live in the legacy package's
``api_config`` today; this contract is structural (``typing.Protocol``) and
names them by role only, so it stays a leaf.

Contract version: ``API_VERSIONS["provider"]``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderDriver(Protocol):
    """Structural contract satisfied by every provider driver class.

    Implementations should be cheap to instantiate — each LLM call builds a new
    driver from a freshly-read card. Caching belongs to the resolver, not here.
    ``build_*`` methods raise ``NotImplementedError`` for protocols the provider
    does not speak; callers treat that as "this card cannot fill that slot".
    """

    card: Any

    @classmethod
    def driver_type(cls) -> str:
        """Registry key; must equal the value stored in ``user_providers.driver_type``."""
        ...

    def build_claude_config(self, model: str) -> Any:
        """Config for the AGENT slot over the anthropic protocol."""
        ...

    def build_openai_config(self, model: str) -> Any:
        """Config for the HELPER_LLM slot over the openai protocol."""
        ...

    def build_anthropic_helper_config(self, model: str) -> Any:
        """Config for the HELPER_LLM slot over direct anthropic Messages calls."""
        ...

    def build_cli_helper_config(self, model: str) -> Any:
        """Config for the HELPER_LLM slot when it rides a subscription CLI."""
        ...

    def build_codex_config(
        self,
        model: str,
        *,
        thinking: str = "",
        reasoning_effort: str = "",
    ) -> Any:
        """Config for the AGENT slot when the framework is ``codex_cli``."""
        ...

    async def probe(self) -> Any:
        """Active credential + endpoint reachability check; returns a health object
        with at least ``ok: bool`` and ``detail: str``."""
        ...

    def models(self) -> list[str]:
        """Model ids the user marked usable on this card."""
        ...


__all__ = ["ProviderDriver"]
