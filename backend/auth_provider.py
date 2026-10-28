"""
@file_name: auth_provider.py
@author: Bin Liang
@date: 2026-09-04
@description: The bound ``kernel.auth`` provider (authProviders, spec section 19.5): which plugin answers "who is this request?" for this process. The distribution's ``auth`` names it; without a distribution the deployment mode picks the matching builtin (cloud → builtin.auth.netmind, local → builtin.auth.local). The registry entry is resolved once per plugin id and cached; nothing bound = fail-closed.
"""
from __future__ import annotations

from typing import Any

from narranexus.contracts.services import AuthProvider
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

AUTH_SLOT = "kernel.auth"
LOCAL_PROVIDER = "builtin.auth.local"
CLOUD_PROVIDER = "builtin.auth.netmind"

_CACHE: dict[str, AuthProvider] = {}


def bound_provider_id() -> str:
    """The plugin id bound to ``kernel.auth``: the distribution's ``auth``, else the builtin matching the deployment mode."""
    from backend.plugins_boot import distribution
    from narranexus.kernel.plugins.bound import bound_layer, bound_provider

    if bound_layer(KERNEL_REGISTRIES, AUTH_SLOT) != "DEFAULT":
        return bound_provider(KERNEL_REGISTRIES, AUTH_SLOT) or LOCAL_PROVIDER
    res = distribution()
    if res is not None:
        return res.spec.auth
    # The same mode predicate the middleware branches on (tests patch it there).
    from backend.auth import _is_cloud_mode

    return CLOUD_PROVIDER if _is_cloud_mode() else LOCAL_PROVIDER


def auth_provider() -> AuthProvider:
    """The provider instance for the bound plugin (built once per plugin id from its registry contribution)."""
    pid = bound_provider_id()
    if pid in _CACHE:
        return _CACHE[pid]
    registry = KERNEL_REGISTRIES.registry_for(AUTH_SLOT)
    entry = next((e for e in registry.entries() if e.owner == pid), None)
    if entry is None:
        raise RuntimeError(
            f"{AUTH_SLOT}: bound provider {pid!r} has no contribution in this process "
            f"(loaded: {sorted({e.owner for e in registry.entries()})}); refusing to authenticate"
        )
    provider: Any = entry.factory()
    if not isinstance(provider, AuthProvider):
        raise RuntimeError(f"{AUTH_SLOT}: {pid!r} built {type(provider).__name__}, which is not an AuthProvider")
    _CACHE[pid] = provider
    return provider


def reset_auth_provider() -> None:
    """Forget the cached provider (tests; a process runs one distribution, so production never needs this)."""
    _CACHE.clear()


__all__ = ["AUTH_SLOT", "CLOUD_PROVIDER", "LOCAL_PROVIDER", "auth_provider", "bound_provider_id", "reset_auth_provider"]
