"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-03
@description: Host service contracts named by the ``kernel.*`` slots, plus the ``ServiceRef``s that cross the plugin boundary.

The Protocols are distribution-level choices a plugin never binds at runtime.
Batch 1 fixes their shapes as structural Protocols so every slot in the kernel
tree points at a symbol that exists; the kernel's current implementations
(sqlite/mysql backends, secret_box, local/NetMind auth, the EventBus) become
their default providers when those packages migrate under the kernel.

The ``ServiceRef`` constants at the bottom used to live in
``narranexus/kernel/plugins/service_refs.py``, which meant the kernel — the
one tree where "adding an implementation must not require an edit" is a
charter rule (§2.9) — knew two business plugins by name, and a fourth
cross-plugin service could not be added without editing it. They belong to
the contract package: a ref is a name plus a type, and both sides of the call
(provider and consumer) already depend on contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Iterable, Mapping, Optional, Protocol, TypeVar, runtime_checkable

from narranexus.contracts._base import PluginError
from narranexus.contracts.job import JobRunOutcome
from narranexus.contracts.skill import SkillWorkspace
from narranexus.contracts.web import WebHost

T = TypeVar("T")


@runtime_checkable
class DatabaseBackend(Protocol):
    """Slot ``kernel.db``: the dialect-specific database driver.

    Traced from ``narranexus.platform.utils.db.db_backend.DatabaseBackend`` (the
    ABC the sqlite / sqlite_proxy / mysql backends implement) — same name on
    purpose: it IS that service, seen through the slot. Only the driver
    subset is the contract (``execute`` / ``execute_write`` / ``probe`` /
    ``placeholder`` / ``dialect``); the CRUD helpers are the
    AsyncDatabaseClient's business. ``probe`` returns ``None`` and raises on
    failure, exactly as the ABC does.
    ``tests/nx_kernel/contracts/test_services_shapes.py`` pins signature
    parity with the ABC so the two cannot drift silently.
    """

    @property
    def dialect(self) -> str: ...

    @property
    def placeholder(self) -> str: ...

    async def execute(self, query: str, params: Optional[tuple] = None) -> list[dict[str, Any]]: ...

    async def execute_write(self, query: str, params: Optional[tuple] = None) -> int: ...

    async def probe(self) -> None: ...


@runtime_checkable
class SecretStore(Protocol):
    """Slot ``kernel.secrets``: secrets encrypted at rest, keyed by owner + name.

    No in-repo implementation has this shape today: the marketplace
    ``SecretBox`` is an encrypt/decrypt codec, not a store. The
    get/put/delete-by-owner-and-name surface is the slot's own definition,
    to be traced against the first provider that lands.
    """

    def get(self, owner: str, name: str) -> str | None: ...

    def put(self, owner: str, name: str, value: str) -> None: ...

    def delete(self, owner: str, name: str) -> None: ...


@runtime_checkable
class AuthProvider(Protocol):
    """Slot ``kernel.auth`` (distribution-only): resolves a request to an identity or ``None``.

    The request → identity step of the backend auth middleware: a mapping of
    claims, or ``None`` for an anonymous request.
    """

    async def authenticate(self, request: Any) -> Mapping[str, Any] | None: ...


@runtime_checkable
class EventSink(Protocol):
    """Slot ``kernel.events``: where host events are emitted (``narranexus.kernel.events.EventBus.emit``)."""

    async def emit(self, name: str, payload: Mapping[str, Any]) -> Any: ...


# =============================================================================
# Cross-boundary service refs
# =============================================================================


@dataclass(frozen=True)
class ServiceRef(Generic[T]):
    """A typed key for a service on the host locator; equality is by id.

    Lives here, not in the kernel, because a ref is contract data: both sides
    of the call (the plugin that ``expose``s and the one that ``require``s)
    already depend on contracts, and neither should need the kernel to name a
    service. ``narranexus.kernel.plugins.services`` re-exports it for the
    locator implementation, and ``narranexus.sdk`` for plugin authors.
    """

    id: str

    def __post_init__(self) -> None:
        if not self.id or "/" in self.id or " " in self.id:
            raise PluginError(f"service ref id must be a non-empty token, got {self.id!r}")


#: ``(agent_id, user_id) -> SkillWorkspace`` — provided by builtin.skills.
SKILL_WORKSPACES: ServiceRef[Callable[[str, Optional[str]], SkillWorkspace]] = ServiceRef("skills.workspaces")
#: ``(db) -> the job instance service over that db`` — provided by builtin.job.
JOB_INSTANCES: ServiceRef[Callable[[Any], Any]] = ServiceRef("jobs.instances")
#: ``async (agent_id, job_id) -> JobRunOutcome`` — provided by builtin.job.
JOB_RUN_ONCE: ServiceRef[Callable[[str, str], Awaitable[JobRunOutcome]]] = ServiceRef("jobs.run_once")
#: ``async (db, user_id, paused_reasons) -> resumed count`` — provided by builtin.job.
#: Resumes the jobs an account suspension paused (admin reinstate).
JOB_RESUME_FOR_PRINCIPAL: ServiceRef[Callable[[Any, str, Iterable[str]], Awaitable[int]]] = ServiceRef(
    "jobs.resume_for_principal"
)
#: The request-scoped HTTP host API (``contracts.web.WebHost``) — provided by the backend host.
WEB_HOST: ServiceRef[WebHost] = ServiceRef("host.web")

__all__ = [
    "JOB_INSTANCES",
    "JOB_RESUME_FOR_PRINCIPAL",
    "JOB_RUN_ONCE",
    "SKILL_WORKSPACES",
    "WEB_HOST",
    "AuthProvider",
    "DatabaseBackend",
    "EventSink",
    "SecretStore",
    "ServiceRef",
]
