"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-11-28
@description: Repository module - Data access layer abstraction

Responsibilities:
- Encapsulate database access logic
- Provide unified CRUD interfaces
- Implement conversion between entity objects and database rows

Usage example:
    from xyz_agent_context.repository import (
        SocialNetworkRepository,
        JobRepository,
        InboxRepository,
    )

    # Create repository instances
    social_repo = SocialNetworkRepository(db_client)
    job_repo = JobRepository(db_client)

    # Use repositories for data operations
    entity = await social_repo.get_entity("user_123", "agent_456")
    jobs = await job_repo.get_jobs_by_agent("agent_456")
"""

from .base import BaseRepository
from .event_repository import EventRepository
from .narrative_repository import NarrativeRepository
from .social_network_repository import SocialNetworkRepository
from .job_repository import JobRepository
from .inbox_repository import InboxRepository
from .mcp_repository import MCPRepository
from .user_repository import UserRepository
from .agent_repository import AgentRepository
from .rag_store_repository import RAGStoreRepository
from .agent_message_repository import AgentMessageRepository

# Instance-related Repositories (ModuleInstance decoupled)
from .instance_repository import InstanceRepository
from .instance_link_repository import InstanceNarrativeLinkRepository
from .instance_awareness_repository import InstanceAwarenessRepository, InstanceAwareness

# Embedding Store
from .embedding_store_repository import EmbeddingStoreRepository

# Import entity types from schema (convenient re-export)
from xyz_agent_context.schema import (
    SocialNetworkEntity,
    MCPUrl,
    User,
    UserStatus,
    Agent,
    RAGStoreModel,
)

__all__ = [
    # Base
    "BaseRepository",
    # Event
    "EventRepository",
    # Narrative
    "NarrativeRepository",
    # Social Network
    "SocialNetworkRepository",
    "SocialNetworkEntity",
    # Job
    "JobRepository",
    # Inbox
    "InboxRepository",
    # Agent Message
    "AgentMessageRepository",
    # MCP
    "MCPRepository",
    "MCPUrl",
    # User
    "UserRepository",
    "User",
    "UserStatus",
    # Agent
    "AgentRepository",
    "Agent",
    # RAG Store
    "RAGStoreRepository",
    "RAGStoreModel",
    # Instance (ModuleInstance decoupled)
    "InstanceRepository",
    "InstanceNarrativeLinkRepository",
    # Instance Awareness
    "InstanceAwarenessRepository",
    "InstanceAwareness",
    # Embedding Store
    "EmbeddingStoreRepository",
]
