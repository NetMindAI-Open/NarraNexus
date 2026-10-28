"""
@file_name: engine.py
@author: Bin Liang
@date: 2026-09-04
@description: The headless engine (spec section 19.4): ``Engine.load(dist)`` brings the plugin platform up for one distribution without a web host, ``run_turn`` streams a turn's messages, ``events()`` is the host event bus plugins publish on, ``agents()`` lists agents. The backend host and the CLI boot through the same kernel objects; this is the library form of that boot, not another code path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from narranexus.hosts.boot import BootReport
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.distribution import DistributionResolution, load_distribution, resolve_distribution
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries

@dataclass
class Engine:
    """A booted plugin platform for one distribution, usable from any asyncio program."""

    registries: Registries
    report: BootReport
    distribution: DistributionResolution | None = None
    bus: EventBus = field(default_factory=EventBus)
    _db: Any = None
    _owns_db: bool = False

    # ---- construction ----------------------------------------------------
    @classmethod
    def load(
        cls,
        dist: str | Path | DistributionResolution | None = None,
        *,
        registries: Registries | None = None,
        cloud: bool | None = None,
        host_version: str | None = None,
        store: RegistryStore | None = None,
        bus: EventBus | None = None,
    ) -> "Engine":
        """Boot the platform for ``dist`` (a directory / file / resolved distribution; ``None`` = every builtin).

        The process registries are used unless private ones are given (tests);
        booting the process registries twice is refused by the kernel (frozen).
        """
        from narranexus.hosts.boot import boot
        from narranexus.kernel.deployment import is_cloud_mode
        from narranexus.kernel.plugins.compat import host_version as _host_version
        res: DistributionResolution | None
        if dist is None or isinstance(dist, DistributionResolution):
            res = dist
        else:
            spec, base = load_distribution(Path(dist))
            res = resolve_distribution(spec, base, host_version=host_version)
            res.raise_for_problems()
        regs = registries or KERNEL_REGISTRIES
        if regs.frozen:
            raise RuntimeError("Engine.load: these registries are already booted (one Engine per registries)")
        report = boot(
            "backend",
            registries=regs,
            cloud=is_cloud_mode() if cloud is None else cloud,
            host_version=host_version or _host_version(),
            store=store,
            distribution=res,
        )
        # Not marked healthy here: "booted" is not "proven healthy". The
        # embedding host calls engine.mark_healthy() after its own probe (or
        # after the first turn ran), which is when the boot-crash counter
        # clears and registry.json becomes the last-known-good snapshot.
        return cls(registries=regs, report=report, distribution=res, bus=bus or EventBus())

    def mark_healthy(self) -> None:
        """Declare this boot healthy: clears the crash counter and moves the last-known-good snapshot."""
        self.report.mark_healthy()

    # ---- turns -------------------------------------------------------------
    async def run_turn(
        self,
        agent_id: str,
        user_id: str,
        input: str,
        *,
        working_source: Any = "chat",
        silent: bool = False,
        fast_mode: bool = False,
        pipeline_profile: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Run one turn of ``agent_id`` for ``user_id`` and yield its messages as the pipeline produces them."""
        from narranexus.platform.agent_runtime import AgentRuntime

        runtime = AgentRuntime(database_client=await self.db(), registries=self.registries)
        async for msg in runtime.run(
            agent_id, user_id, input, working_source=working_source, silent=silent, fast_mode=fast_mode,
            pipeline_profile=pipeline_profile, **kwargs,
        ):
            yield msg

    # ---- data --------------------------------------------------------------
    async def db(self) -> Any:
        if self._db is None:
            from narranexus.platform.utils.db.db_factory import get_db_client

            self._db = await get_db_client()
            self._owns_db = True
        return self._db

    def use_db(self, db: Any) -> "Engine":
        """Run on a caller-owned database client (the caller closes it)."""
        self._db, self._owns_db = db, False
        return self

    async def agents(self, user_id: str | None = None) -> list[Mapping[str, Any]]:
        """Agents visible to ``user_id`` (their own plus public ones), or every agent when ``None``."""
        from narranexus.platform.repository.agent_repository import AgentRepository

        repo = AgentRepository(await self.db())
        rows = await repo.find({} if user_id is None else {"created_by": user_id})
        if user_id is not None:
            seen = {a.agent_id for a in rows}
            rows += [a for a in await repo.find({"is_public": True}) if a.agent_id not in seen]
        return [a.model_dump() for a in rows]

    # ---- events ----------------------------------------------------------
    def events(self) -> EventBus:
        """The event bus plugin contexts publish on (subscribe with ``events().subscribe(name, handler, owner=...)``)."""
        return self.bus

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        loaded = self.report.builtins.loaded if self.report.builtins else []
        return tuple(pl.plugin_id for pl in loaded if not pl.error) + self.report.user_plugin_ids

    # ---- lifecycle ---------------------------------------------------------
    async def close(self) -> None:
        if self._db is not None and self._owns_db:
            from narranexus.platform.utils.db.db_factory import close_db_client

            await close_db_client()
        self._db = None

    async def __aenter__(self) -> "Engine":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def run(coro: Any) -> Any:
    """Convenience for scripts: ``narranexus.engine.run(engine.agents())``."""
    return asyncio.run(coro)


__all__ = ["Engine", "run"]
