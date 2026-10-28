"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: Contract for agent data-access providers (slot ``agent.capabilities.data_access``).

``DirectStore`` (module/data_access/store.py) is the seam every MCP tool
talks to; locally it reaches the repositories directly. The per-capability
bodies of that seam — how awareness is rewritten, how a social entity is
merged, how a job is created — belong to the plugin that owns the capability,
so each builtin registers a ``DataAccessSpec`` per ``AgentDataStore`` method
it implements and the store dispatches by name. The store keeps what is
platform policy: input rejects/clamps mirrored from the routes (parity with
``HttpStore``) and the "never raise, always return the tool's dict" invariant.

``handler(db, *args, **kwargs)`` receives the store's db client first, then
the method's own arguments in the ``AgentDataStore`` order.

Contract version: ``API_VERSIONS["data_access"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class DataAccessSpec:
    name: str  # the AgentDataStore method this implements
    handler: Callable[..., Awaitable[Any]]

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"data-access name must be a method name, got {self.name!r}")


__all__ = ["DataAccessSpec"]
