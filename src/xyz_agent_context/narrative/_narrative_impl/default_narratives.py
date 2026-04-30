#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file_name: default_narratives.py
@author: NetMind.AI
@date: 2026-01-07
@description: Manages the 8 special default Narratives

Before each agent converses with a user, these 8 special Narratives must exist in the database.
These Narratives handle common interaction scenarios and are not bound to specific business objects or events.

Features:
- Each agent-user combination has its own 8 independent default Narratives
- The is_special field value is "default"
- Uses narrative type as name

Design notes:
- This module belongs to the _narrative_impl private implementation layer
- Uses NarrativeCRUD for database operations to avoid circular dependency with NarrativeService
- Provides build_default_narrative_id() utility function for unified ID construction logic
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional, TYPE_CHECKING
from loguru import logger

from xyz_agent_context.utils import utc_now

from ..models import (
    Narrative,
    NarrativeType,
    NarrativeInfo,
    NarrativeActor,
    NarrativeActorType,
)

if TYPE_CHECKING:
    from .crud import NarrativeCRUD


# ===== Definition of the 8 default Narratives =====

DEFAULT_NARRATIVES_CONFIG: List[Dict[str, Any]] = [
    {
        "code": "N-01",
        "name": "GreetingAndCourtesy",
        "description": "Greetings, small talk, thanks, farewells, ending chat or explicitly terminating current conversation - purely courtesy or conversation boundary related exchanges that don't carry any actual topic or subject",
        "examples": [
            "Hello",
            "Hi there",
            "Thank you",
            "Good night",
            "Let's stop here",
            "Talk to you later",
            "No need to continue",
            "End chat"
        ]
    },
    {
        "code": "N-02",
        "name": "CasualChatOrEmotion",
        "description": "Casual chat or emotional expression that clearly doesn't point to any specific object, event, or issue; must switch Narrative once specific references appear",
        "examples": [
            "Just chatting",
            "Feeling a bit bored",
            "Been tired lately",
            "Not in the best mood today"
        ]
    },
    {
        "code": "N-03",
        "name": "JokeAndEntertainment",
        "description": "Requests purely for entertainment purposes, not involving any entity, event, or ongoing topic",
        "examples": [
            "Tell me a joke",
            "Make me laugh"
        ]
    },
    {
        "code": "N-04",
        "name": "AgentHelpAndCapability",
        "description": "Asking about agent usage, feature description, or capability boundaries, unrelated to any specific business or entity",
        "examples": [
            "What can you do?",
            "How do I use you?",
            "Will you remember me?"
        ]
    },
    {
        "code": "N-05",
        "name": "AgentPersonaConfiguration",
        "description": "Modifying or setting agent's global identity, personality, role, speaking style, behavioral preferences, etc., affecting all subsequent conversations without binding to specific events",
        "examples": [
            "Talk to me from a product manager's perspective",
            "Act as a rigorous research assistant",
            "Be more formal in your responses"
        ]
    },
    {
        "code": "N-06",
        "name": "TaskLookup",
        "description": "Viewing, searching, filtering task lists and other operational requests unrelated to specific tasks",
        "examples": [
            "What tasks do I have?",
            "Show me incomplete tasks"
        ]
    },
    {
        "code": "N-07",
        "name": "GeneralOneShotQuestion",
        "description": "One-time, independent questions not pointing to any entity or event worth ongoing discussion",
        "examples": [
            "How many kilometers in a mile?",
            "What day is it today?"
        ]
    },
    {
        "code": "N-08",
        "name": "UnclassifiedOrGarbage",
        "description": "Fallback container for inputs that clearly don't point to specific entities and aren't worth creating a new Narrative",
        "examples": [
            "Meaningless input",
            "Garbled text",
            "Unparseable command"
        ]
    }
]


# ===== ID construction utility functions =====

def build_default_narrative_id(
    agent_id: str,
    user_id: Optional[str],
    narrative_code: str
) -> str:
    """
    Build the ID for a default Narrative

    Unified ID construction logic to avoid duplication across multiple locations.

    Format:
    - With user_id: {agent_id}_{user_id}_default_{code}
    - Without user_id: {agent_id}_default_{code}

    Args:
        agent_id: Agent ID
        user_id: User ID (optional)
        narrative_code: Narrative code (e.g., "N-01")

    Returns:
        Narrative ID

    Example:
        >>> build_default_narrative_id("agent_001", "user_123", "N-01")
        'agent_001_user_123_default_N-01'
        >>> build_default_narrative_id("agent_001", None, "N-01")
        'agent_001_default_N-01'
    """
    if user_id:
        return f"{agent_id}_{user_id}_default_{narrative_code}"
    return f"{agent_id}_default_{narrative_code}"


def build_default_narrative_id_pattern(
    agent_id: str,
    user_id: Optional[str]
) -> str:
    """
    Build the ID matching pattern for default Narratives (for SQL LIKE queries)

    Args:
        agent_id: Agent ID
        user_id: User ID (optional)

    Returns:
        SQL LIKE pattern string

    Example:
        >>> build_default_narrative_id_pattern("agent_001", "user_123")
        'agent_001_user_123_default_%'
    """
    if user_id:
        return f"{agent_id}_{user_id}_default_%"
    return f"{agent_id}_default_%"


# ===== Narrative creation function =====

def create_default_narrative(
    agent_id: str,
    user_id: Optional[str],
    config: Dict[str, Any]
) -> Narrative:
    """
    Create a default Narrative instance

    Args:
        agent_id: Agent ID
        user_id: User ID (optional)
        config: Narrative configuration (from DEFAULT_NARRATIVES_CONFIG)

    Returns:
        Narrative instance (not yet saved to database)
    """
    now = utc_now()
    narrative_code = config["code"]
    narrative_name = config["name"]

    # Use unified ID construction function
    narrative_id = build_default_narrative_id(agent_id, user_id, narrative_code)

    # Create actors list
    actors = [
        NarrativeActor(id=agent_id, type=NarrativeActorType.AGENT)
    ]
    if user_id:
        actors.append(NarrativeActor(id=user_id, type=NarrativeActorType.USER))

    # Create narrative_info
    narrative_info = NarrativeInfo(
        name=narrative_name,
        description=config["description"],
        current_summary=f"This is a default {narrative_name} Narrative",
        actors=actors
    )

    # Create Narrative
    # 2026-01-21 P1-1: No longer uses main_chat_instance_id; ChatModule instances managed via link table
    narrative = Narrative(
        id=narrative_id,
        type=NarrativeType.OTHER,  # Default Narratives use OTHER type
        agent_id=agent_id,
        narrative_info=narrative_info,
        main_chat_instance_id=None,  # Deprecated
        event_ids=[],
        is_special="default",  # Marked as default Narrative
        created_at=now,
        updated_at=now
    )

    return narrative


# ===== Core business functions =====

async def ensure_default_narratives(
    agent_id: str,
    user_id: Optional[str] = None,
    crud: Optional["NarrativeCRUD"] = None
) -> Dict[str, Narrative]:
    """
    Ensure that the 8 default Narratives exist in the database for the specified agent-user combination

    Uses a concurrency-safe upsert strategy:
    - First attempts to load; if exists, returns directly
    - If not exists, creates and saves
    - Uses database primary key constraint to prevent duplicate creation

    Args:
        agent_id: Agent ID
        user_id: User ID (optional)
        crud: NarrativeCRUD instance (if None, creates a new instance)

    Returns:
        Dict[narrative_name, Narrative]: Dictionary of the 8 default Narratives
    """
    # If no crud provided, create one
    if crud is None:
        from .crud import NarrativeCRUD
        crud = NarrativeCRUD(agent_id)

    result: Dict[str, Narrative] = {}
    created_count = 0
    existing_count = 0

    logger.info(f"Checking default Narratives for agent {agent_id} + user {user_id}...")

    for config in DEFAULT_NARRATIVES_CONFIG:
        narrative_code = config["code"]
        narrative_name = config["name"]

        # Use unified ID construction function
        narrative_id = build_default_narrative_id(agent_id, user_id, narrative_code)

        # Check if already exists
        try:
            existing_narrative = await crud.load_by_id(narrative_id)

            if existing_narrative:
                logger.debug(f"Default Narrative {narrative_name} ({narrative_code}) already exists")
                result[narrative_name] = existing_narrative
                existing_count += 1
            else:
                # Does not exist, create new and save using upsert
                logger.info(f"Creating default Narrative: {narrative_name} ({narrative_code})")
                new_narrative = create_default_narrative(agent_id, user_id, config)

                # Save using upsert (concurrency-safe)
                await crud.upsert(new_narrative)

                result[narrative_name] = new_narrative
                created_count += 1

        except Exception as e:
            logger.warning(f"Error processing default Narrative {narrative_name}: {e}")
            # Try to create (might be caused by concurrency, use upsert for safety)
            try:
                new_narrative = create_default_narrative(agent_id, user_id, config)
                await crud.upsert(new_narrative)
                result[narrative_name] = new_narrative
                created_count += 1
            except Exception as create_error:
                logger.exception(f"Failed to create default Narrative {narrative_name}: {create_error}")
                raise

    logger.info(
        f"Default Narratives check completed: "
        f"{existing_count} already existed, {created_count} newly created, "
        f"{len(result)} total"
    )

    return result


# ===== Query helper functions =====

async def get_default_narrative_by_name(
    agent_id: str,
    narrative_name: str,
    user_id: Optional[str] = None,
    crud: Optional["NarrativeCRUD"] = None
) -> Optional[Narrative]:
    """
    Get a default Narrative by name

    Args:
        agent_id: Agent ID
        narrative_name: Narrative name (e.g., "GreetingAndCourtesy")
        user_id: User ID (optional; if provided, looks up the user-specific default Narrative)
        crud: NarrativeCRUD instance

    Returns:
        Narrative instance, or None if not found
    """
    # Find the corresponding code
    config = next(
        (c for c in DEFAULT_NARRATIVES_CONFIG if c["name"] == narrative_name),
        None
    )

    if not config:
        logger.warning(f"No default Narrative configuration found with name {narrative_name}")
        return None

    # Use unified ID construction function
    narrative_id = build_default_narrative_id(agent_id, user_id, config['code'])

    if crud is None:
        from .crud import NarrativeCRUD
        crud = NarrativeCRUD(agent_id)

    return await crud.load_by_id(narrative_id)


async def get_default_narrative_by_code(
    agent_id: str,
    narrative_code: str,
    user_id: Optional[str] = None,
    crud: Optional["NarrativeCRUD"] = None
) -> Optional[Narrative]:
    """
    Get a default Narrative by code

    Args:
        agent_id: Agent ID
        narrative_code: Narrative code (e.g., "N-01")
        user_id: User ID (optional; if provided, looks up the user-specific default Narrative)
        crud: NarrativeCRUD instance

    Returns:
        Narrative instance, or None if not found
    """
    # Use unified ID construction function
    narrative_id = build_default_narrative_id(agent_id, user_id, narrative_code)

    if crud is None:
        from .crud import NarrativeCRUD
        crud = NarrativeCRUD(agent_id)

    return await crud.load_by_id(narrative_id)


# ===== Configuration query functions =====

def get_all_default_narrative_names() -> List[str]:
    """
    Get all default Narrative names

    Returns:
        List of names
    """
    return [config["name"] for config in DEFAULT_NARRATIVES_CONFIG]


def get_all_default_narrative_codes() -> List[str]:
    """
    Get all default Narrative codes

    Returns:
        List of codes
    """
    return [config["code"] for config in DEFAULT_NARRATIVES_CONFIG]


def get_default_narrative_config(name_or_code: str) -> Optional[Dict[str, Any]]:
    """
    Get a default Narrative configuration by name or code

    Args:
        name_or_code: Narrative name or code

    Returns:
        Configuration dictionary, or None if not found
    """
    return next(
        (
            c for c in DEFAULT_NARRATIVES_CONFIG
            if c["name"] == name_or_code or c["code"] == name_or_code
        ),
        None
    )
