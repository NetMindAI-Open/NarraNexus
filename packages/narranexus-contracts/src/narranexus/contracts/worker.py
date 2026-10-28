"""
@file_name: worker.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for supervised background workers (slot ``backend.workers``).

A worker is a long-running coroutine the host supervises with backoff
restarts. The plugin hands the host a factory that builds a fresh
``WorkerHandle`` per (re)start; ``host`` says which process runs it
(the workers supervisor by default; ``backend`` for lifespan tasks that must
share the API process). The host namespaces plugin workers as
``<owner>:<name>`` so two plugins may both call a worker ``sync``.

Contract version: ``API_VERSIONS["worker"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable

WorkerHost = Literal["workers", "backend"]


@runtime_checkable
class WorkerHandle(Protocol):
    """One run of a worker: the blocking coroutine and a graceful stop."""

    run: Awaitable[None]
    stop: Callable[[], Any]


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    # Called once per (re)start with the host's supervisor context (opaque here).
    factory: Callable[[Any], Awaitable[WorkerHandle]]
    host: WorkerHost = "workers"
    stable_after_s: float = 60.0

    def __post_init__(self) -> None:
        if not self.name or ":" in self.name:
            raise ValueError(f"worker name must be non-empty and contain no ':' (host namespaces it), got {self.name!r}")
        if self.host not in ("workers", "backend"):
            raise ValueError(f"host must be 'workers' or 'backend', got {self.host!r}")


__all__ = ["WorkerHandle", "WorkerHost", "WorkerSpec"]
