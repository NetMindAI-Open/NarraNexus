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
    Search social entities (supports keyword and semantic modes)

    - keyword: Keyword matching (searches entity_name, entity_description, tags)
    - semantic: Semantic similarity search (based on embedding vectors)

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

        if search_type == "semantic":
            from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding
            query_embedding = await get_embedding(query)
            results = await social_repo.semantic_search(
                instance_id=instance_id,
                query_embedding=query_embedding,
                limit=limit
            )
            entity_list = []
            for entity, score in results:
                entity_info = SocialNetworkEntityInfo(
                    entity_id=entity.entity_id,
                    entity_name=entity.entity_name,
                    entity_description=entity.entity_description,
                    entity_type=entity.entity_type,
                    identity_info=entity.identity_info or {},
                    contact_info=entity.contact_info or {},
                    tags=entity.tags or [],
                    relationship_strength=entity.relationship_strength or 0.0,
                    interaction_count=entity.interaction_count or 0,
                    last_interaction_time=format_for_api(entity.last_interaction_time),
                    persona=entity.persona,
                    related_job_ids=entity.related_job_ids or [],
                    expertise_domains=entity.expertise_domains or [],
                    similarity_score=round(score, 4)
                )
                entity_list.append(entity_info)
        else:
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
                    entity_description=entity.entity_description,
                    entity_type=entity.entity_type,
                    identity_info=entity.identity_info or {},
                    contact_info=entity.contact_info or {},
                    tags=entity.tags or [],
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
        logger.error(f"Error searching social network entities: {e}")
        return SocialNetworkSearchResponse(
            success=False, error=str(e), search_type=search_type
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

        entity_data = await db_client.get_one(
            "instance_social_entities",
            filters={"instance_id": instance_id, "entity_id": user_id}
        )

        if entity_data:
            entity_info = SocialNetworkEntityInfo(
                entity_id=entity_data.get("entity_id"),
                entity_name=entity_data.get("entity_name"),
                entity_description=entity_data.get("entity_description"),
                entity_type=entity_data.get("entity_type"),
                identity_info=_parse_json(entity_data.get("identity_info"), {}),
                contact_info=_parse_json(entity_data.get("contact_info"), {}),
                tags=_parse_json(entity_data.get("tags"), []),
                relationship_strength=entity_data.get("relationship_strength", 0.0),
                interaction_count=entity_data.get("interaction_count", 0),
                last_interaction_time=format_for_api(entity_data.get("last_interaction_time")),
            )
            return SocialNetworkResponse(success=True, entity=entity_info)
        else:
            return SocialNetworkResponse(
                success=False,
                error=f"No social network info found for user: {user_id}"
            )

    except Exception as e:
        logger.error(f"Error getting social network info: {e}")
        return SocialNetworkResponse(success=False, error=str(e))


@router.get("/{agent_id}/social-network", response_model=SocialNetworkListResponse)
async def get_all_social_network_entities(agent_id: str):
    """
    Get all social entities for an Agent

    Queries data from instance_social_entities table (via SocialNetworkModule's instance_id).
    """
    logger.info(f"Getting all social network entities for agent: {agent_id}")

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

        entities_data = await db_client.get(
            "instance_social_entities",
            filters={"instance_id": instance_id},
            order_by="updated_at DESC",
            limit=1000
        )

        entity_list = []
        for entity_data in entities_data:
            entity_info = SocialNetworkEntityInfo(
                entity_id=entity_data.get("entity_id"),
                entity_name=entity_data.get("entity_name"),
                entity_description=entity_data.get("entity_description"),
                entity_type=entity_data.get("entity_type"),
                identity_info=_parse_json(entity_data.get("identity_info"), {}),
                contact_info=_parse_json(entity_data.get("contact_info"), {}),
                tags=_parse_json(entity_data.get("tags"), []),
                relationship_strength=entity_data.get("relationship_strength", 0.0),
                interaction_count=entity_data.get("interaction_count", 0),
                last_interaction_time=format_for_api(entity_data.get("last_interaction_time")),
                persona=entity_data.get("persona"),
                related_job_ids=_parse_json(entity_data.get("related_job_ids"), []),
                expertise_domains=_parse_json(entity_data.get("expertise_domains"), []),
            )
            entity_list.append(entity_info)

        return SocialNetworkListResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list)
        )

    except Exception as e:
        logger.error(f"Error getting social network entities: {e}")
        return SocialNetworkListResponse(success=False, error=str(e))


