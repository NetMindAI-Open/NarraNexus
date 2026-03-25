"""
@file_name: models.py
@author: NetMind.AI
@date: 2025-12-22
@description: Unified data models for the Narrative module

Merged from:
- narrative.py: Narrative, NarrativeInfo, NarrativeType, etc.
- event.py: Event, EventLogEntry, TriggerType, etc.
- models.py (original): ConversationSession, ContinuityResult, NarrativeSearchResult

Data model categories:
1. Event related: TriggerType, EventLogEntry, Event
2. Narrative related: NarrativeType, NarrativeActor, NarrativeInfo, DynamicSummaryEntry, Narrative
3. Session related: ConversationSession, ContinuityResult, NarrativeSearchResult
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel

# Import ModuleInstance (from schema, to avoid duplicate definitions)
from xyz_agent_context.schema.module_schema import ModuleInstance


# =============================================================================
# Event Related Models
# =============================================================================

class TriggerType(Enum):
    """
    Trigger type
    """
    CHAT = "chat"   # Chat trigger
    TASK = "task"   # Task trigger
    API = "api"     # API trigger
    TOOL = "tool"   # Agent proactively invokes a tool trigger
    OTHER = "other"


class EventLogEntry(BaseModel):
    """
    Event log entry

    Records each step of operation in the Agent Loop
    """
    timestamp: datetime  # Timestamp
    type: str  # Type: thinking, tool_call, tool_result, message_output, etc.
    content: Any  # Specific content


class Event(BaseModel):
    """
    Event represents a complete process "from trigger to final output"

    It represents a traceable reasoning and action process in the system,
    and is the basic unit for Narrative growth and updates.

    According to the design document:
    - Event contains: ID, Trigger, Env Context, Module Set, Event Log, Final Output
    - Event is the basic unit for Narrative growth and updates
    """
    id: str  # Randomly generated unique ID
    trigger: TriggerType  # Event trigger type
    trigger_source: str  # Detailed trigger source info, e.g., "user_123", "task_456"
    env_context: Dict[str, Any]  # Event execution environment info (model, agent framework, execution params, etc.)
    module_instances: List[ModuleInstance]  # All Module instances loaded during this event
    event_log: List[EventLogEntry]  # Detailed record of each reasoning/call step in the Event
    final_output: str  # Final response content produced when the Event ends
    created_at: datetime  # Event creation time
    updated_at: datetime  # Event update time

    # Association info
    narrative_id: Optional[str] = None  # Associated Narrative ID (if any)
    agent_id: str  # Associated Agent ID
    user_id: Optional[str] = None  # Associated User ID (if applicable)

    # Embedding related fields (for relevance search)
    event_embedding: Optional[List[float]] = None  # Embedding vector of the event content
    embedding_text: Optional[str] = None  # Text used to generate the embedding (input + output summary)


# =============================================================================
# Narrative Related Models
# =============================================================================

class NarrativeType(Enum):
    """
    Narrative type
    """
    CHAT = "chat"
    TASK = "task"
    OTHER = "other"


class NarrativeActorType(Enum):
    """
    Narrative actor type

    - USER: Creator/owner of the Narrative
    - AGENT: Agent participant
    - SYSTEM: System participant
    - PARTICIPANT: Target user of a Job, can access the associated Narrative but is not the creator
    """
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    PARTICIPANT = "participant"  # 2026-01-21: Support for target customers in sales scenarios


class NarrativeActor(BaseModel):
    """
    Narrative actor
    """
    id: str  # Actor's ID
    type: NarrativeActorType  # Actor's type


class NarrativeInfo(BaseModel):
    """
    Narrative basic information
    """
    name: str  # Name of the narrative
    description: str  # Description of the narrative
    current_summary: str  # Summary of the narrative
    actors: List[NarrativeActor]  # List of actors in the narrative


class DynamicSummaryEntry(BaseModel):
    """
    A single entry in the Dynamic Summary

    Records a short summary of each Event, arranged chronologically
    """
    event_id: str  # Event ID
    summary: str  # Short summary of the Event
    timestamp: datetime  # Event time
    references: List[str] = []  # Referenced other event_ids


class Narrative(BaseModel):
    """
    Narrative = Routing Metadata for a storyline

    Core concepts:
    - Narrative does not store Memory (content), only routing information (index)
    - Memory is managed by each Module through EventMemoryModule
    - narrative_id is the unique identifier for Module Instances

    Field categories:
    - Identity: id, type, agent_id
    - Routing Index: routing_embedding, topic_hint, topic_keywords
    - Orchestration Config: active_instances, instance_history_ids
    - References Only: event_ids
    - Metadata: created_at, updated_at, embedding_updated_at
    """
    # ===== Identity =====
    id: str  # Randomly generated unique ID
    type: NarrativeType  # Narrative type
    agent_id: str  # Associated Agent ID

    # ===== Core Content =====
    narrative_info: NarrativeInfo  # Narrative basic info (name, description, central summary)

    # ===== Orchestration Config =====
    # Main Chat Instance (deprecated, 2026-01-21 P1-1)
    # No longer uses a fixed main_chat_instance_id; each user gets an independent ChatModule instance via _ensure_user_chat_instance()
    main_chat_instance_id: Optional[str] = None  # Deprecated, retained only for database compatibility

    # Instance management
    active_instances: List[ModuleInstance] = []  # Currently active Module instances
    instance_history_ids: List[str] = []  # Completed/failed instance IDs

    # ===== References Only =====
    event_ids: List[str]  # List of event IDs in the narrative (chronologically ordered)

    # ===== Dynamic Summary =====
    dynamic_summary: List[DynamicSummaryEntry] = []  # Dynamic summary list

    # ===== Env Variables =====
    env_variables: Dict[str, Any] = {}  # Environment variables

    # ===== Routing Index =====
    topic_keywords: List[str] = []  # Topic keywords
    topic_hint: str = ""  # Topic hint/summary
    routing_embedding: Optional[List[float]] = None  # Routing embedding vector
    embedding_updated_at: Optional[datetime] = None  # Last update time of the embedding vector
    events_since_last_embedding_update: int = 0  # Number of Events since last embedding update

    # ===== Metadata =====
    created_at: datetime  # Narrative creation time
    updated_at: datetime  # Narrative update time
    round_counter: int = 0  # Round counter

    # ===== Association Info =====
    related_narrative_ids: List[str] = []  # Related Narrative IDs

    # ===== Special Markers =====
    is_special: str = "other"  # Special marker field, default value is "other"


# =============================================================================
# Session Related Models
# =============================================================================

class ConversationSession(BaseModel):
    """
    Conversation Session

    Used to track continuous conversations between a user and an Agent,
    determining continuity between queries.

    Lifecycle:
    - Created: On the user's first query
    - Updated: last_query and last_query_time updated after each query
    - Expired: No activity for more than SESSION_TIMEOUT
    """
    # ===== Core Identity =====
    session_id: str  # Session unique ID (format: sess_xxxxxxxx)
    user_id: str  # User ID
    agent_id: str  # Agent ID

    # ===== Time Info =====
    created_at: datetime  # Session creation time
    last_query_time: datetime  # Time of the last query

    # ===== Continuity Tracking =====
    last_query: str = ""  # Text content of the last query
    last_response: str = ""  # Content of the last Agent response
    last_query_embedding: Optional[List[float]] = None  # Embedding vector of the last query
    current_narrative_id: Optional[str] = None  # Currently active Narrative ID

    # ===== Statistics =====
    query_count: int = 0  # Total number of queries in this session


class ContinuityResult(BaseModel):
    """
    Narrative Attribution Detection Result

    Used for ContinuityDetector to return detection results.

    Note: This is not just about determining conversation continuity,
    but whether the current query belongs to the current Narrative.
    Conversation continuity != Belonging to the same Narrative.
    """
    # ===== Core Result =====
    is_continuous: bool  # Whether it belongs to the current Narrative
    confidence: float  # Confidence (0-1)
    reason: str  # Judgment reason

    # ===== Detailed Info (for debugging) =====
    rule_score: Optional[float] = None  # Quick rule score
    semantic_score: Optional[float] = None  # Semantic similarity score
    weighted_score: Optional[float] = None  # Weighted final score


class NarrativeSearchResult(BaseModel):
    """
    Narrative Search Result

    Used to return retrieved Narratives with their relevance scores
    """
    narrative_id: str  # Narrative ID
    similarity_score: float  # Similarity score (0-1)
    rank: int  # Rank (1 = most relevant)
    episode_summaries: List[str] = []    # EverMemOS episode summaries (for LLM judge and Context injection)
    episode_contents: List[str] = []    # EverMemOS episode raw contents (for short-term memory dedup)


class NarrativeSelectionResult(BaseModel):
    """
    Narrative Selection Result

    Contains the selected Narrative list, query embedding, and selection reason.
    Used for passing complete selection information in step_1_select_narrative.
    """
    narratives: List["Narrative"] = []  # Selected Narrative list
    query_embedding: Optional[List[float]] = None  # Query embedding
    selection_reason: str = ""  # Selection reason (human-readable)
    selection_method: str = ""  # Selection method: continuous, high_confidence, llm_confirmed, new_created
    is_new: bool = False  # Whether a new Narrative was created
    best_score: Optional[float] = None  # Best match score (if any)
    scores: Dict[str, float] = {}  # Per-narrative similarity scores (narrative_id → score)
    retrieval_method: str = ""  # Retrieval method: evermemos, vector, fallback_vector
    evermemos_memories: Dict[str, Any] = {} # EverMemOS retrieval result cache (for MemoryModule use)
