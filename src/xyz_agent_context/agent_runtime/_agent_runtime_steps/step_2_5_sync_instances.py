"""
@file_name: step_2_5_sync_instances.py
@author: NetMind.AI
@date: 2025-12-24
@description: Step 2.5 - Sync Instance changes

Merged the original step_2_5 and step_2_6:
- Update Markdown (record Instances and relationship graph)
- Sync Instance changes to database (establish/remove associations)
"""

from __future__ import annotations

from typing import AsyncGenerator, List, Set, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ProgressMessage, ProgressStatus

if TYPE_CHECKING:
    from .context import RunContext
    from xyz_agent_context.narrative import NarrativeService, NarrativeMarkdownManager
    from xyz_agent_context.schema.module_schema import ModuleInstance


@timed("step.2_5_sync_instances")

async def step_2_5_sync_instances(
    ctx: "RunContext",
    narrative_service: "NarrativeService",
    markdown_manager: "NarrativeMarkdownManager"
) -> AsyncGenerator[ProgressMessage, None]:
    """
    Step 2.5: Sync Instance changes

    Sync Instance Decision results to Markdown and database.

    Flow:
    1. Update Markdown (Instances and relationship graph)
    2. Sync Instance changes to database
       - Added Instances: establish instance_narrative_links associations
       - Removed Instances: remove associations (mark as history)
       - Updated Instances: update status
    3. Update narrative.active_instances runtime cache

    Args:
        ctx: Run context
        narrative_service: Narrative service
        markdown_manager: Markdown manager

    Yields:
        ProgressMessage: Progress messages
    """
    yield ProgressMessage(
        step="2.5",
        title="Sync Instance Changes",
        description="Update Markdown and sync Instances to database",
        status=ProgressStatus.RUNNING,
        substeps=ctx.substeps_2_5
    )

    main_narrative = ctx.main_narrative
    load_result = ctx.load_result

    # =========================================================================
    # 2.5.1 Update Markdown
    # =========================================================================
    if main_narrative and hasattr(load_result, 'active_instances') and load_result.active_instances:
        await markdown_manager.update_instances(
            narrative=main_narrative,
            instances=load_result.active_instances,
            relationship_graph=load_result.relationship_graph,
            changes_summary=load_result.changes_summary
        )

        ctx.substeps_2_5.append("[2.5.1] ✓ Markdown updated")
        logger.debug("markdown updated")
    else:
        ctx.substeps_2_5.append("[2.5.1] ✖ No Narrative, skipping Markdown update")

    # =========================================================================
    # 2.5.2 Sync Instances to database
    # =========================================================================
    if not main_narrative or not load_result or not load_result.active_instances:
        ctx.substeps_2_5.append("[2.5.2] ✖ No Instance, skipping database sync")
        yield ProgressMessage(
            step="2.5",
            title="Sync Instance Changes",
            description="✓ Complete (no database sync needed)",
            status=ProgressStatus.COMPLETED,
            substeps=ctx.substeps_2_5
        )
        return

    # Get database client and Repository
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.repository import InstanceRepository, InstanceNarrativeLinkRepository
    from xyz_agent_context.schema.instance_schema import LinkType

    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)
    link_repo = InstanceNarrativeLinkRepository(db_client)

    narrative_id = main_narrative.id
    new_instances: List["ModuleInstance"] = load_result.active_instances
    new_instance_ids: Set[str] = {inst.instance_id for inst in new_instances}

    # Get currently linked instance_ids (from database)
    current_linked_ids: Set[str] = set(
        await link_repo.get_instances_for_narrative(narrative_id, link_type=LinkType.ACTIVE)
    )

    # Calculate changes
    added_ids = new_instance_ids - current_linked_ids
    removed_ids = current_linked_ids - new_instance_ids
    kept_ids = new_instance_ids & current_linked_ids

    logger.debug(
        f"Instance changes: added={len(added_ids)}, removed={len(removed_ids)}, kept={len(kept_ids)}"
    )

    # Handle added Instances
    if added_ids:
        from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus

        # Build instance_id -> raw_instance mapping for checking JobModule job_config
        raw_instance_map = {}
        if hasattr(load_result, 'raw_instances') and load_result.raw_instances:
            for raw_inst in load_result.raw_instances:
                resolved_id = load_result.key_to_id.get(raw_inst.task_key, raw_inst.instance_id)
                raw_instance_map[resolved_id] = raw_inst

        # 1. First create ModuleInstance records (if they don't exist)
        created_count = 0
        skipped_job_count = 0
        for inst in new_instances:
            if inst.instance_id in added_ids:
                # Check if it already exists
                existing = await instance_repo.get_by_instance_id(inst.instance_id)
                if not existing:
                    # [Fix] JobModule must have job_config to create ModuleInstance
                    # Otherwise it produces orphan instances (ModuleInstance exists but no Job record)
                    if inst.module_class == "JobModule":
                        raw_inst = raw_instance_map.get(inst.instance_id)
                        if not raw_inst or not raw_inst.job_config:
                            logger.warning(
                                f"  Skipping JobModule {inst.instance_id}: missing job_config, "
                                f"not creating ModuleInstance to avoid orphan instance"
                            )
                            skipped_job_count += 1
                            # Remove from added_ids to avoid creating link later
                            added_ids.discard(inst.instance_id)
                            continue

                    # Create ModuleInstance record
                    status_enum = inst.status if isinstance(inst.status, InstanceStatus) else InstanceStatus(inst.status)
                    instance_record = ModuleInstanceRecord(
                        instance_id=inst.instance_id,
                        module_class=inst.module_class,
                        agent_id=ctx.agent_id,
                        user_id=ctx.user_id,
                        is_public=False,
                        status=status_enum,
                        description=inst.description,
                        dependencies=inst.dependencies or [],
                        config={},
                        topic_hint=inst.description[:100] if inst.description else "",
                    )
                    await instance_repo.create_instance(instance_record)
                    created_count += 1
                    logger.debug(f"  Created ModuleInstance: {inst.instance_id} ({inst.module_class})")

        if created_count > 0:
            ctx.substeps_2_5.append(f"[2.5.2] ✓ Created {created_count} ModuleInstances")
            logger.info(f"Created {created_count} ModuleInstance records")
        if skipped_job_count > 0:
            ctx.substeps_2_5.append(f"[2.5.2] ⚠ Skipped {skipped_job_count} JobModules missing job_config")
            logger.warning(f"Skipped {skipped_job_count} JobModules missing job_config (avoiding orphan instances)")

        # 2. Establish Instance-Narrative associations
        added_count = await link_repo.link_multiple(
            list(added_ids),
            narrative_id,
            link_type=LinkType.ACTIVE
        )
        ctx.substeps_2_5.append(f"[2.5.2] ✓ Added {added_count} associations")
        logger.info(f"Established {added_count} new Instance-Narrative associations")

    # Handle removed Instances (remove associations)
    # Important: only mark completed/failed/cancelled instances as history
    # In-progress instances (blocked, active, running, pending) should remain active so ModulePoller can monitor them
    if removed_ids:
        from xyz_agent_context.schema.instance_schema import InstanceStatus

        # Terminal status list
        terminal_statuses = {
            InstanceStatus.COMPLETED.value,
            InstanceStatus.FAILED.value,
            InstanceStatus.CANCELLED.value,
            "completed",
            "failed",
            "cancelled",
        }

        unlinked_count = 0
        skipped_count = 0

        for inst_id in removed_ids:
            # Get the instance's current status
            db_instance = await instance_repo.get_by_instance_id(inst_id)
            if db_instance:
                current_status = db_instance.status
                # Only terminal-state instances are marked as history
                if current_status in terminal_statuses:
                    await link_repo.unlink(inst_id, narrative_id, to_history=True)
                    unlinked_count += 1
                    logger.debug(f"  Instance {inst_id} completed, marked as history")
                else:
                    # In-progress instance remains active (no action taken)
                    skipped_count += 1
                    logger.debug(
                        f"  Instance {inst_id} still in progress (status={current_status}), keeping active"
                    )
            else:
                # If the instance record is not found, also mark as history
                await link_repo.unlink(inst_id, narrative_id, to_history=True)
                unlinked_count += 1

        if unlinked_count > 0:
            ctx.substeps_2_5.append(f"[2.5.2] ✓ Removed {unlinked_count} completed associations")
            logger.info(f"Removed {unlinked_count} completed Instance-Narrative associations")
        if skipped_count > 0:
            ctx.substeps_2_5.append(f"[2.5.2] ✓ Kept {skipped_count} in-progress associations")
            logger.info(f"Kept {skipped_count} in-progress Instance-Narrative associations (waiting for ModulePoller)")

    # Update Instance status (if changed)
    updated_count = 0
    for inst in new_instances:
        if inst.instance_id in kept_ids:
            db_instance = await instance_repo.get_by_instance_id(inst.instance_id)
            if db_instance:
                current_status = db_instance.status
                new_status = inst.status.value if hasattr(inst.status, 'value') else inst.status
                if current_status != new_status:
                    await instance_repo.update_status(inst.instance_id, inst.status)
                    updated_count += 1

    if updated_count > 0:
        ctx.substeps_2_5.append(f"[2.5.2] ✓ Updated {updated_count} statuses")
        logger.info(f"Updated {updated_count} Instance statuses")

    # Update runtime cache
    main_narrative.active_instances = new_instances

    # Save Narrative
    await narrative_service.save_narrative_to_db(main_narrative)

    logger.info(
        f"Instance sync complete: +{len(added_ids)} -{len(removed_ids)} ~{updated_count}, "
        f"total {len(new_instances)}"
    )

    # =========================================================================
    # 2.5.3 Create Job records for JobModule (Complex Job support)
    # =========================================================================
    created_job_ids = []

    # Check if raw_instances exist
    has_raw_instances = hasattr(load_result, 'raw_instances')
    raw_instances_count = len(load_result.raw_instances) if has_raw_instances else 0
    logger.debug(
        f"step_2_5.3 raw_instances has_attr={has_raw_instances} count={raw_instances_count}"
    )

    if has_raw_instances and load_result.raw_instances:
        # Only create Jobs for newly added JobModule Instances
        from xyz_agent_context.services import InstanceSyncService

        logger.debug(f"step_2_5 added_ids={added_ids}")
        logger.debug(f"step_2_5 key_to_id={load_result.key_to_id}")
        logger.debug(f"step_2_5 raw_instances_count={len(load_result.raw_instances)}")

        for inst in load_result.raw_instances:
            logger.debug(
                f"step_2_5 instance module_class={inst.module_class} "
                f"task_key='{inst.task_key}' instance_id='{inst.instance_id}'"
            )
            if inst.module_class == "JobModule":
                resolved_id = load_result.key_to_id.get(inst.task_key, inst.instance_id)
                job_config_info = None
                if inst.job_config:
                    job_config_info = {
                        "title": inst.job_config.title,
                        "scheduled_at": inst.job_config.scheduled_at,
                        "cron": getattr(inst.job_config, 'cron', None),
                        "payload": inst.job_config.payload[:50] if inst.job_config.payload else None
                    }
                logger.debug(
                    f"step_2_5 JobModule details resolved_id={resolved_id} "
                    f"in_added_ids={resolved_id in added_ids} "
                    f"job_config={job_config_info}"
                )

        job_instances = [
            inst for inst in load_result.raw_instances
            if inst.module_class == "JobModule"
            and inst.job_config is not None
            and load_result.key_to_id.get(inst.task_key, inst.instance_id) in added_ids
        ]

        logger.debug(f"step_2_5 qualifying_jobmodule_count={len(job_instances)}")

        if job_instances:
            sync_service = InstanceSyncService(db_client)
            created_job_ids = await sync_service.create_jobs_for_instances(
                instances=job_instances,
                agent_id=ctx.agent_id,
                user_id=ctx.user_id,
                key_to_id=load_result.key_to_id,
                narrative_id=narrative_id  # Feature 3.1: Associate Narrative context
            )
            if created_job_ids:
                ctx.substeps_2_5.append(f"[2.5.3] ✓ Created {len(created_job_ids)} Job records")
                logger.info(f"Created {len(created_job_ids)} Job records: {created_job_ids}")
                # Save to RunContext for subsequent Context Runtime use
                ctx.created_job_ids = created_job_ids

    # Build Markdown update details
    markdown_update_details = {}
    if main_narrative and load_result and hasattr(load_result, 'active_instances'):
        markdown_update_details = {
            "instances_updated": [
                {
                    "instance_id": inst.instance_id,
                    "module_class": inst.module_class,
                    "status": inst.status.value if hasattr(inst.status, 'value') else inst.status
                }
                for inst in load_result.active_instances
            ],
            "relationship_graph": load_result.relationship_graph if hasattr(load_result, 'relationship_graph') else "",
            "changes_summary": load_result.changes_summary if hasattr(load_result, 'changes_summary') else {},
        }

    yield ProgressMessage(
        step="2.5",
        title="Sync Instance Changes",
        description=f"✓ Sync complete: {len(new_instances)} active Instances",
        status=ProgressStatus.COMPLETED,
        details={
            "added": len(added_ids),
            "removed": len(removed_ids),
            "updated": updated_count,
            "total_active": len(new_instances),
            # Markdown update details
            "markdown_update": markdown_update_details,
            # Complex Job support
            "created_job_ids": created_job_ids,
        },
        substeps=ctx.substeps_2_5
    )
