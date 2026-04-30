"""
@file_name: hook_manager.py
@author: NetMind.AI
@date: 2025-11-21
@description: Hook Manager - Manages Agent Runtime Hooks

Performance optimization:
- hook_after_event_execution: Parallel execution (modules are typically independent)
- hook_data_gathering: Supports both parallel and sequential modes

Benefits of parallel execution:
- Before (sequential): Total time = A + B + C = 300ms
- After (parallel): Total time = max(A, B, C) = 100ms

Hook types:
1. hook_data_gathering: Data collection phase, called when building Context
2. hook_after_event_execution: Post-event processing, returns HookCallbackResult
3. process_hook_callbacks: Processes callback results returned by hook_after_event_execution
"""

import asyncio
from typing import List, Optional, Dict, Callable, TYPE_CHECKING
from loguru import logger

from xyz_agent_context.module import XYZBaseModule
from xyz_agent_context.utils.logging import timed
from xyz_agent_context.schema import ContextData, HookAfterExecutionParams
from xyz_agent_context.schema.module_schema import HookCallbackResult
from xyz_agent_context.utils.exceptions import (
    DataGatheringError,
    HookExecutionError,
)

if TYPE_CHECKING:
    from xyz_agent_context.narrative import Narrative, NarrativeService


class HookManager:
    """
    Hook Manager

    Responsible for coordinating module hook execution, supports parallel execution to improve performance.
    """

    def __init__(self, parallel_data_gathering: bool = False):
        """
        Initialize HookManager

        Args:
            parallel_data_gathering: Whether to execute data_gathering hook in parallel
                - False (default): Sequential execution, safer (modules may modify ctx_data)
                - True: Parallel execution, faster (must ensure no write conflicts between modules)
        """
        self.parallel_data_gathering = parallel_data_gathering

    async def hook_data_gathering(
        self,
        module_list: List[XYZBaseModule],
        ctx_data: ContextData
    ) -> ContextData:
        """
        Have each Module perform data collection (data_gathering)

        Choose parallel or sequential execution based on configuration.

        Args:
            module_list: Module list
            ctx_data: Context data

        Returns:
            Updated ctx_data
        """
        if not module_list:
            return ctx_data

        if self.parallel_data_gathering:
            return await self._parallel_data_gathering(module_list, ctx_data)
        else:
            return await self._sequential_data_gathering(module_list, ctx_data)

    async def _sequential_data_gathering(
        self,
        module_list: List[XYZBaseModule],
        ctx_data: ContextData
    ) -> ContextData:
        """Sequential data_gathering execution (default, safer)"""
        logger.debug(f"        Sequential data_gathering for {len(module_list)} modules")
        for i, module in enumerate(module_list):
            module_name = module.config.name
            logger.debug(f"          [{i+1}/{len(module_list)}] {module_name}")
            try:
                with timed(f"hook.{module_name}.data_gathering", slow_threshold_ms=500):
                    ctx_data = await module.hook_data_gathering(ctx_data)
            except Exception as e:
                # Use structured exception to log the error, but continue executing other modules
                error = DataGatheringError(
                    module=module_name,
                    message="Data gathering failed",
                    cause=e,
                    agent_id=ctx_data.agent_id,
                )
                logger.exception(
                    f"Module {module_name} data gathering failed, continuing with other modules",
                    extra=error.to_dict(),
                )
        return ctx_data

    async def _parallel_data_gathering(
        self,
        module_list: List[XYZBaseModule],
        ctx_data: ContextData
    ) -> ContextData:
        """
        Parallel data_gathering execution + merge

        Each module receives an independent copy of the original ctx_data, without mutual interference.
        After all modules complete, ContextDataMerger is used to merge results.

        Merge strategy:
        - List fields (e.g., chat_history): extend
        - Dict fields (e.g., extra_data): deep merge
        - Simple fields: non-None values override
        """
        from .ctx_data_merger import ContextDataMerger

        logger.debug(f"        Parallel data_gathering for {len(module_list)} modules")

        # Execute all module hooks in parallel
        async def gather_one(module: XYZBaseModule) -> ContextData:
            """Execute a single module's data_gathering"""
            # Each module receives an independent copy, without mutual interference
            local_ctx = ctx_data.model_copy(deep=True)
            module_name = module.config.name
            with timed(f"hook.{module_name}.data_gathering", slow_threshold_ms=500):
                return await module.hook_data_gathering(local_ctx)

        results = await asyncio.gather(
            *[gather_one(module) for module in module_list],
            return_exceptions=True
        )

        # Filter errors, collect valid results
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Use structured exception to log error information
                module_name = module_list[i].config.name
                error = DataGatheringError(
                    module=module_name,
                    message="Data gathering failed",
                    cause=result,
                    agent_id=ctx_data.agent_id,
                )
                logger.error(
                    f"Module {module_name} data gathering failed",
                    extra=error.to_dict(),
                )
            else:
                valid_results.append(result)

        # Merge all results
        logger.debug(f"        Merging {len(valid_results)} results")
        return ContextDataMerger.merge(ctx_data, valid_results)

    async def hook_after_event_execution(
        self,
        module_list: List[XYZBaseModule],
        params: HookAfterExecutionParams
    ) -> List[HookCallbackResult]:
        """
        Have each Module perform post-event processing (parallel execution)

        This hook is typically used for:
        - Logging/statistics
        - Sending notifications
        - Updating external systems
        - Detecting Job completion and returning callbacks

        These operations are typically independent and can safely be executed in parallel.

        Args:
            module_list: List of modules that need to execute the hook
            params: Structured hook parameters

        Returns:
            List[HookCallbackResult]: List of all results that need to trigger callbacks
        """
        if not module_list:
            return []

        logger.debug(f"        Parallel hook_after_event_execution for {len(module_list)} modules")

        # Execute all modules' post-processing hooks in parallel
        async def execute_one(module: XYZBaseModule) -> tuple[Optional[HookCallbackResult], Optional[HookExecutionError]]:
            """Execute a single module's post-processing hook, returns (callback_result, error)"""
            module_name = module.config.name
            try:
                with timed(f"hook.{module_name}.after_event_execution", slow_threshold_ms=2000):
                    result = await module.hook_after_event_execution(params)
                return (result, None)
            except Exception as e:
                # Return structured error
                error = HookExecutionError(
                    module=module.config.name,
                    hook_name="hook_after_event_execution",
                    message="Hook execution failed",
                    cause=e,
                    agent_id=params.agent_id,
                    event_id=params.event_id,
                )
                return (None, error)

        results = await asyncio.gather(
            *[execute_one(module) for module in module_list],
            return_exceptions=True
        )

        # Collect callback results and log errors
        callback_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Unexpected exception in after_execution hook: {result}")
                continue

            callback_result, error = result

            # Log errors
            if error is not None:
                logger.warning(
                    f"Hook execution failed: {error.module}\n"
                    f"          Cause: {type(error.cause).__name__}: {error.cause}"
                )

            # Collect callback results
            if callback_result is not None and callback_result.trigger_callback:
                logger.info(f"Callback triggered by {module_list[i].config.name}")
                callback_results.append(callback_result)

        return callback_results

    async def hook_callback_results(
        self,
        hook_callback_results: List[HookCallbackResult],
        narrative: Optional["Narrative"],
        narrative_service: "NarrativeService",
        execute_callback_instance: Callable
    ) -> None:
        """
        Process callback results returned by hook_after_event_execution

        This method is responsible for:
        1. Checking dependencies and activating waiting instances
        2. Triggering background execution of newly activated instances
        3. Sending user notifications

        Args:
            hook_callback_results: Result list returned by hook_after_event_execution
            narrative: Current Narrative (optional)
            narrative_service: Narrative service
            execute_callback_instance: Function to execute callback instance
        """
        if not hook_callback_results:
            return

        logger.info(f"Processing {len(hook_callback_results)} hook callback(s)")

        for callback in hook_callback_results:
            logger.info(
                f"  → Callback: instance_id={callback.instance_id}, "
                f"status={callback.instance_status.value}"
            )
            logger.info(f"      notification={callback.notification_message}")

            if not narrative:
                logger.warning("No narrative available, skipping dependency check")
                continue

            # 1. Handle instance completion, check dependencies and activate new instances
            newly_activated = await narrative_service.handle_instance_completion(
                narrative_id=narrative.id,
                instance_id=callback.instance_id,
                new_status=callback.instance_status
            )

            logger.info(
                f"Dependencies checked, {len(newly_activated)} instances activated"
            )

            # 2. For each newly activated instance, trigger background execution
            for activated_instance_id in newly_activated:
                logger.info(
                    f"Triggering background execution for: {activated_instance_id}"
                )

                # Execute newly activated instance in background (non-blocking main flow)
                asyncio.create_task(
                    execute_callback_instance(
                        narrative_id=narrative.id,
                        instance_id=activated_instance_id,
                        trigger_data=callback.output_data
                    )
                )

                logger.info(f"      ✓ Background task created for {activated_instance_id}")

            # 3. Send user notification (if notification_message exists)
            if callback.notification_message:
                logger.info(f"      📬 User notification: {callback.notification_message}")
                # TODO: Call Inbox module to send notification

