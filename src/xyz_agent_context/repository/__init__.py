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
from .agent_message_repository import AgentMessageRepository
from .agent_circuit_breaker_repository import AgentCircuitBreakerRepository
from .event_memory_repository import EventMemoryRepository

# Instance-related Repositories (ModuleInstance decoupled)
from .instance_repository import InstanceRepository
from .instance_link_repository import InstanceNarrativeLinkRepository
from .instance_awareness_repository import InstanceAwarenessRepository, InstanceAwareness


# Subproject 1: Team Membership
from .team_repository import TeamRepository, TeamMemberRepository

# Subproject 2: Skill Archive
from .skill_archive_repository import SkillArchiveRepository

# Invite code registration gate

# Import entity types from schema (convenient re-export)
from xyz_agent_context.schema import (
    SocialNetworkEntity,
    MCPUrl,
    User,
    UserStatus,
    Agent,
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
    "AgentCircuitBreakerRepository",
    "EventMemoryRepository",
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
    # Instance (ModuleInstance decoupled)
    "InstanceRepository",
    "InstanceNarrativeLinkRepository",
    # Instance Awareness
    "InstanceAwarenessRepository",
    "InstanceAwareness",
        # Team
    "TeamRepository",
    "TeamMemberRepository",
    # Skill Archive
    "SkillArchiveRepository",
    # Invite Code
]
