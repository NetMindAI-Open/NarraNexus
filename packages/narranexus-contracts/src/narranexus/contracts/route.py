"""
@file_name: route.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for HTTP route contributions (slot ``backend.routes``).

A plugin contributes a FastAPI router as data: the router object, the prefix
it is mounted under, and the auth mode. The backend host mounts every entry
after its own routers and before the SPA fallback; the prefix of a
third-party plugin is always ``/api/x/<plugin id>`` (the host enforces this
so plugins cannot shadow shell routes). Auth is fail-closed: ``"user"``
means the global auth middleware must have authenticated the caller;
``"none"`` is an explicit opt-out that the host records as an exempt prefix.

Contract version: ``API_VERSIONS["route"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

AuthMode = Literal["user", "none"]

API_PREFIX = "/api/"
PLUGIN_API_PREFIX = "/api/x/"


@dataclass(frozen=True)
class RouterSpec:
    """One router to mount. ``router`` is a FastAPI ``APIRouter`` (typed ``Any`` to keep this package framework-free)."""

    router: Any
    prefix: str
    auth: AuthMode = "user"
    quota_bypass: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.prefix.startswith(API_PREFIX) or self.prefix.endswith("/"):
            raise ValueError(f"route prefix must start with {API_PREFIX!r} and not end with '/', got {self.prefix!r}")
        if self.auth not in ("user", "none"):
            raise ValueError(f"auth must be 'user' or 'none', got {self.auth!r}")


def plugin_route_prefix(plugin_id: str) -> str:
    """The prefix a non-builtin plugin's routers must live under."""
    return f"{PLUGIN_API_PREFIX}{plugin_id}"


__all__ = ["API_PREFIX", "PLUGIN_API_PREFIX", "AuthMode", "RouterSpec", "plugin_route_prefix"]
