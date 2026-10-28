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
    from narranexus.platform.repository import (
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
from .ban_audit_repository import BanAuditRepository
from .gateway_key_misuse_repository import GatewayKeyMisuseRepository
from .agent_repository import AgentRepository
from .agent_registry_repository import AgentRegistryRepository
from .agent_message_repository import AgentMessageRepository
from .agent_circuit_breaker_repository import AgentCircuitBreakerRepository
from .owner_notice_cooldown_repository import OwnerNoticeCooldownRepository
from .bus_delivery_receipt_repository import BusDeliveryReceiptRepository
from .channel_ingress_breaker_repository import ChannelIngressBreakerRepository
from .event_memory_repository import EventMemoryRepository

# Instance-related Repositories (ModuleInstance decoupled)
from .instance_repository import InstanceRepository
from .instance_link_repository import InstanceNarrativeLinkRepository
from .instance_awareness_repository import InstanceAwarenessRepository, InstanceAwareness
from .home_assistant_repository import HomeAssistantBindingRepository, HABindingRow


# Subproject 1: Team Membership
from .team_repository import TeamRepository, TeamMemberRepository
from .team_bulletin_repository import TeamBulletinRepository

# Subproject 2: Skill Archive
from .skill_archive_repository import SkillArchiveRepository

# Resumable coding-agent CLI session handles

# Invite code registration gate

# Import entity types from schema (convenient re-export)
from narranexus.platform.schema import (
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
    "OwnerNoticeCooldownRepository",
    "BusDeliveryReceiptRepository",
    "ChannelIngressBreakerRepository",
    "EventMemoryRepository",
    # MCP
    "MCPRepository",
    "MCPUrl",
    # User
    "UserRepository",
    "BanAuditRepository",
    "GatewayKeyMisuseRepository",
    "User",
    "UserStatus",
    # Agent
    "AgentRepository",
    "AgentRegistryRepository",
    "Agent",
    # Instance (ModuleInstance decoupled)
    "InstanceRepository",
    "InstanceNarrativeLinkRepository",
    # Instance Awareness
    "InstanceAwarenessRepository",
    "HomeAssistantBindingRepository",
    "HABindingRow",
    "InstanceAwareness",
        # Team
    "TeamRepository",
    "TeamMemberRepository",
    "TeamBulletinRepository",
    # Skill Archive
    "SkillArchiveRepository",
    # CLI Session
    # Invite Code
]
