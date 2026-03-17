"""
@file_name: _job_lifecycle.py
@author: NetMind.AI
@date: 2026-03-06
@description: Job post-execution lifecycle processing

Extracted from JobModule to separate concerns:
- handle_job_execution_result: LLM-powered analysis after JOB-triggered execution
- update_ongoing_jobs_from_chat: ONGOING job progress tracking on CHAT trigger
"""

import json
from typing import Optional, List, Dict, Any

from loguru import logger

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from xyz_agent_context.schema import ContextData, WorkingSource
from xyz_agent_context.schema.module_schema import (
    HookCallbackResult,
    InstanceStatus,
)
from xyz_agent_context.schema.hook_schema import HookAfterExecutionParams
from xyz_agent_context.schema.job_schema import (
    JobType,
    JobStatus,
    JobModel,
    JobExecutionResult,
    OngoingExecutionResult,
)
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.utils import utc_now

from xyz_agent_context.module.job_module._job_analysis import (
    extract_execution_trace,
    build_job_analysis_prompt,
)
from xyz_agent_context.module.job_module.prompts import ONGOING_CHAT_ANALYSIS_PROMPT


async def handle_job_execution_result(
    params: HookAfterExecutionParams,
    repo: JobRepository,
    get_job_by_instance_id,
) -> Optional[HookCallbackResult]:
    """
    Process JOB-triggered execution results using LLM analysis.

    Flow:
    1. Collect execution info (trace, context, output)
    2. Get Job info via instance_id
    3. Build analysis prompt (type-dependent guidance)
    4. Call LLM to analyze and determine status
    5. Update job fields in database
    6. Return HookCallbackResult if terminal state (for dependency chain)

    Args:
        params: Hook execution parameters
        repo: JobRepository for DB operations
        get_job_by_instance_id: Async callable to get job by instance_id

    Returns:
        HookCallbackResult if one_off job completed/failed, else None
    """
    instance = params.instance
    if not instance:
        logger.warning("            No instance available, cannot trigger callback")

    # Collect execution info
    final_output = params.final_output
    ctx_data = params.ctx_data
    agent_loop_response = params.agent_loop_response

    execution_trace = extract_execution_trace(agent_loop_response)

    # Get Job info via instance_id
    current_time = utc_now()
    input_content = params.input_content
    job_info = await _get_job_info_for_analysis(instance, get_job_by_instance_id)

    # Build LLM analysis prompt
    prompts = build_job_analysis_prompt(
        current_time=current_time,
        input_content=input_content,
        job_info=job_info,
        execution_trace=execution_trace,
        final_output=final_output,
        ctx_data=ctx_data,
    )

    # Call LLM to analyze execution results
    llm_result: JobExecutionResult = await OpenAIAgentsSDK().llm_function(
        instructions=prompts,
        user_input="Please analysis it!",
        output_type=JobExecutionResult,
    )

    if not llm_result or not llm_result.final_output:
        logger.warning("            LLM returned empty result, skipping job update")
        return None

    result = llm_result.final_output
    logger.info(f"            LLM analysis result: \n\t{json.dumps(result.model_dump(mode='json'), indent=4)}")

    # Update database
    job_id = result.job_id
    if job_id:
        logger.info(f"            Updating job: {job_id}, status={result.status}, next_run={result.next_run_time}")
        try:
            existing_job = await repo.get_job(job_id)
            existing_process = existing_job.process if existing_job and existing_job.process else []

            now = utc_now()
            updates = {
                "status": result.status.value,
                "process": existing_process + result.process,
                "last_run_time": now,
                "last_error": result.last_error if result.status == JobStatus.FAILED else None,
                "updated_at": now,
                "next_run_time": result.next_run_time,
            }
            await repo.update_job(job_id, updates)
            logger.info(f"            Job {job_id} updated: status={result.status.value}")

            if result.should_notify:
                logger.info(f"            Should notify user: {result.notification_summary}")
                # TODO: Call Inbox module to send notification

        except Exception as e:
            logger.error(f"            Failed to update job {job_id}: {e}")

    # Return callback for terminal states (dependency chain trigger)
    is_terminal = result.status in [JobStatus.COMPLETED, JobStatus.FAILED]
    if is_terminal and instance:
        instance_status = (
            InstanceStatus.COMPLETED if result.status == JobStatus.COMPLETED
            else InstanceStatus.FAILED
        )
        callback_result = HookCallbackResult(
            instance_id=instance.instance_id,
            trigger_callback=True,
            instance_status=instance_status,
            output_data={
                "job_id": result.job_id,
                "status": result.status.value,
                "process": result.process,
                "notification_summary": result.notification_summary,
            },
            notification_message=result.notification_summary if result.should_notify else None
        )
        logger.info(
            f"            Job terminal state, returning callback: "
            f"instance_id={instance.instance_id}, status={instance_status.value}"
        )
        return callback_result

    if not instance:
        logger.warning("            Job completed but no instance to trigger callback")
    else:
        logger.info(f"            Scheduled job, no callback (status={result.status.value})")
    return None


async def update_ongoing_jobs_from_chat(
    active_job_instance_ids: List[str],
    chat_content: str,
    ctx_data: ContextData,
    agent_id: str,
    repo: JobRepository,
    get_job_by_instance_id,
) -> None:
    """
    Update related ONGOING Jobs when triggered by CHAT.

    When a user chats with the Agent, if the current Narrative has active ONGOING Jobs,
    check whether the conversation satisfies the Job's end condition.

    Args:
        active_job_instance_ids: Currently active JobModule instance IDs
        chat_content: Agent's final output
        ctx_data: Complete context data
        agent_id: Agent ID
        repo: JobRepository for DB operations
        get_job_by_instance_id: Async callable to get job by instance_id
    """
    if not active_job_instance_ids:
        return

    logger.info(f"          Checking {len(active_job_instance_ids)} active job instances for ONGOING updates")

    for instance_id in active_job_instance_ids:
        try:
            # Get Job object
            job = await get_job_by_instance_id(agent_id, instance_id)
            if not job:
                logger.debug(f"            Instance {instance_id}: No job found, skipping")
                continue

            # Only process Jobs where current user is the target user
            current_user_id = ctx_data.user_id if ctx_data else None
            job_target_user = job.related_entity_id
            if job_target_user and current_user_id and job_target_user != current_user_id:
                logger.info(
                    f"            Job {job.job_id}: skipping - target user({job_target_user}) != "
                    f"current user({current_user_id})"
                )
                continue

            # Filter: only process ONGOING type with ACTIVE or RUNNING status
            if job.job_type != JobType.ONGOING:
                logger.debug(f"            Job {job.job_id}: Not ONGOING type ({job.job_type}), skipping")
                continue

            valid_statuses = {JobStatus.ACTIVE, JobStatus.RUNNING}
            if job.status not in valid_statuses:
                logger.debug(f"            Job {job.job_id}: Status {job.status} not in {valid_statuses}, skipping")
                continue

            # Get end_condition
            end_condition = None
            if job.trigger_config:
                end_condition = job.trigger_config.end_condition
            if not end_condition:
                logger.debug(f"            Job {job.job_id}: No end_condition defined, skipping")
                continue

            logger.info(f"            Analyzing ONGOING Job {job.job_id} against chat interaction")

            # Build LLM analysis prompt
            current_time = utc_now()
            user_query = ctx_data.input_content if ctx_data else ""
            max_iter = job.trigger_config.max_iterations if job.trigger_config else None

            prompt = ONGOING_CHAT_ANALYSIS_PROMPT.format(
                job_id=job.job_id,
                title=job.title,
                description=job.description,
                payload_preview=job.payload[:500] if job.payload else "N/A",
                end_condition=end_condition,
                iteration_count=job.iteration_count,
                max_iterations=max_iter or "No limit",
                user_query=user_query,
                chat_content_preview=chat_content[:1000] if chat_content else "N/A",
            )

            # Call LLM analysis
            llm_result = await OpenAIAgentsSDK().llm_function(
                instructions=prompt,
                user_input="Please analyze this interaction.",
                output_type=OngoingExecutionResult,
            )

            if not llm_result or not llm_result.final_output:
                logger.warning(f"            LLM returned empty result for job {job.job_id}")
                continue

            result = llm_result.final_output
            logger.info(
                f"            LLM analysis: is_end_condition_met={result.is_end_condition_met}, "
                f"should_continue={result.should_continue}"
            )

            # Update Job status
            updates: Dict[str, Any] = {"updated_at": current_time}

            existing_process = job.process if job.process else []
            if result.process:
                updates["process"] = existing_process + result.process

            if result.is_end_condition_met or not result.should_continue:
                updates["status"] = JobStatus.COMPLETED.value
                logger.info(f"            Job {job.job_id} completed: {result.end_condition_reason}")
            else:
                updates["iteration_count"] = job.iteration_count + 1
                if max_iter and updates["iteration_count"] >= max_iter:
                    updates["status"] = JobStatus.COMPLETED.value
                    logger.info(f"            Job {job.job_id} completed: max iterations reached")

            await repo.update_job(job.job_id, updates)
            logger.info(f"            Job {job.job_id} updated: {list(updates.keys())}")

        except Exception as e:
            logger.error(f"            Error processing job instance {instance_id}: {e}")
            continue


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _get_job_info_for_analysis(instance, get_job_by_instance_id) -> Dict[str, Any]:
    """
    Get complete Job information for LLM analysis via instance_id.

    Returns:
        Dictionary containing job_id, job_type, title, description,
        trigger_config, iteration_count, process, status.
    """
    if not instance or not instance.instance_id:
        return {}

    try:
        job = await get_job_by_instance_id(instance)
        if not job:
            return {}

        trigger_info = {}
        if job.trigger_config:
            trigger_info = {
                "end_condition": job.trigger_config.end_condition,
                "interval_seconds": job.trigger_config.interval_seconds,
                "max_iterations": job.trigger_config.max_iterations,
                "cron": job.trigger_config.cron,
            }

        return {
            "job_id": job.job_id,
            "job_type": job.job_type.value if job.job_type else None,
            "title": job.title,
            "description": job.description,
            "payload": job.payload[:500] if job.payload else None,
            "trigger_config": trigger_info,
            "iteration_count": job.iteration_count or 0,
            "process": job.process or [],
            "status": job.status.value if job.status else None,
        }
    except Exception as e:
        logger.error(f"Failed to get job info for analysis: {e}")
        return {}
