"""
@file_name: _job_context_builder.py
@author: Bin Liang
@date: 2026-03-06
@description: Context building for Job execution prompts

Extracted from job_trigger.py. Handles loading dependency outputs,
social network context, narrative summaries, and assembling the
final execution prompt for AgentRuntime.
"""

import json
from typing import List, Dict, Any

from loguru import logger

from xyz_agent_context.schema.job_schema import JobModel
from xyz_agent_context.utils import DatabaseClient
from xyz_agent_context.module.job_module.prompts import (
    JOB_TASK_INFO_TEMPLATE,
    JOB_ENTITIES_SECTION_TEMPLATE,
    JOB_PROGRESS_SECTION_TEMPLATE,
    JOB_DEPENDENCIES_SECTION_TEMPLATE,
    JOB_EXECUTION_PROMPT_TEMPLATE,
)


async def get_dependency_outputs(
    db: DatabaseClient,
    instance_id: str,
) -> List[Dict[str, Any]]:
    """
    Get execution outputs of dependency Jobs.

    Queries module_instances.dependencies via instance_id,
    then retrieves execution results of each dependency Job.

    Args:
        db: Database client
        instance_id: Current Job's instance_id

    Returns:
        List of dependency Job outputs, each element contains:
        - instance_id: Dependency's instance_id
        - title: Job title
        - output: Complete execution output
        - status: Execution status
    """
    outputs: List[Dict[str, Any]] = []

    try:
        # 1. Get dependency list of current instance
        query = """
            SELECT dependencies FROM module_instances WHERE instance_id = %s
        """
        rows = await db.execute(query, (instance_id,), fetch=True)
        if not rows or not rows[0].get('dependencies'):
            return outputs

        deps_raw = rows[0]['dependencies']
        if isinstance(deps_raw, str):
            dep_ids = json.loads(deps_raw)
        else:
            dep_ids = deps_raw

        if not dep_ids:
            return outputs

        logger.debug(f"Found {len(dep_ids)} dependencies for {instance_id}: {dep_ids}")

        # 2. Get execution output of each dependency
        for dep_id in dep_ids:
            try:
                # 2.1 Get dependency Job info and process (event_ids)
                query = """
                    SELECT ij.title, ij.status, ij.process, mi.status as instance_status
                    FROM instance_jobs ij
                    LEFT JOIN module_instances mi ON ij.instance_id = mi.instance_id
                    WHERE ij.instance_id = %s
                """
                job_rows = await db.execute(query, (dep_id,), fetch=True)
                if not job_rows:
                    logger.warning(f"Dependency job not found: {dep_id}")
                    continue

                job_row = job_rows[0]
                process_raw = job_row.get('process')

                # 2.2 Get event_id from process
                event_ids = []
                if process_raw:
                    if isinstance(process_raw, str):
                        event_ids = json.loads(process_raw)
                    else:
                        event_ids = process_raw

                # 2.3 Get latest event output
                output_text = ""
                if event_ids:
                    latest_event_id = event_ids[-1]
                    event_query = """
                        SELECT final_output FROM events WHERE event_id = %s
                    """
                    event_rows = await db.execute(event_query, (latest_event_id,), fetch=True)
                    if event_rows and event_rows[0].get('final_output'):
                        output_text = event_rows[0]['final_output']

                outputs.append({
                    'instance_id': dep_id,
                    'title': job_row.get('title', dep_id),
                    'status': job_row.get('instance_status', job_row.get('status', 'unknown')),
                    'output': output_text,
                })

            except Exception as e:
                logger.error(f"Error fetching dependency output for {dep_id}: {e}")
                outputs.append({
                    'instance_id': dep_id,
                    'title': dep_id,
                    'status': 'error',
                    'output': f"[Failed to get output: {str(e)}]",
                })

    except Exception as e:
        logger.error(f"Error getting dependency outputs: {e}")

    return outputs


async def load_social_network_context(
    db: DatabaseClient,
    entity_ids: List[str],
    agent_id: str,
) -> List[Dict[str, Any]]:
    """
    Load Social Network context (Feature 3.1 Enhancement).

    Loads detailed Entity information for the given entity_ids for Job execution.

    Args:
        db: Database client
        entity_ids: List of Entity IDs
        agent_id: Agent ID (for querying SocialNetworkModule's instance_id)

    Returns:
        List of Entity information dicts
    """
    if not entity_ids:
        return []

    try:
        from xyz_agent_context.repository import (
            SocialNetworkRepository,
            InstanceRepository,
        )

        # 1. Get SocialNetworkModule's instance_id
        instance_repo = InstanceRepository(db)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule",
        )

        if not instances:
            logger.warning(f"No SocialNetworkModule found for agent {agent_id}")
            return []

        social_instance_id = instances[0].instance_id
        logger.debug(f"Found SocialNetworkModule instance: {social_instance_id}")

        # 2. Query detailed information for each Entity
        social_repo = SocialNetworkRepository(db)
        entities_info: List[Dict[str, Any]] = []

        for entity_id in entity_ids:
            try:
                entity = await social_repo.get_entity(entity_id, social_instance_id)
                if entity:
                    # Truncate overly long fields (avoid prompt being too long)
                    description = entity.entity_description[:500] if entity.entity_description else ""

                    # Extract persona from identity_info (if available)
                    persona = None
                    if entity.identity_info and isinstance(entity.identity_info, dict):
                        persona_raw = entity.identity_info.get('persona', '')
                        if persona_raw:
                            persona = str(persona_raw)[:300]

                    entities_info.append({
                        "entity_id": entity.entity_id,
                        "entity_name": entity.entity_name,
                        "entity_type": entity.entity_type,
                        "description": description,
                        "tags": entity.tags[:10],
                        "persona": persona,
                        "expertise_domains": entity.expertise_domains[:5] if entity.expertise_domains else [],
                    })
                    logger.debug(f"Loaded entity: {entity.entity_name} ({entity_id})")
                else:
                    logger.warning(f"Entity {entity_id} not found")
            except Exception as e:
                logger.error(f"Failed to load entity {entity_id}: {e}")

        logger.info(f"Loaded {len(entities_info)} entities for job context")
        return entities_info

    except Exception as e:
        logger.error(f"Failed to load social network context: {e}")
        return []


async def load_narrative_summary(
    db: DatabaseClient,
    narrative_id: str,
) -> str:
    """
    Load Narrative summary (Feature 3.1 Enhancement).

    Gets the Narrative's current_summary field for understanding
    overall progress during Job execution.

    Args:
        db: Database client
        narrative_id: Narrative ID

    Returns:
        Narrative summary string (truncated to 800 characters)
    """
    if not narrative_id:
        return ""

    try:
        from xyz_agent_context.repository import NarrativeRepository

        narrative_repo = NarrativeRepository(db)
        narrative = await narrative_repo.get_by_id(narrative_id)

        if not narrative:
            logger.warning(f"Narrative {narrative_id} not found")
            return ""

        if narrative.narrative_info and narrative.narrative_info.current_summary:
            summary = narrative.narrative_info.current_summary
            truncated_summary = summary[:800] if len(summary) > 800 else summary
            logger.info(f"Loaded narrative summary for {narrative_id} (length: {len(truncated_summary)})")
            return truncated_summary
        else:
            logger.debug(f"Narrative {narrative_id} has no current_summary")
            return ""

    except Exception as e:
        logger.error(f"Failed to load narrative summary for {narrative_id}: {e}")
        return ""


async def build_execution_prompt(
    db: DatabaseClient,
    job: JobModel,
    user_timezone: str,
) -> str:
    """
    Build execution prompt for AgentRuntime (Feature 3.1 Enhanced).

    Combines job metadata with the payload, social network context,
    narrative summary, and dependency outputs to create a complete
    prompt for the Agent to execute.

    Args:
        db: Database client
        job: JobModel instance
        user_timezone: User's timezone string

    Returns:
        Complete execution prompt string with enriched context
    """
    from xyz_agent_context.utils.timezone import format_for_llm, utc_now

    current_time_str = format_for_llm(utc_now(), user_timezone)
    created_str = format_for_llm(job.created_at, user_timezone) if job.created_at else "Unknown"

    # ===== Load all context (Feature 3.1) =====

    # 1. Load Social Network context (single target user)
    entities_info: List[Dict[str, Any]] = []
    if job.related_entity_id:
        entities_info = await load_social_network_context(
            db=db,
            entity_ids=[job.related_entity_id],
            agent_id=job.agent_id,
        )

    # 2. Load Narrative Summary
    narrative_summary = ""
    if job.narrative_id:
        narrative_summary = await load_narrative_summary(
            db=db,
            narrative_id=job.narrative_id,
        )

    # 3. Load dependency Job outputs
    dep_outputs: List[Dict[str, Any]] = []
    if job.instance_id:
        dep_outputs = await get_dependency_outputs(db=db, instance_id=job.instance_id)

    # ===== Build Prompt sections =====

    # Section: Task information
    execution_user_id = job.related_entity_id or job.user_id
    task_info_section = JOB_TASK_INFO_TEMPLATE.format(
        title=job.title,
        description=job.description,
        created_str=created_str,
        current_time_str=current_time_str,
        execution_user_id=execution_user_id,
        user_id=job.user_id,
    )

    # Section: Related people/entities
    entities_section = ""
    if entities_info:
        entity_lines = []
        for entity in entities_info:
            entity_line = f"- **{entity['entity_name']}** ({entity['entity_type']})"
            if entity.get('description'):
                entity_line += f"\n  - Description: {entity['description']}"
            if entity.get('tags'):
                entity_line += f"\n  - Tags: {', '.join(entity['tags'])}"
            if entity.get('persona'):
                entity_line += f"\n  - Persona: {entity['persona']}"
            entity_lines.append(entity_line)

        entities_section = JOB_ENTITIES_SECTION_TEMPLATE.format(
            entity_lines=chr(10).join(entity_lines),
        )
        logger.info(f"Added {len(entities_info)} entities to prompt")

    # Section: Current progress
    narrative_section = ""
    if narrative_summary:
        narrative_section = JOB_PROGRESS_SECTION_TEMPLATE.format(
            narrative_summary=narrative_summary,
        )
        logger.info("Added narrative summary to prompt")

    # Section: Prerequisite task results
    dependency_section = ""
    if dep_outputs:
        dep_parts = []
        for dep in dep_outputs:
            dep_part = f"""### {dep['title']} (`{dep['instance_id']}`)
**Status**: {dep['status']}

**Execution Output**:
{dep['output'] if dep['output'] else '*This task has no output content*'}
"""
            dep_parts.append(dep_part)

        dependency_section = JOB_DEPENDENCIES_SECTION_TEMPLATE.format(
            dep_parts=chr(10).join(dep_parts),
        )
        logger.info(f"Added {len(dep_outputs)} dependency outputs to prompt")

    # ===== Assemble complete Prompt =====
    extra_requirement = ""
    if dep_outputs or entities_info or narrative_summary:
        extra_requirement = "6. Make full use of prerequisite task results and context information, do not repeat already completed work"

    prompt = JOB_EXECUTION_PROMPT_TEMPLATE.format(
        task_info_section=task_info_section,
        entities_section=entities_section,
        narrative_section=narrative_section,
        dependency_section=dependency_section,
        payload=job.payload,
        related_entity_id=job.related_entity_id,
        extra_requirement=extra_requirement,
    )
    return prompt
