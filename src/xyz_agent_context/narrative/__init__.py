"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-12-22
@description: Unified exports for the Narrative package

Module structure (after refactoring):
    narrative/
    ├── __init__.py           # This file - unified exports
    ├── models.py             # Data models
    ├── config.py             # Configuration management
    ├── narrative_service.py  # Narrative service (protocol layer)
    ├── event_service.py      # Event service (protocol layer)
    ├── session_service.py    # Session service
    ├── exporters.py          # Export utilities
    ├── _narrative_impl/      # Narrative implementation (private)
    └── _event_impl/          # Event implementation (private)

Usage:
    >>> from xyz_agent_context.narrative import NarrativeService, EventService
    >>> service = NarrativeService(agent_id="agent_1")
"""

# =============================================================================
# Data Models (imported from models.py)
# =============================================================================
from .models import (
    # Event related
    TriggerType,
    EventLogEntry,
    Event,
    # Narrative related
    NarrativeType,
    NarrativeActorType,
    NarrativeActor,
    NarrativeInfo,
    DynamicSummaryEntry,
    Narrative,
    # Session related
    ConversationSession,
    ContinuityResult,
    NarrativeSearchResult,
)

# ModuleInstance imported from schema
from xyz_agent_context.schema.module_schema import ModuleInstance

# =============================================================================
# Configuration (imported from config.py)
# =============================================================================
from .config import NarrativeConfig, config

# =============================================================================
# Core Services (protocol layer)
# =============================================================================
from .narrative_service import NarrativeService
from .event_service import EventService
from .session_service import SessionService

# =============================================================================
# Public interfaces from private implementation
# =============================================================================
from ._narrative_impl import (
    ContinuityDetector,
    InstanceHandler,
)


# =============================================================================
# Export Utilities (imported from exporters.py)
# =============================================================================
from .exporters import (
    NarrativeMarkdownManager,
    TrajectoryRecorder,
)

# =============================================================================
# Default Narratives (imported from _narrative_impl/default_narratives.py)
# =============================================================================
from ._narrative_impl import (
    DEFAULT_NARRATIVES_CONFIG,
    build_default_narrative_id,
    build_default_narrative_id_pattern,
    create_default_narrative,
    ensure_default_narratives,
    get_default_narrative_by_name,
    get_default_narrative_by_code,
    get_all_default_narrative_names,
    get_all_default_narrative_codes,
    get_default_narrative_config,
)


# =============================================================================
# Public API
# =============================================================================
__all__ = [
    # ===== Data Models =====
    "TriggerType",
    "EventLogEntry",
    "Event",
    "ModuleInstance",
    "NarrativeType",
    "NarrativeActorType",
    "NarrativeActor",
    "NarrativeInfo",
    "DynamicSummaryEntry",
    "Narrative",
    "ConversationSession",
    "ContinuityResult",
    "NarrativeSearchResult",

    # ===== Configuration =====
    "NarrativeConfig",
    "config",

    # ===== Core Services =====
    "NarrativeService",
    "EventService",
    "SessionService",

    "ContinuityDetector",
    "InstanceHandler",

    # ===== Export Utilities =====
    "NarrativeMarkdownManager",
    "TrajectoryRecorder",
    
    # ===== Default Narratives =====
    "DEFAULT_NARRATIVES_CONFIG",
    "build_default_narrative_id",
    "build_default_narrative_id_pattern",
    "create_default_narrative",
    "ensure_default_narratives",
    "get_default_narrative_by_name",
    "get_default_narrative_by_code",
    "get_all_default_narrative_names",
    "get_all_default_narrative_codes",
    "get_default_narrative_config",
]
