"""
@file_name: agents_social_network.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Social Network routes

Provides endpoints for:
- GET /{agent_id}/social-network - Get all social entities for an Agent
- GET /{agent_id}/social-network/{user_id} - Get social network info for a specific user
- GET /{agent_id}/social-network/search - Search social entities (keyword/semantic)
"""

import json
from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import SocialNetworkRepository, InstanceRepository
from xyz_agent_context.schema import (
    SocialNetworkEntityInfo,
    SocialNetworkResponse,
    SocialNetworkListResponse,
    SocialNetworkSearchResponse,
)


router = APIRouter()


def _parse_json(value: Any, default: Any) -> Any:
    """
    Parse JSON field

    Handles JSON strings stored in the database, converting them to Python objects.
    Supports dict and list JSON types, also handles double-encoding cases.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            # Handle double-encoding
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except json.JSONDecodeError:
                    pass
            return parsed
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed for value: {value[:100]}... Error: {e}")
            return default
    logger.warning(f"Unexpected type for JSON field: {type(value)}, value: {value}")
    return default


@router.get("/{agent_id}/social-network/search", response_model=SocialNetworkSearchResponse)
async def search_social_network_entities(
    agent_id: str,
    query: str = Query(..., description="Search query"),
    search_type: str = Query("semantic", description="Search type: 'keyword' or 'semantic'"),
    limit: int = Query(10, description="Maximum number of results")
):
    """
    Search social entities by keyword (entity_name, entity_description, tags).
    `search_type` is accepted for API compatibility but always resolves to
    keyword search — embedding/semantic search is retired.

    NOTE: This route MUST be registered before /{user_id} to avoid path shadowing.
    """
    logger.info(f"Searching social network entities: agent={agent_id}, query='{query}', type={search_type}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkSearchResponse(
                success=True, entities=[], count=0, search_type=search_type
            )

        instance_id = instances[0].instance_id
        social_repo = SocialNetworkRepository(db_client)

        # Embeddings retired — entity search is BM25-style keyword search only.
        results = await social_repo.keyword_search(
            instance_id=instance_id,
            keyword=query,
            limit=limit
        )
        entity_list = []
        for entity in results:
            entity_info = SocialNetworkEntityInfo(
                entity_id=entity.entity_id,
                entity_name=entity.entity_name,
                aliases=entity.aliases or [],
                entity_description=entity.entity_description,
                entity_type=entity.entity_type,
                familiarity=entity.familiarity or "known_of",
                identity_info=entity.identity_info or {},
                contact_info=entity.contact_info or {},
                tags=entity.keywords or [],
                keywords=entity.keywords or [],
                relationship_strength=entity.relationship_strength or 0.0,
                interaction_count=entity.interaction_count or 0,
                last_interaction_time=format_for_api(entity.last_interaction_time),
                persona=entity.persona,
                related_job_ids=entity.related_job_ids or [],
                expertise_domains=entity.expertise_domains or [],
            )
            entity_list.append(entity_info)

        return SocialNetworkSearchResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list),
            search_type=search_type
        )

    except Exception as e:
        logger.exception(f"Error searching social network entities: {e}")
        return SocialNetworkSearchResponse(
            success=False, error=str(e), search_type=search_type
        )


def _entity_to_info(e) -> SocialNetworkEntityInfo:
    """Map a SocialNetworkEntity (from the unified-memory-backed repo) to the
    API's SocialNetworkEntityInfo. `tags` and `keywords` are the same list."""
    return SocialNetworkEntityInfo(
        entity_id=e.entity_id,
        entity_name=e.entity_name,
        aliases=e.aliases,
        entity_description=e.entity_description,
        entity_type=e.entity_type,
        familiarity=e.familiarity or "known_of",
        identity_info=e.identity_info,
        contact_info=e.contact_info,
        tags=e.keywords,
        keywords=e.keywords,
        relationship_strength=e.relationship_strength,
        interaction_count=e.interaction_count,
        last_interaction_time=format_for_api(e.last_interaction_time),
        persona=e.persona,
        related_job_ids=e.related_job_ids,
        expertise_domains=e.expertise_domains,
    )


@router.get("/{agent_id}/social-network/{user_id}", response_model=SocialNetworkResponse)
async def get_user_social_network_info(agent_id: str, user_id: str):
    """
    Get a user's information in the Agent's social network

    Queries data from instance_social_entities table (via SocialNetworkModule's instance_id).
    """
    logger.info(f"Getting social network info for user: {user_id}, agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkResponse(
                success=False,
                error=f"No SocialNetworkModule instance found for agent: {agent_id}"
            )

        instance_id = instances[0].instance_id

        entity = await SocialNetworkRepository(db_client).get_entity(user_id, instance_id)
        if entity:
            return SocialNetworkResponse(success=True, entity=_entity_to_info(entity))
        else:
            return SocialNetworkResponse(
                success=False,
                error=f"No social network info found for user: {user_id}"
            )

    except Exception as e:
        logger.exception(f"Error getting social network info: {e}")
        return SocialNetworkResponse(success=False, error=str(e))


@router.get("/{agent_id}/social-network", response_model=SocialNetworkListResponse)
async def get_all_social_network_entities(agent_id: str):
    """
    Get all social entities for an Agent

    Queries data from instance_social_entities table (via SocialNetworkModule's instance_id).
    """
    logger.debug(f"Getting all social network entities for agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkListResponse(success=True, entities=[], count=0)

        instance_id = instances[0].instance_id

        entities = await SocialNetworkRepository(db_client).get_all_entities(instance_id, limit=1000)
        entity_list = [_entity_to_info(e) for e in entities]

        return SocialNetworkListResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list)
        )

    except Exception as e:
        logger.exception(f"Error getting social network entities: {e}")
        return SocialNetworkListResponse(success=False, error=str(e))


