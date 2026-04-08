"""
@file_name: schema_registry.py
@author: NarraNexus
@date: 2026-04-03
@description: Unified schema registry -- single source of truth for all database tables.

Define tables once, auto-create and auto-migrate on startup.
Supports both SQLite and MySQL from the same definitions.

To add a new table: add an entry to TABLES dict via _register().
To add a new column: add it to the table's "columns" list.
On next app startup, the column is automatically added via ALTER TABLE ADD COLUMN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class Column:
    """Definition for a single database column."""

    name: str
    sqlite_type: str  # TEXT, INTEGER, REAL, BLOB
    mysql_type: str  # VARCHAR(64), BIGINT, MEDIUMTEXT, etc.
    nullable: bool = True
    default: str | None = None  # SQL default expression, e.g. "0", "'active'"
    primary_key: bool = False
    auto_increment: bool = False
    unique: bool = False


@dataclass
class Index:
    """Definition for a database index."""

    name: str
    columns: list[str]
    unique: bool = False


@dataclass
class TableDef:
    """Definition for a database table."""

    name: str
    columns: list[Column]
    indexes: list[Index] = field(default_factory=list)
    # For composite primary keys (e.g., bus_channel_members)
    primary_key: list[str] | None = None


# ============================================================================
# Registry
# ============================================================================

TABLES: Dict[str, TableDef] = {}


def _register(table: TableDef) -> None:
    """Register a table definition in the global registry."""
    TABLES[table.name] = table


# ============================================================================
# Table Definitions
# ============================================================================

# 1. agents
_register(
    TableDef(
        name="agents",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("agent_name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("created_by", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_description", "TEXT", "VARCHAR(255)"),
            Column("agent_type", "TEXT", "VARCHAR(32)"),
            Column("is_public", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("agent_metadata", "TEXT", "MEDIUMTEXT"),
            Column("agent_create_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("agent_update_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_agents_agent_id", ["agent_id"], unique=True),
            Index("idx_agents_created_by", ["created_by"]),
            Index("idx_agents_agent_type", ["agent_type"]),
            Index("idx_agents_create_time", ["agent_create_time"]),
        ],
    )
)

# 2. users
_register(
    TableDef(
        name="users",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("password_hash", "TEXT", "VARCHAR(255)"),
            Column("role", "TEXT", "VARCHAR(32)", nullable=False, default="'user'"),
            Column("user_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("display_name", "TEXT", "VARCHAR(255)"),
            Column("email", "TEXT", "VARCHAR(255)"),
            Column("phone_number", "TEXT", "VARCHAR(32)"),
            Column("nickname", "TEXT", "VARCHAR(50)"),
            Column("timezone", "TEXT", "VARCHAR(64)", nullable=False, default="'UTC'"),
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("metadata", "TEXT", "MEDIUMTEXT"),
            Column("last_login_time", "TEXT", "DATETIME(6)"),
            Column("create_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("update_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_users_user_id", ["user_id"], unique=True),
            Index("idx_users_user_type", ["user_type"]),
            Index("idx_users_status", ["status"]),
        ],
    )
)

# 3. events
_register(
    TableDef(
        name="events",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("event_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("trigger", "TEXT", "VARCHAR(128)", nullable=False),
            Column("trigger_source", "TEXT", "VARCHAR(128)", nullable=False),
            Column("env_context", "TEXT", "MEDIUMTEXT"),
            Column("module_instances", "TEXT", "MEDIUMTEXT"),
            Column("event_log", "TEXT", "MEDIUMTEXT"),
            Column("final_output", "TEXT", "TEXT"),
            Column("narrative_id", "TEXT", "VARCHAR(128)"),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)"),
            Column("event_embedding", "TEXT", "MEDIUMTEXT"),
            Column("embedding_text", "TEXT", "TEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_events_event_id", ["event_id"], unique=True),
            Index("idx_events_narrative_id", ["narrative_id"]),
            Index("idx_events_agent_id", ["agent_id"]),
            Index("idx_events_user_id", ["user_id"]),
            Index("idx_events_trigger", ["trigger"]),
            Index("idx_events_created_at", ["created_at"]),
        ],
    )
)

# 4. narratives
_register(
    TableDef(
        name="narratives",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("narrative_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("type", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("narrative_info", "TEXT", "MEDIUMTEXT"),
            Column("main_chat_instance_id", "TEXT", "VARCHAR(128)"),
            Column("active_instances", "TEXT", "MEDIUMTEXT"),
            Column("instance_history_ids", "TEXT", "MEDIUMTEXT"),
            Column("event_ids", "TEXT", "MEDIUMTEXT"),
            Column("dynamic_summary", "TEXT", "MEDIUMTEXT"),
            Column("env_variables", "TEXT", "MEDIUMTEXT"),
            Column("topic_keywords", "TEXT", "MEDIUMTEXT"),
            Column("topic_hint", "TEXT", "TEXT"),
            Column("routing_embedding", "TEXT", "MEDIUMTEXT"),
            Column("embedding_updated_at", "TEXT", "DATETIME(6)"),
            Column("events_since_last_embedding_update", "INTEGER", "INT", nullable=False, default="0"),
            Column("round_counter", "INTEGER", "INT", nullable=False, default="0"),
            Column("related_narrative_ids", "TEXT", "MEDIUMTEXT"),
            Column("is_special", "TEXT", "VARCHAR(64)", nullable=False, default="'other'"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_narratives_narrative_id", ["narrative_id"], unique=True),
            Index("idx_narratives_agent_id", ["agent_id"]),
            Index("idx_narratives_type", ["type"]),
            Index("idx_narratives_created_at", ["created_at"]),
        ],
    )
)

# 5. mcp_urls
_register(
    TableDef(
        name="mcp_urls",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("mcp_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("url", "TEXT", "VARCHAR(1024)", nullable=False),
            Column("description", "TEXT", "VARCHAR(512)"),
            Column("is_enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("connection_status", "TEXT", "VARCHAR(32)"),
            Column("last_check_time", "TEXT", "DATETIME(6)"),
            Column("last_error", "TEXT", "VARCHAR(1024)"),
            Column("metadata", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_mcp_urls_mcp_id", ["mcp_id"], unique=True),
            Index("idx_mcp_urls_agent_user", ["agent_id", "user_id"]),
            Index("idx_mcp_urls_is_enabled", ["is_enabled"]),
        ],
    )
)

# 6. inbox_table
_register(
    TableDef(
        name="inbox_table",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("source", "TEXT", "TEXT"),
            Column("event_id", "TEXT", "VARCHAR(64)"),
            Column("message_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("title", "TEXT", "VARCHAR(255)", nullable=False),
            Column("content", "TEXT", "TEXT", nullable=False),
            Column("is_read", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_inbox_message_id", ["message_id"], unique=True),
            Index("idx_inbox_user_id", ["user_id"]),
            Index("idx_inbox_is_read", ["is_read"]),
        ],
    )
)

# 7. agent_messages
_register(
    TableDef(
        name="agent_messages",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("source_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("source_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("content", "TEXT", "TEXT", nullable=False),
            Column("if_response", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("narrative_id", "TEXT", "VARCHAR(128)"),
            Column("event_id", "TEXT", "VARCHAR(128)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_agent_messages_message_id", ["message_id"], unique=True),
            Index("idx_agent_messages_agent_id", ["agent_id"]),
            Index("idx_agent_messages_agent_source", ["agent_id", "source_type"]),
            Index("idx_agent_messages_created_at", ["created_at"]),
            Index("idx_agent_messages_if_response", ["agent_id", "if_response"]),
        ],
    )
)

# 8. module_instances
_register(
    TableDef(
        name="module_instances",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("module_class", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)"),
            Column("is_public", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("description", "TEXT", "TEXT"),
            Column("dependencies", "TEXT", "MEDIUMTEXT"),
            Column("config", "TEXT", "MEDIUMTEXT"),
            Column("state", "TEXT", "MEDIUMTEXT"),
            Column("routing_embedding", "TEXT", "MEDIUMTEXT"),
            Column("keywords", "TEXT", "MEDIUMTEXT"),
            Column("topic_hint", "TEXT", "TEXT"),
            Column("last_used_at", "TEXT", "DATETIME(6)"),
            Column("completed_at", "TEXT", "DATETIME(6)"),
            Column("archived_at", "TEXT", "DATETIME(6)"),
            Column("last_polled_status", "TEXT", "VARCHAR(32)"),
            Column("callback_processed", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_module_instances_instance_id", ["instance_id"], unique=True),
            Index("idx_module_instances_agent_id", ["agent_id"]),
            Index("idx_module_instances_agent_user", ["agent_id", "user_id"]),
            Index("idx_module_instances_module_class", ["module_class"]),
            Index("idx_module_instances_status", ["status"]),
            Index("idx_module_instances_is_public", ["agent_id", "is_public"]),
        ],
    )
)

# 9. instance_social_entities
_register(
    TableDef(
        name="instance_social_entities",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("entity_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("entity_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("entity_name", "TEXT", "VARCHAR(255)"),
            Column("entity_description", "TEXT", "TEXT"),
            Column("identity_info", "TEXT", "JSON"),
            Column("contact_info", "TEXT", "JSON"),
            Column("relationship_strength", "REAL", "FLOAT", default="0.0"),
            Column("interaction_count", "INTEGER", "INT", default="0"),
            Column("last_interaction_time", "TEXT", "DATETIME(6)"),
            Column("tags", "TEXT", "JSON"),
            Column("expertise_domains", "TEXT", "JSON"),
            Column("related_job_ids", "TEXT", "JSON"),
            Column("embedding", "TEXT", "JSON"),
            Column("persona", "TEXT", "TEXT"),
            Column("extra_data", "TEXT", "JSON"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_instance_entity", ["instance_id", "entity_id"], unique=True),
            Index("idx_social_instance_id", ["instance_id"]),
            Index("idx_social_entity_type", ["entity_type"]),
        ],
    )
)

# 10. instance_jobs
_register(
    TableDef(
        name="instance_jobs",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("job_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("title", "TEXT", "VARCHAR(255)", nullable=False),
            Column("description", "TEXT", "TEXT"),
            Column("payload", "TEXT", "TEXT"),
            Column("job_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("trigger_config", "TEXT", "JSON"),
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'pending'"),
            Column("process", "TEXT", "JSON"),
            Column("last_error", "TEXT", "TEXT"),
            Column("notification_method", "TEXT", "VARCHAR(32)", default="'inbox'"),
            Column("next_run_time", "TEXT", "DATETIME(6)"),
            Column("last_run_time", "TEXT", "DATETIME(6)"),
            Column("started_at", "TEXT", "DATETIME(6)"),
            Column("embedding", "TEXT", "MEDIUMTEXT"),
            Column("related_entity_id", "TEXT", "VARCHAR(64)"),
            Column("narrative_id", "TEXT", "VARCHAR(64)"),
            Column("monitored_job_ids", "TEXT", "JSON"),
            Column("iteration_count", "INTEGER", "INT", default="0"),
            Column("created_at", "TEXT", "DATETIME(6)"),
            Column("updated_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            Index("idx_instance_jobs_job_id", ["job_id"], unique=True),
            Index("uk_instance_jobs_instance_id", ["instance_id"], unique=True),
            Index("idx_instance_jobs_agent_user", ["agent_id", "user_id"]),
            Index("idx_instance_jobs_status", ["status"]),
            Index("idx_instance_jobs_next_run_time", ["next_run_time"]),
            Index("idx_instance_jobs_narrative_id", ["narrative_id"]),
        ],
    )
)

# 11. instance_rag_store
_register(
    TableDef(
        name="instance_rag_store",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("display_name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("store_name", "TEXT", "VARCHAR(512)", nullable=False),
            Column("keywords", "TEXT", "JSON"),
            Column("uploaded_files", "TEXT", "JSON"),
            Column("file_count", "INTEGER", "INT", default="0"),
            Column("created_at", "TEXT", "DATETIME(6)"),
            Column("updated_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            Index("idx_instance_rag_store_instance_id", ["instance_id"], unique=True),
            Index("uk_rag_display_name", ["display_name"], unique=True),
        ],
    )
)

# 12. instance_narrative_links
_register(
    TableDef(
        name="instance_narrative_links",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("narrative_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("link_type", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("local_status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("linked_at", "TEXT", "DATETIME(6)"),
            Column("unlinked_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_instance_narrative", ["instance_id", "narrative_id"], unique=True),
            Index("idx_nar_links_narrative_id", ["narrative_id"]),
            Index("idx_nar_links_instance_id", ["instance_id"]),
            Index("idx_nar_links_link_type", ["link_type"]),
        ],
    )
)

# 13. instance_awareness
_register(
    TableDef(
        name="instance_awareness",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("awareness", "TEXT", "TEXT", nullable=False, default="''"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_instance_awareness_instance_id", ["instance_id"], unique=True),
        ],
    )
)

# 14. instance_module_report_memory
_register(
    TableDef(
        name="instance_module_report_memory",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("report_memory", "TEXT", "TEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_report_memory_instance_id", ["instance_id"], unique=True),
        ],
    )
)

# 15. instance_json_format_memory
_register(
    TableDef(
        name="instance_json_format_memory",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("memory", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_json_memory_instance_id", ["instance_id"], unique=True),
        ],
    )
)

# 15b. instance_json_format_memory_chat (dynamic per-module table for ChatModule)
_register(
    TableDef(
        name="instance_json_format_memory_chat",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("memory", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_json_memory_chat_instance_id", ["instance_id"], unique=True),
        ],
    )
)

# 15c. module_report_memory (module status reports to Narrative)
_register(
    TableDef(
        name="module_report_memory",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("narrative_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("module_name", "TEXT", "VARCHAR(128)", nullable=False),
            Column("report_memory", "TEXT", "TEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_narrative_module", ["narrative_id", "module_name"], unique=True),
            Index("idx_report_narrative", ["narrative_id"]),
            Index("idx_report_module", ["module_name"]),
        ],
    )
)

# 16. cost_records
_register(
    TableDef(
        name="cost_records",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("event_id", "TEXT", "VARCHAR(64)"),
            Column("call_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("model", "TEXT", "VARCHAR(128)", nullable=False),
            Column("input_tokens", "INTEGER", "INT", nullable=False, default="0"),
            Column("output_tokens", "INTEGER", "INT", nullable=False, default="0"),
            Column("total_cost_usd", "REAL", "DECIMAL(10,6)", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_cost_agent_id", ["agent_id"]),
            Index("idx_cost_created_at", ["created_at"]),
            Index("idx_cost_call_type", ["call_type"]),
        ],
    )
)

# 17. embeddings_store
_register(
    TableDef(
        name="embeddings_store",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("entity_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("entity_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("model", "TEXT", "VARCHAR(128)", nullable=False),
            Column("dimensions", "INTEGER", "INT UNSIGNED", nullable=False),
            Column("vector", "TEXT", "JSON", nullable=False),
            Column("source_text", "TEXT", "TEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_entity_model", ["entity_type", "entity_id", "model"], unique=True),
            Index("idx_emb_type_model", ["entity_type", "model"]),
            Index("idx_emb_entity", ["entity_type", "entity_id"]),
        ],
    )
)

# 20. bus_channels (text primary key, no auto-increment)
_register(
    TableDef(
        name="bus_channels",
        columns=[
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False, primary_key=True),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("channel_type", "TEXT", "VARCHAR(32)", nullable=False, default="'group'"),
            Column("created_by", "TEXT", "VARCHAR(64)", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[],
    )
)

# 21. bus_channel_members (composite primary key)
_register(
    TableDef(
        name="bus_channel_members",
        columns=[
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("joined_at", "TEXT", "DATETIME(6)"),
            Column("last_read_at", "TEXT", "DATETIME(6)"),
            Column("last_processed_at", "TEXT", "DATETIME(6)"),
        ],
        primary_key=["channel_id", "agent_id"],
        indexes=[
            Index("idx_bus_member_agent", ["agent_id"]),
        ],
    )
)

# 22. bus_messages (text primary key)
_register(
    TableDef(
        name="bus_messages",
        columns=[
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False, primary_key=True),
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("from_agent", "TEXT", "VARCHAR(64)", nullable=False),
            Column("content", "TEXT", "TEXT", nullable=False),
            Column("msg_type", "TEXT", "VARCHAR(32)", nullable=False, default="'text'"),
            Column("mentions", "TEXT", "TEXT", nullable=True),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_bus_msg_channel_time", ["channel_id", "created_at"]),
        ],
    )
)

# 23. bus_agent_registry (text primary key)
_register(
    TableDef(
        name="bus_agent_registry",
        columns=[
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, primary_key=True),
            Column("owner_user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("capabilities", "TEXT", "TEXT"),
            Column("description", "TEXT", "TEXT"),
            Column("capability_embedding", "TEXT", "MEDIUMTEXT"),
            Column("visibility", "TEXT", "VARCHAR(32)", nullable=False, default="'private'"),
            Column("registered_at", "TEXT", "DATETIME(6)"),
            Column("last_seen_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            Index("idx_bus_registry_visibility", ["visibility"]),
            Index("idx_bus_registry_owner", ["owner_user_id"]),
        ],
    )
)

# 24. user_providers (per-user LLM provider configurations)
_register(
    TableDef(
        name="user_providers",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("provider_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("source", "TEXT", "VARCHAR(32)", nullable=False),
            Column("protocol", "TEXT", "VARCHAR(32)", nullable=False),
            Column("auth_type", "TEXT", "VARCHAR(32)", nullable=False, default="'api_key'"),
            Column("api_key", "TEXT", "VARCHAR(512)"),
            Column("base_url", "TEXT", "VARCHAR(512)"),
            Column("models", "TEXT", "TEXT"),
            Column("linked_group", "TEXT", "VARCHAR(64)"),
            Column("is_active", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_up_user_provider", ["user_id", "provider_id"], unique=True),
            Index("idx_up_user_id", ["user_id"]),
        ],
    )
)

# 25. user_slots (per-user slot assignments)
_register(
    TableDef(
        name="user_slots",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("slot_name", "TEXT", "VARCHAR(32)", nullable=False),
            Column("provider_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("model", "TEXT", "VARCHAR(128)", nullable=False),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_us_user_slot", ["user_id", "slot_name"], unique=True),
        ],
    )
)

# 26. bus_message_failures (composite primary key)
_register(
    TableDef(
        name="bus_message_failures",
        columns=[
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("retry_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("last_error", "TEXT", "TEXT"),
            Column("last_retry_at", "TEXT", "DATETIME(6)"),
        ],
        primary_key=["message_id", "agent_id"],
        indexes=[],
    )
)


# ============================================================================
# DDL Generation
# ============================================================================


def generate_sqlite_ddl(table: TableDef) -> List[str]:
    """
    Generate CREATE TABLE and CREATE INDEX statements for SQLite.

    Args:
        table: The table definition.

    Returns:
        List of SQL statements (CREATE TABLE first, then CREATE INDEX).
    """
    stmts: List[str] = []
    col_defs: List[str] = []

    for col in table.columns:
        parts = [col.name]

        if col.auto_increment and col.primary_key:
            parts.append("INTEGER PRIMARY KEY AUTOINCREMENT")
        else:
            parts.append(col.sqlite_type)
            if not col.nullable:
                parts.append("NOT NULL")
            if col.unique:
                parts.append("UNIQUE")

        if col.default is not None and not (col.auto_increment and col.primary_key):
            parts.append(f"DEFAULT {col.default}")

        col_defs.append(" ".join(parts))

    # Composite primary key
    if table.primary_key:
        col_defs.append(f"PRIMARY KEY ({', '.join(table.primary_key)})")

    create_sql = (
        f"CREATE TABLE IF NOT EXISTS {table.name} (\n"
        + ",\n".join(f"    {d}" for d in col_defs)
        + "\n)"
    )
    stmts.append(create_sql)

    # Indexes
    for idx in table.indexes:
        unique = "UNIQUE " if idx.unique else ""
        cols = ", ".join(idx.columns)
        stmts.append(
            f"CREATE {unique}INDEX IF NOT EXISTS {idx.name} ON {table.name}({cols})"
        )

    return stmts


def generate_mysql_ddl(table: TableDef) -> List[str]:
    """
    Generate CREATE TABLE and CREATE INDEX statements for MySQL.

    Args:
        table: The table definition.

    Returns:
        List of SQL statements (CREATE TABLE first, then CREATE INDEX).
    """
    stmts: List[str] = []
    col_defs: List[str] = []
    pk_cols: List[str] = []

    for col in table.columns:
        parts = [f"`{col.name}`"]
        parts.append(col.mysql_type)

        if col.auto_increment:
            parts.append("NOT NULL AUTO_INCREMENT")
        else:
            if not col.nullable:
                parts.append("NOT NULL")

        if col.default is not None and not col.auto_increment:
            # Translate SQLite default expressions to MySQL equivalents
            default_val = col.default
            if default_val == "(datetime('now'))":
                default_val = "CURRENT_TIMESTAMP(6)"
            parts.append(f"DEFAULT {default_val}")

        col_defs.append(" ".join(parts))

        if col.primary_key:
            pk_cols.append(f"`{col.name}`")

    # Primary key
    if table.primary_key:
        pk_cols = [f"`{c}`" for c in table.primary_key]
    if pk_cols:
        col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

    create_sql = (
        f"CREATE TABLE IF NOT EXISTS `{table.name}` (\n"
        + ",\n".join(f"    {d}" for d in col_defs)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    stmts.append(create_sql)

    # Indexes (as separate statements for idempotent creation)
    for idx in table.indexes:
        unique = "UNIQUE " if idx.unique else ""
        cols = ", ".join(f"`{c}`" for c in idx.columns)
        stmts.append(
            f"CREATE {unique}INDEX `{idx.name}` ON `{table.name}`({cols})"
        )

    return stmts


def generate_create_table_sql(table: TableDef, dialect: str) -> List[str]:
    """
    Generate DDL statements for the given dialect.

    Args:
        table: The table definition.
        dialect: 'sqlite' or 'mysql'.

    Returns:
        List of SQL statements.
    """
    if dialect == "sqlite":
        return generate_sqlite_ddl(table)
    elif dialect == "mysql":
        return generate_mysql_ddl(table)
    else:
        raise ValueError(f"Unsupported dialect: {dialect}")


# ============================================================================
# Auto-Migration
# ============================================================================


async def auto_migrate(backend: "DatabaseBackend") -> None:
    """
    Run on every startup. Idempotent.

    Workflow:
        1. Create missing tables (CREATE TABLE IF NOT EXISTS)
        2. Add missing columns (ALTER TABLE ADD COLUMN)
        3. Create missing indexes (CREATE INDEX IF NOT EXISTS)

    Args:
        backend: An initialized DatabaseBackend instance.
    """
    from xyz_agent_context.utils.db_backend import DatabaseBackend  # noqa: F811

    dialect = backend.dialect
    tables_created = 0
    columns_added = 0
    indexes_created = 0

    for table_name, table_def in TABLES.items():
        # Check if table exists
        if dialect == "sqlite":
            rows = await backend.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
        else:
            rows = await backend.execute(
                "SELECT TABLE_NAME FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s",
                (table_name,),
            )

        if not rows:
            # Create table and indexes
            ddl_stmts = generate_create_table_sql(table_def, dialect)
            for stmt in ddl_stmts:
                await backend.execute_write(stmt)
            tables_created += 1
        else:
            # Check for missing columns
            if dialect == "sqlite":
                existing = await backend.execute(
                    f"PRAGMA table_info({table_name})", None
                )
                existing_cols = {row["name"] for row in existing}
            else:
                existing = await backend.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s",
                    (table_name,),
                )
                existing_cols = {row["COLUMN_NAME"] for row in existing}

            for col in table_def.columns:
                if col.name not in existing_cols and not col.auto_increment:
                    col_type = col.sqlite_type if dialect == "sqlite" else col.mysql_type
                    default = ""
                    if col.default is not None:
                        default_val = col.default
                        if dialect == "mysql" and default_val == "(datetime('now'))":
                            default_val = "CURRENT_TIMESTAMP(6)"
                        default = f" DEFAULT {default_val}"
                    null_clause = "" if col.nullable else " NOT NULL"
                    # SQLite cannot add NOT NULL without default
                    if dialect == "sqlite" and not col.nullable and col.default is None:
                        default = " DEFAULT ''"
                    if dialect == "mysql":
                        await backend.execute_write(
                            f"ALTER TABLE `{table_name}` ADD COLUMN `{col.name}` "
                            f"{col_type}{null_clause}{default}"
                        )
                    else:
                        await backend.execute_write(
                            f"ALTER TABLE {table_name} ADD COLUMN {col.name} "
                            f"{col_type}{null_clause}{default}"
                        )
                    columns_added += 1

            # Check for missing indexes
            if dialect == "sqlite":
                idx_rows = await backend.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                    (table_name,),
                )
                existing_indexes = {row["name"] for row in idx_rows}
            else:
                idx_rows = await backend.execute(
                    "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                    "WHERE table_schema=DATABASE() AND table_name=%s",
                    (table_name,),
                )
                existing_indexes = {row["INDEX_NAME"] for row in idx_rows}

            for idx in table_def.indexes:
                if idx.name not in existing_indexes:
                    unique = "UNIQUE " if idx.unique else ""
                    if dialect == "sqlite":
                        cols = ", ".join(idx.columns)
                        await backend.execute_write(
                            f"CREATE {unique}INDEX IF NOT EXISTS "
                            f"{idx.name} ON {table_name}({cols})"
                        )
                    else:
                        cols = ", ".join(f"`{c}`" for c in idx.columns)
                        await backend.execute_write(
                            f"CREATE {unique}INDEX `{idx.name}` "
                            f"ON `{table_name}`({cols})"
                        )
                    indexes_created += 1

    logger.info(
        f"Schema migration complete: "
        f"{tables_created} tables created, "
        f"{columns_added} columns added, "
        f"{indexes_created} indexes created "
        f"(total {len(TABLES)} tables in registry)"
    )
