"""
@file_name: reflect.py
@author: Bin Liang
@date: 2026-09-04
@description: Reflect stage default strategy: steps 5–6 (module after-turn hooks + callback instances) dispatched to the background.

Moved verbatim: owner helper credentials are injected first; a credential
error alerts the owner instead of raising; the cost context is cleared at
the end. The stage yields the legacy "Post-processing (background)"
progress message.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator

from loguru import logger

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


class BackgroundReflect:
    stage = Stage.REFLECT

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar
        from narranexus.platform.schema import ProgressMessage, ProgressStatus
        from narranexus.platform.utils.background_tasks import spawn as _spawn_bg
        from narranexus.platform.utils.cost_tracker import clear_cost_context

        ctx, s = inputs.ctx, inputs.services
        _bg_start = time.monotonic()
        _agent_id = ctx.agent_id
        _event_id = str(ctx.event.id) if ctx.event and ctx.event.id else ""

        async def _run_hooks_background():
            from narranexus.platform.agent_framework.llm.failure import is_credential_error
            from narranexus.platform.agent_framework.providers.resolver import ProviderResolverError, inject_owner_helper_credentials
            from narranexus.platform.services.background_llm_alerts import alert_background_llm_failure
            from narranexus.platform.utils.db.db_factory import get_db_client

            owner_user_id = None
            try:
                _db = await get_db_client()
                owner_user_id = await inject_owner_helper_credentials(_agent_id, _db)
            except ProviderResolverError as e:
                owner_user_id = ((await _db.get_one("agents", {"agent_id": _agent_id}) or {}).get("created_by"))
                await alert_background_llm_failure(agent_id=_agent_id, owner_user_id=owner_user_id, source="post_turn_hooks", error=e, source_id=_event_id)
                clear_cost_context()
                return
            except Exception as e:  # noqa: BLE001 — best-effort injection
                logger.warning(f"[BG] helper-credential injection failed for {_agent_id}: {e}")
            try:
                hook_callback_results = None
                async for msg in ar.step_5_execute_hooks(ctx, s.hook_manager):
                    if isinstance(msg, ProgressMessage):
                        pass
                    else:
                        hook_callback_results = msg
                if hook_callback_results:
                    await s.hook_manager.hook_callback_results(
                        hook_callback_results=hook_callback_results,
                        narrative=ctx.main_narrative,
                        narrative_service=s.narrative_service,
                        execute_callback_instance=s.execute_callback_instance,
                    )
                elapsed = time.monotonic() - _bg_start
                logger.info(f"[BG] Steps 5-6 completed for {_agent_id} in {elapsed:.1f}s")
            except Exception as e:  # noqa: BLE001
                elapsed = time.monotonic() - _bg_start
                if is_credential_error(e):
                    await alert_background_llm_failure(agent_id=_agent_id, owner_user_id=owner_user_id, source="post_turn_hooks", error=e, source_id=_event_id)
                logger.exception(f"[BG] Steps 5-6 failed for {_agent_id} after {elapsed:.1f}s: {e}")
            finally:
                clear_cost_context()

        _spawn_bg(_run_hooks_background(), name=f"post_turn_hooks:{_agent_id}:{_event_id or '-'}")
        logger.info(f"[BG] Steps 5-6 dispatched to background for {_agent_id}")
        yield ProgressMessage(
            step="5",
            title="Post-processing (background)",
            description="✓ Module hooks dispatched to background",
            status=ProgressStatus.COMPLETED,
            substeps=["Entity extraction, memory writes, job analysis running in background"],
        )


__all__ = ["BackgroundReflect"]
