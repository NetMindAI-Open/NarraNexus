"""
@file_name: entity_schema.py
@author: NetMind.AI
@date: 2025-12-02
@description: Entity data model Schema

Centralized management of all entity data models, for use by the Repository layer
and other modules

Includes:
- SocialNetworkEntity: Social network entity
- User: User entity
- UserStatus: User status enum
- Agent: Agent entity
- MCPUrl: MCP URL entity
"""

from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# ===== User Status Enum =====

class UserStatus(str, Enum):
    """User status enum"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"
    DELETED = "deleted"


# ===== Social Network Entity =====

class SocialNetworkEntity(BaseModel):
    """
    Social Network Entity data model

    Records information about entities (users or other Agents) in the Instance's social network

    Refactoring notes (2025-12-24):
    - owner_agent_id changed to instance_id
    - Data follows the Instance, rather than being directly tied to agent_id
    """
    # Database auto-increment ID
    id: Optional[int] = None

    # Instance association (core refactoring point)
    instance_id: str = Field(..., max_length=64, description="Associated SocialNetworkModule Instance ID")

    # Entity identifier (required)
    entity_id: str = Field(..., max_length=64, description="Entity ID (user_id or agent_id)")
    entity_type: str = Field(..., max_length=32, description="Entity type: user | agent | group")

    # Entity basic information
    entity_name: Optional[str] = Field(None, max_length=255, description="Entity name/nickname")
    aliases: List[str] = Field(
        default=[],
        description="Cross-system identifiers and alternate names (e.g., Lark open_ids, platform agent IDs)"
    )
    entity_description: Optional[str] = Field(None, description="Entity brief description")

    # Core field: Identity information (JSON format)
    identity_info: Dict[str, Any] = Field(
        default={},
        description="Identity info JSON: organization, position, expertise, preferences, etc."
    )

    # Contact information (JSON format)
    contact_info: Dict[str, Any] = Field(
        default={},
        description="Contact info JSON: chat_channel, email, preferred_method, etc."
    )

    # Familiarity level (cognitive tier)
    familiarity: str = Field(
        default="known_of",
        max_length=32,
        description="Familiarity level: direct (interacted with) | known_of (mentioned by others)"
    )

    # Relationship metadata
    relationship_strength: float = Field(
        default=0.0,
        description="Relationship strength 0.0-1.0"
    )
    interaction_count: int = Field(
        default=0,
        description="Interaction count"
    )
    last_interaction_time: Optional[datetime] = Field(
        None,
        description="Last interaction time"
    )

    # Keyword system (for search and classification)
    # NOTE: DB column is still named 'tags' — mapping handled in repository layer
    keywords: List[str] = Field(
        default=[],
        description="Keyword list: ['bitcoin_forum', 'expert:recommendation_system', 'engineer']"
    )

    # Expertise domains (for intelligent matching and recommendations)
    expertise_domains: List[str] = Field(
        default=[],
        description="Expertise domain list JSON: ['recommendation_system', 'machine_learning', 'deep_learning']"
    )

    # === Job association (Feature 2.2.1 - bidirectional index) ===
    related_job_ids: List[str] = Field(
        default=[],
        description="List of associated Job IDs, for reverse lookup of all Jobs related to this Entity"
    )

    # === Semantic search (Feature 2.3 - Entity semantic search) ===
    embedding: Optional[List[float]] = Field(
        default=None,
        description="Entity semantic vector (generated from entity_name + entity_description + keywords, for semantic search)"
    )

    # Persona (communication style guide)
    persona: Optional[str] = Field(
        default=None,
        description="Persona/style guide for communicating with this entity (natural language description)"
    )

    # Extra data (for extension fields such as embedding vectors)
    extra_data: Dict[str, Any] = Field(
        default={},
        description="Extra data JSON, for storing extension fields (e.g., embedding vectors, embedding_text, etc.)"
    )

    # Timestamps (managed automatically by database)
    created_at: Optional[datetime] = Field(default=None, description="Creation time")
    updated_at: Optional[datetime] = Field(default=None, description="Update time")


# ===== User Entity =====

class User(BaseModel):
    """User data model"""
    id: Optional[int] = None
    user_id: str = Field(..., max_length=64, description="Unique user identifier")
    user_type: str = Field(..., max_length=32, description="User type")
    display_name: Optional[str] = Field(None, max_length=255, description="Display name")
    email: Optional[str] = Field(None, max_length=255, description="Email")
    phone_number: Optional[str] = Field(None, max_length=32, description="Phone number")
    nickname: Optional[str] = Field(None, max_length=50, description="Nickname")
    timezone: str = Field(default="UTC", max_length=64, description="User timezone (IANA format, e.g., Asia/Shanghai)")
    status: UserStatus = Field(default=UserStatus.ACTIVE, description="User status")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    last_login_time: Optional[datetime] = Field(default=None, description="Last login time")
    create_time: Optional[datetime] = Field(default=None, description="Creation time")
    update_time: Optional[datetime] = Field(default=None, description="Update time")


# ===== Agent Entity =====

class Agent(BaseModel):
    """Agent data model"""
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Unique Agent identifier")
    agent_name: str = Field(..., max_length=255, description="Agent name")
    created_by: str = Field(..., max_length=64, description="Creator")
    agent_description: Optional[str] = Field(None, max_length=255, description="Agent description")
    agent_type: Optional[str] = Field(None, max_length=32, description="Agent type")
    is_public: bool = Field(default=False, description="Whether publicly visible (visible to all users)")
    agent_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    agent_create_time: Optional[datetime] = Field(default=None, description="Creation time")
    agent_update_time: Optional[datetime] = Field(default=None, description="Update time")


# ===== MCP URL Entity =====

class MCPUrl(BaseModel):
    """MCP URL data model"""
    id: Optional[int] = None
    mcp_id: str = Field(..., max_length=64, description="Unique MCP identifier")
    agent_id: str = Field(..., max_length=64, description="Unique Agent identifier")
    user_id: str = Field(..., max_length=64, description="Unique User identifier")
    name: str = Field(..., max_length=255, description="MCP name")
    url: str = Field(..., max_length=1024, description="MCP SSE URL")
    description: Optional[str] = Field(None, max_length=512, description="MCP description")
    is_enabled: bool = Field(default=True, description="Whether enabled")
    connection_status: Optional[str] = Field(None, max_length=32, description="Connection status")
    last_check_time: Optional[datetime] = Field(default=None, description="Last check time")
    last_error: Optional[str] = Field(None, max_length=1024, description="Last error message")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    created_at: Optional[datetime] = Field(default=None, description="Creation time")
    updated_at: Optional[datetime] = Field(default=None, description="Update time")
