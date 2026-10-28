"""Isolated Uvicorn child for the graceful-stop regression, never a real agent."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sys


def serve(root: Path, socket_fd: int) -> None:
    import uvicorn
    from fastapi import FastAPI, WebSocket
    from loguru import logger

    from narranexus.platform.settings import settings

    settings.database_url = f"sqlite://{root / 'probe.db'}"
    import backend.main as main
    import backend.routes.websocket as ws_mod
    from backend.run_lifecycle import RunTasks
    from narranexus.platform.agent_runtime.background_run import BackgroundRun
    from narranexus.platform.utils.db.db_factory import get_db_client

    def mark(name):
        (root / name).touch()

    class ProbeTasks(RunTasks):
        async def close(self, cleanup):
            mark("draining")
            await super().close(cleanup)

    class SyntheticRun(BackgroundRun):
        async def drive(self, **kwargs):
            await self.db.insert("events", {
                "event_id": "evt_sigterm_probe", "agent_id": self.agent_id,
                "user_id": self.user_id, "trigger": "chat", "trigger_source": "test",
                "state": "completed",
            })
            await self.emit({
                "type": "progress", "step": "0", "status": "completed",
                "details": {"event_id": "evt_sigterm_probe"},
            })
            try:
                while not (root / "finish").exists():
                    await asyncio.sleep(0.05)
                    await self.emit({"type": "agent_thinking", "thinking_content": "."})
                assert not self.cancellation.is_cancelled
                assert not (root / "housekeeping_stopped").exists()
                self.state = "completed"
            finally:
                await self._finalize()
                mark("finalized")

    async def housekeeping(_service):
        try:
            await asyncio.Event().wait()
        finally:
            assert (root / "finalized").exists()
            db = await get_db_client()
            assert await db.get_one("events", {"event_id": "evt_sigterm_probe"})
            mark("housekeeping_stopped")

    real_close = main.close_db_client

    async def close_db():
        assert (root / "finalized").exists()
        assert (root / "housekeeping_stopped").exists()
        await real_close()
        mark("db_closed")

    @asynccontextmanager
    async def lifespan(app):
        async with main.lifespan(app):
            # /ws/agent/run only drives an agent the caller can see.
            db = await get_db_client()
            await db.insert("agents", {
                "agent_id": "agent_synthetic", "agent_name": "Synthetic probe",
                "created_by": "user_synthetic",
            })
            mark("ready")
            yield
        mark("lifespan_done")

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws/agent/run")
    async def run(websocket: WebSocket):
        try:
            await ws_mod.websocket_agent_run(websocket)
        finally:
            mark("ws_detached")

    # Keep real lifespan, DB, run recorder, broadcaster and WS handler. Replace
    # unrelated boot/network/filesystem services and the LLM pipeline only.
    with (
        patch.object(main, "setup_logging"),
        patch.object(main, "RunTasks", ProbeTasks),
        patch.object(main, "close_db_client", close_db),
        patch.object(ws_mod, "BackgroundRun", SyntheticRun),
        patch.object(ws_mod, "_is_cloud_mode", return_value=False),
        patch.object(ws_mod, "_resolve_run_steerable", AsyncMock(return_value=False)),
        patch("backend.plugins_boot.boot_backend_plugins", return_value=Mock()),
        patch("backend.plugins_boot.fire_startup", AsyncMock()),
        patch("backend.plugins_factory.routes.service", return_value=SimpleNamespace()),
        patch("backend.plugins_host.start_backend_workers", AsyncMock(return_value=[])),
        patch("backend.plugins_host.stop_backend_workers", AsyncMock()),
        patch("narranexus.platform.services.skill_sync_service.SkillSyncService.run_forever", housekeeping),
        patch("narranexus.platform.services.memory_consolidation_worker.MemoryConsolidationWorker.start", AsyncMock()),
        patch("narranexus.platform.services.memory_consolidation_worker.MemoryConsolidationWorker.stop", AsyncMock()),
        patch("narranexus.platform.agent_runtime.executor_reaper.maybe_start_executor_reaper", return_value=None),
        patch("narranexus.platform.marketplace.skill_marketplace_service.is_registry_host", return_value=False),
        patch("narranexus.platform.utils.model_pricing.warm_cache"),
        patch("narranexus.platform.agent_runtime.run_recorder.HEARTBEAT_INTERVAL_S", 0.05),
    ):
        logger.info("Synthetic shutdown probe: {}", root)
        uvicorn.run(app, fd=socket_fd, log_level="info")


if __name__ == "__main__":
    serve(Path(sys.argv[1]), int(sys.argv[2]))
