"""
@file_name: memory.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for memory kinds (slot ``agent.capabilities.memory_kinds``).

A memory kind is a declarative spec: how records of that kind are scoped,
recalled, weighted and rendered. The full spec type lives in the legacy
``memory.spec`` module today; this contract is the structural minimum every
spec must expose so the kernel can register and list kinds without knowing
the spec's internals.

Contract version: ``API_VERSIONS["memory"]``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryKindContract(Protocol):
    """Structural minimum of a memory-kind spec."""

    kind: str
    passive: bool


__all__ = ["MemoryKindContract"]
