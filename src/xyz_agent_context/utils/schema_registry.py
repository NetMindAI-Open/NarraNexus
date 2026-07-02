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


def get_registered_tables() -> List[TableDef]:
    """Return all registered table definitions."""
    return list(TABLES.values())


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
            # --- Agent Runtime Lifecycle (Phase C, 2026-05-13) ---
            # See reference/self_notebook/specs/2026-05-13-agent-runtime-lifecycle-
            # and-stream-resilience-design.md §4.1
            #
            # `state` describes the live status of the agent run associated
            # with this event. Old rows default to 'completed' (they finished
            # before this feature shipped — reconcile MUST NOT mistake them
            # for stale running runs).
            #
            # `state` values:
            #   running   — BackgroundRun task is alive in some backend process
            #   completed — finished normally
            #   cancelled — user pressed Stop
            #   failed    — fatal error (timeout, SDK crash, backend restart)
            Column("state", "TEXT", "VARCHAR(32)", nullable=False, default="'completed'"),
            Column("started_at", "TEXT", "DATETIME(6)"),
            Column("last_event_at", "TEXT", "DATETIME(6)"),
            Column("finished_at", "TEXT", "DATETIME(6)"),
            Column("tool_call_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("current_stage", "TEXT", "VARCHAR(64)"),
            Column("error_message", "TEXT", "TEXT"),
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
            Index("idx_events_agent_created", ["agent_id", "created_at"]),
            # Phase C: filter running rows for reconcile + active_run lookup
            Index("idx_events_state", ["state"]),
            Index("idx_events_agent_state", ["agent_id", "state"]),
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
# NOTE (unified-memory overhaul task 1, 2026-06-08): entities now live in the
# engine's `memory_entity` table — SocialNetworkRepository writes/reads there,
# NOT here. This table is kept (no longer written by the repo) only because the
# bundle export/import + its roundtrip test still reference it; it is removed
# together with the bundle's memory_* migration in overhaul task 3.
_register(
    TableDef(
        name="instance_social_entities",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("instance_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("entity_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("entity_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("entity_name", "TEXT", "VARCHAR(255)"),
            Column("aliases", "TEXT", "JSON"),
            Column("entity_description", "TEXT", "TEXT"),
            Column("identity_info", "TEXT", "JSON"),
            Column("contact_info", "TEXT", "JSON"),
            Column("familiarity", "TEXT", "VARCHAR(32)", default="'known_of'"),
            Column("relationship_strength", "REAL", "FLOAT", default="0.0"),
            Column("interaction_count", "INTEGER", "INT", default="0"),
            Column("last_interaction_time", "TEXT", "DATETIME(6)"),
            Column("tags", "TEXT", "JSON"),
            Column("expertise_domains", "TEXT", "JSON"),
            Column("related_job_ids", "TEXT", "JSON"),
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
            Column("next_run_at_local", "TEXT", "VARCHAR(32)"),
            Column("next_run_tz", "TEXT", "VARCHAR(64)"),
            Column("last_run_at_local", "TEXT", "VARCHAR(32)"),
            Column("last_run_tz", "TEXT", "VARCHAR(64)"),
            Column("last_run_time", "TEXT", "DATETIME(6)"),
            Column("started_at", "TEXT", "DATETIME(6)"),
            Column("related_entity_id", "TEXT", "VARCHAR(64)"),
            Column("narrative_id", "TEXT", "VARCHAR(64)"),
            Column("monitored_job_ids", "TEXT", "JSON"),
            Column("iteration_count", "INTEGER", "INT", default="0"),
            # 2026-06-01: resilience / backoff state (job-scheduler redesign).
            # auto_migrate is additive — these land as nullable/default columns
            # on existing rows.
            Column("consecutive_failure_count", "INTEGER", "INT", default="0"),
            Column("cooldown_until", "TEXT", "DATETIME(6)"),
            Column("paused_reason", "TEXT", "VARCHAR(32)"),
            Column("paused_at", "TEXT", "DATETIME(6)"),
            # 2026-05-27: NOT NULL + DEFAULT added defensively. Pre-existing
            # job rows in local sqlite DBs occasionally had created_at /
            # updated_at = NULL (the column previously had no constraint),
            # which then crashed job_trigger's _row_to_entity → JobModel
            # construction with a pydantic ValidationError every poll cycle:
            #   "Input should be a valid datetime, input_value=None"
            # auto_migrate is additive-only and won't backfill existing NULL
            # rows — `_row_to_entity` does that at the read boundary via
            # `row.get(...) or datetime.now()`. The DEFAULT here prevents any
            # FUTURE INSERT from re-creating the bug.
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
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
            # Capability flag — does this provider's endpoint run Anthropic's
            # server-side tools (web_search_20250305, text_editor, ...)?
            # False for aggregators like NetMind/OpenRouter (they hang on
            # WebSearch); True for official Anthropic and transparent
            # forward proxies. auto_migrate() will add this column to
            # pre-existing tables with the default value.
            Column("supports_anthropic_server_tools", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            # --- Provider Unification (2026-05-13) — see spec
            # reference/self_notebook/specs/2026-05-13-provider-unification-design.md
            #
            # driver_type    : key into agent_framework.provider_driver.DRIVER_REGISTRY.
            #                  null on existing rows; backfilled at startup via
            #                  derive_driver_type(source, auth_type, protocol).
            # owner_user_id  : null = system-shared card (cloud only); otherwise
            #                  equals user_id. Local mode always self-owned.
            # billing_policy : 'user_pays' (default) | 'system_quota' (cloud
            #                  system row) | 'external_oauth' (Claude OAuth).
            # auth_ref       : where to find the credential when api_key alone
            #                  isn't enough — e.g. for OAuth this points at
            #                  ~/.claude/.credentials.json on the host.
            Column("driver_type", "TEXT", "VARCHAR(32)"),
            Column("owner_user_id", "TEXT", "VARCHAR(64)"),
            Column("billing_policy", "TEXT", "VARCHAR(32)", default="'user_pays'"),
            Column("auth_ref", "TEXT", "VARCHAR(512)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_up_user_provider", ["user_id", "provider_id"], unique=True),
            Index("idx_up_user_id", ["user_id"]),
            Index("idx_up_driver_type", ["driver_type"]),
            Index("idx_up_owner", ["owner_user_id"]),
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
            # Framework-neutral per-slot params (thinking, reasoning_effort,
            # and future knobs) serialized as one JSON object. A single
            # extensible column so later per-slot settings don't need
            # another migration. NULL / absent = all params at auto.
            Column("params_json", "TEXT", "MEDIUMTEXT"),
            # Set by self_heal_if_broken() when a slot.model that no longer
            # exists in its provider.models array is auto-repaired to the
            # default. Used as a 24h debounce so a misbehaving slot doesn't
            # write a notification on every LLM call.
            Column("last_auto_repaired_at", "TEXT", "DATETIME(6)"),
            # Coding-agent framework choice — only meaningful on the
            # ``slot_name='agent'`` row. Drives step_3_agent_loop's SDK
            # dispatch: "claude_code" → ClaudeAgentSDK,
            # "codex_cli" → CodexSDK. Default keeps existing rows
            # backward-compatible without a separate backfill pass —
            # the resolver also treats null as claude_code.
            Column(
                "agent_framework",
                "TEXT",
                "VARCHAR(32)",
                nullable=True,
                default="'claude_code'",
            ),
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


# --- 27. lark_credentials ---------------------------------------------------
_register(
    TableDef(
        name="lark_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("app_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("app_secret_ref", "TEXT", "VARCHAR(128)", nullable=False),
            Column("app_secret_encrypted", "TEXT", "VARCHAR(512)"),
            Column("brand", "TEXT", "VARCHAR(16)", nullable=False),
            Column("profile_name", "TEXT", "VARCHAR(128)", nullable=False),
            Column("workspace_path", "TEXT", "VARCHAR(512)"),
            Column("bot_name", "TEXT", "VARCHAR(255)"),
            Column("owner_open_id", "TEXT", "VARCHAR(64)"),
            Column("owner_name", "TEXT", "VARCHAR(255)"),
            Column("auth_status", "TEXT", "VARCHAR(32)", nullable=False, default="'not_logged_in'"),
            Column("is_active", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("permission_state", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_lark_cred_agent_id", ["agent_id"], unique=True),
            Index("idx_lark_cred_profile", ["profile_name"], unique=True),
        ],
    )
)


# --- 27b. channel_slack_credentials -----------------------------------------
# Phase 3: per-agent Slack bot binding (Bot Token + App-Level Token).
# Tokens are stored as base64-encoded text (matching the lark_credentials
# convention: trivially reversible, NOT encryption — production deployments
# should swap in real KMS-backed crypto).
_register(
    TableDef(
        name="channel_slack_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("bot_token_encoded", "TEXT", "VARCHAR(512)", nullable=False),
            Column("app_token_encoded", "TEXT", "VARCHAR(512)", nullable=False),
            Column("bot_user_id", "TEXT", "VARCHAR(64)"),
            Column("team_id", "TEXT", "VARCHAR(64)"),
            Column("team_name", "TEXT", "VARCHAR(255)"),
            # Owner identity — populated at bind via users.lookupByEmail when
            # owner_email is supplied. Drives is_owner_interacting trust
            # signal (sender_id == owner_user_id).
            Column("owner_email", "TEXT", "VARCHAR(254)"),
            Column("owner_user_id", "TEXT", "VARCHAR(64)"),
            Column("owner_name", "TEXT", "VARCHAR(255)"),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_slack_cred_agent_id", ["agent_id"], unique=True),
            # Same Slack bot in same workspace can be bound to AT MOST one
            # agent. Two agents sharing a bot would race on the single
            # Socket Mode WebSocket slot Slack issues per app_token, and
            # the trust signal would flip-flop between agents' owner_user_ids.
            Index("idx_slack_cred_bot_identity", ["team_id", "bot_user_id"], unique=True),
        ],
    )
)


# --- 27c. channel_telegram_credentials -------------------------------------
# Phase 4: per-agent Telegram bot binding (single Bot Token from @BotFather).
# Telegram has no team/workspace concept — bot_user_id alone is the identity
# for the uniqueness check.
_register(
    TableDef(
        name="channel_telegram_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("bot_token_encoded", "TEXT", "VARCHAR(512)", nullable=False),
            # Telegram bot identity (from getMe). bot_user_id is int64 stored as string.
            Column("bot_user_id", "TEXT", "VARCHAR(64)"),
            Column("bot_username", "TEXT", "VARCHAR(128)"),
            # Owner — populated at bind via getChat("@handle"). user_id is the
            # immutable identity (username can change).
            Column("owner_username", "TEXT", "VARCHAR(64)"),
            Column("owner_user_id", "TEXT", "VARCHAR(64)"),
            Column("owner_name", "TEXT", "VARCHAR(255)"),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_tg_cred_agent_id", ["agent_id"], unique=True),
            # Same Telegram bot can be bound to AT MOST one agent. Two agents
            # racing on long-poll for the same token would flip-flop trust
            # signal + drop events arbitrarily.
            Index("idx_tg_cred_bot_identity", ["bot_user_id"], unique=True),
        ],
    )
)


# --- 27c-wx. channel_wechat_credentials ------------------------------------
# Per-agent personal-WeChat binding via the iLink ("ClawBot") gateway. The
# secret is the iLink bot_token produced by the QR-scan bind (base64-encoded,
# NOT encryption — same placeholder convention as lark/slack/telegram). The
# owner's WeChat id is opaque until they DM the freshly bound account, so it is
# claimed on the first inbound DM (owner_wx_id), not supplied at bind time.
_register(
    TableDef(
        name="channel_wechat_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            # iLink bot_token from QR bind (base64-encoded placeholder, like telegram).
            Column("bot_token_encoded", "TEXT", "VARCHAR(1024)", nullable=False),
            # iLink API base URL — bind may return a per-account `baseurl`;
            # falls back to the default host when empty.
            Column("base_url", "TEXT", "VARCHAR(256)", nullable=False, default="''"),
            # Bot's own WeChat id, when the gateway reports it (may be empty).
            Column("bot_wx_id", "TEXT", "VARCHAR(128)", nullable=False, default="''"),
            # Owner — owner_wx_id claimed on first DM; owner_user_id is the
            # NarraNexus account (agents.created_by). owner_wx_id MUST default to
            # '' (NOT NULL): claim_owner's first-DM CAS filters on
            # `owner_wx_id = ''`, and SQL `= ''` never matches NULL — a NULL here
            # would make the owner unclaimable forever.
            Column("owner_wx_id", "TEXT", "VARCHAR(128)", nullable=False, default="''"),
            Column("owner_user_id", "TEXT", "VARCHAR(64)", nullable=False, default="''"),
            Column("owner_name", "TEXT", "VARCHAR(255)", nullable=False, default="''"),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            # One WeChat account per agent. We do NOT add a cross-agent unique
            # bot-identity index: the bound account's wxid is not known at bind
            # time, so it can't be the uniqueness key (unlike telegram's
            # bot_user_id from getMe).
            Index("idx_wx_cred_agent_id", ["agent_id"], unique=True),
        ],
    )
)


# --- 27d. channel_narramessenger_credentials -------------------------------
# NarraMessenger (formerly NexusMatrix) per-agent binding. v1 transport is
# Gateway Polling + /chat/send — pure bearer-token HTTP, no Matrix client, so
# the only secret is the runtime bearer token (base64-encoded, NOT encryption;
# same placeholder convention as lark/slack/telegram). matrix_user_id is the
# bot identity; one Matrix bot binds to AT MOST one agent.
_register(
    TableDef(
        name="channel_narramessenger_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("bearer_token_encoded", "TEXT", "VARCHAR(512)", nullable=False),
            Column("backend_base_url", "TEXT", "VARCHAR(255)"),
            Column("matrix_homeserver_url", "TEXT", "VARCHAR(255)"),
            Column("matrix_user_id", "TEXT", "VARCHAR(255)"),
            # NarraMessenger identity ids returned at connect.
            Column("nexus_principal_id", "TEXT", "VARCHAR(64)"),
            Column("nexus_profile_id", "TEXT", "VARCHAR(64)"),
            Column("bind_room_id", "TEXT", "VARCHAR(255)"),
            # Owner — drives the is_owner_interacting trust signal.
            Column("owner_matrix_user_id", "TEXT", "VARCHAR(255)"),
            Column("owner_name", "TEXT", "VARCHAR(255)"),
            Column("connection_mode", "TEXT", "VARCHAR(16)", nullable=False, default="'gateway'"),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_nm_cred_agent_id", ["agent_id"], unique=True),
            # Same Matrix bot identity binds to AT MOST one agent — two agents
            # polling the same bearer would split invocations arbitrarily.
            Index("idx_nm_cred_matrix_user", ["matrix_user_id"], unique=True),
        ],
    )
)


# --- 27e. channel_discord_credentials --------------------------------------
# Discord per-agent bot binding (single Bot Token from the Developer Portal).
# Like Telegram, Discord has no workspace/team concept at the credential
# level — one bot token drives a single Gateway connection that serves every
# guild the bot has joined, so ``bot_user_id`` alone is the identity for the
# uniqueness check. Token is base64-encoded text (matching the lark/slack/
# telegram convention: trivially reversible, NOT encryption — production
# deployments should swap in real KMS-backed crypto).
_register(
    TableDef(
        name="channel_discord_credentials",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("bot_token_encoded", "TEXT", "VARCHAR(512)", nullable=False),
            # Discord bot identity (from the Gateway READY event / users/@me).
            # Snowflake ids are uint64 stored as string.
            Column("bot_user_id", "TEXT", "VARCHAR(64)"),
            Column("bot_username", "TEXT", "VARCHAR(128)"),
            # Owner — the agent owner's numeric Discord user id, supplied at
            # bind time. Drives the is_owner_interacting trust signal
            # (sender_id == owner_user_id). Discord usernames are not stable
            # identifiers, so we key trust on the numeric id only.
            Column("owner_user_id", "TEXT", "VARCHAR(64)"),
            Column("owner_name", "TEXT", "VARCHAR(255)"),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_discord_cred_agent_id", ["agent_id"], unique=True),
            # Same Discord bot can be bound to AT MOST one agent. Two agents
            # racing on a single bot token would fight over the one Gateway
            # session Discord issues per token and flip-flop the trust signal.
            Index("idx_discord_cred_bot_identity", ["bot_user_id"], unique=True),
        ],
    )
)


# Note: there is intentionally NO `arena_credentials` table. Arena is an external
# service — Arena owns the identity, and the agent's api_key lives only in its
# workspace (skills/arena/). Idempotency ("does this user already have an Arena
# agent") keys on the `agents` table via agent_metadata.provisioned_source.
# 28. user_quotas (system-default free-tier token quota per user)
_register(
    TableDef(
        name="user_quotas",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("initial_input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False),
            Column("initial_output_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False),
            Column("used_input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("used_output_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("granted_input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("granted_output_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            # User-choice toggle: when 1, force routing to the system-default
            # provider even if the user has configured their own. Respects the
            # same quota gating as the no-config fallback path. Defaults to 1
            # so newly registered users get the free tier on first chat.
            Column("prefer_system_override", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_user_quotas_user", ["user_id"], unique=True),
        ],
    )
)


# ----------------------------------------------------------------------------
# 29. user_notifications — out-of-band messages to surface in UI
#
# Introduced by the Provider Unification work (spec
# reference/self_notebook/specs/2026-05-13-provider-unification-design.md).
# The first producer is the self-heal mechanism: when a slot.model is no
# longer in the provider.models array, the resolver auto-swaps to a safe
# default and writes a `slot_auto_repaired` row here so the user finds
# out at the next time they open the app.
#
# Kept intentionally minimal — kind+payload+severity is enough for
# Settings-page bell + future system messages. payload is JSON text so
# producers don't need a schema migration per new notification kind.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="user_notifications",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("kind", "TEXT", "VARCHAR(32)", nullable=False),
            Column("payload", "TEXT", "MEDIUMTEXT"),
            Column("severity", "TEXT", "VARCHAR(16)", nullable=False, default="'info'"),
            Column("read_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_un_user_unread", ["user_id", "read_at"]),
            Index("idx_un_user_created", ["user_id", "created_at"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# 30. event_stream — per-stream-chunk persistence for live agent runs.
#
# Introduced by the Agent Runtime Lifecycle work (spec
# reference/self_notebook/specs/2026-05-13-agent-runtime-lifecycle-and-
# stream-resilience-design.md §4.1.2).
#
# Why we need it
#   The user requirement is "重连等于没关过一样" — closing a browser tab
#   mid-run and reopening it later must restore the full thinking / tool
#   trace, not just the final reply. The events table only persists
#   final_output (per-turn granularity); the streaming events themselves
#   live on the WebSocket and disappear when the WS drops. This table
#   captures every stream-level chunk so replay-on-reconnect works.
#
# Granularity decision (組合 B)
#   Thinking is grouped into SEGMENTS — a segment is the contiguous
#   stretch between two type switches (tool_call / tool_output / etc.).
#   When a non-thinking event arrives or the run ends, the buffered
#   segment is flushed as ONE row here. Tool_call and tool_output get
#   one row each. Decision driver: 4408 raw thinking chunks per Xiong-
#   style run collapse to ~50 segment rows; total row count per run is
#   bounded by `2 × tool_call_count + small constant` rather than by
#   token granularity.
#
# Layout
#   * (event_id, seq) is the natural primary key but we keep a synthetic
#     auto-increment `id` to make MySQL row inserts cheaper.
#   * `kind` is small and bounded — VARCHAR(32) is plenty.
#   * `payload` is JSON or plain text. For `thinking_segment` it is the
#     raw concatenated text; for `tool_call` / `tool_output` it is a
#     JSON object so the consumer can pull `tool_name` / `arguments` /
#     `output` without a separate column per attribute.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="event_stream",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("event_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("seq", "INTEGER", "INT", nullable=False),
            Column("kind", "TEXT", "VARCHAR(32)", nullable=False),
            Column("payload", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            # Replay-on-reconnect: SELECT ... WHERE event_id=? ORDER BY seq ASC
            Index("idx_event_stream_event_seq", ["event_id", "seq"], unique=True),
            Index("idx_event_stream_event_id", ["event_id"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# lark_seen_messages — persistent dedup of incoming Lark events (Bug 27)
#
# Lark's event delivery is at-least-once: WebSocket reconnects or missed
# acks cause the server to re-push the same `message_id`. Without a durable
# record the trigger's in-memory set is wiped on every process restart,
# and the agent answers the same message twice (observed: same message_id
# re-processed about an hour apart, once before container restart and once
# after).
#
# The trigger checks this table on every incoming event: INSERT-or-skip
# on `message_id` acts as the atomic "have we seen this before" gate.
# Rows older than 7 days are cleaned up on startup.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="lark_seen_messages",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("message_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("seen_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_lark_seen_messages_message_id", ["message_id"], unique=True),
            Index("idx_lark_seen_messages_seen_at", ["seen_at"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# lark_trigger_audit — append-only lifecycle log for the Lark trigger.
#
# Motivation: on EC2 deployments we often cannot pull container logs out.
# Without a durable record of "what the trigger was doing", post-incident
# triage degenerates into guessing. This table is the trigger's black box —
# one row per lifecycle event (ingress, dedup decision, WS connect /
# disconnect, worker error / timeout, heartbeat). The /healthz endpoint
# and any future admin UI read from here.
#
# `details` is JSON so new fields can be added without migrations.
# 30-day retention (longer than `lark_seen_messages`) because post-incident
# review needs wider history than dedup does.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="lark_trigger_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("event_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("event_type", "TEXT", "VARCHAR(64)", nullable=False),
            Column("message_id", "TEXT", "VARCHAR(128)"),
            Column("agent_id", "TEXT", "VARCHAR(128)"),
            Column("app_id", "TEXT", "VARCHAR(128)"),
            Column("chat_id", "TEXT", "VARCHAR(128)"),
            Column("sender_id", "TEXT", "VARCHAR(128)"),
            Column("details", "TEXT", "MEDIUMTEXT"),
        ],
        indexes=[
            Index("idx_lark_trigger_audit_event_time", ["event_time"]),
            Index("idx_lark_trigger_audit_event_type", ["event_type"]),
            Index("idx_lark_trigger_audit_agent_id", ["agent_id"]),
            Index("idx_lark_trigger_audit_message_id", ["message_id"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# service_audit — generic L2 observability for long-running background loops
# (JobTrigger, ModulePoller, and any future poller). incident lesson #4/#5:
# the EC2 pollers had only L1 ("process alive") visibility; a wedged poll
# coroutine looked healthy while no work happened. The channel side already
# had lark_trigger_audit; this generalises the same black-box recorder so
# any service shares one table, keyed by `service`.
#
# Event vocabulary: started / stopped / heartbeat / error. A stale-or-
# missing heartbeat row (frozen counters in `detail`) reveals a stuck loop.
# `detail` is JSON so new fields never need a migration. Append-only.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="service_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("service", "TEXT", "VARCHAR(64)", nullable=False),
            Column("event_type", "TEXT", "VARCHAR(64)", nullable=False),
            Column("detail", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_service_audit_service_time", ["service", "created_at"]),
            Index("idx_service_audit_event_type", ["event_type"]),
        ],
    )
)


# Subproject 1: Team Membership (from main)
_register(
    TableDef(
        name="teams",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("owner_user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("description", "TEXT", "TEXT"),
            Column("color", "TEXT", "VARCHAR(16)"),
            Column("source", "TEXT", "VARCHAR(64)", nullable=False, default="'user'"),
            Column("intro_md", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_teams_team_id", ["team_id"], unique=True),
            Index("idx_teams_owner_user_id", ["owner_user_id"]),
        ],
    )
)

# ----------------------------------------------------------------------------
# channel_seen_messages — multi-channel durable dedup (Phase 1 / IM abstraction)
#
# Same INSERT-or-UNIQUE atomicity as `lark_seen_messages`, but namespaced by
# channel so Lark + Slack + Telegram dedup independently. The composite UNIQUE
# `(channel, message_id)` lets the same `om_xxx` (or whatever the platform
# emits) appear in multiple channels without colliding.
#
# `lark_seen_messages` is intentionally NOT migrated here — iron rule #6
# forbids destructive DB changes. Phase 2 will switch the Lark trigger to
# point at this generic table, double-write for one release, then drop the
# old table in a separate cleanup PR once data has aged out.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="channel_seen_messages",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("channel", "TEXT", "VARCHAR(32)", nullable=False),
            Column("message_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("seen_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_channel_seen_messages_unique", ["channel", "message_id"], unique=True),
            Index("idx_channel_seen_messages_seen_at", ["seen_at"]),
        ],
    )
)

_register(
    TableDef(
        name="team_members",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("joined_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_team_members_team_agent", ["team_id", "agent_id"], unique=True),
            Index("idx_team_members_agent_id", ["agent_id"]),
            Index("idx_team_members_team_id", ["team_id"]),
        ],
    )
)

# Subproject 2: Bundle Import — preflight session storage (cross-process / crash-safe)
_register(
    TableDef(
        name="bundle_preflight_sessions",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("token", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("work_dir", "TEXT", "VARCHAR(1024)", nullable=False),
            Column("manifest_json", "TEXT", "MEDIUMTEXT", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_bps_token", ["token"], unique=True),
            Index("idx_bps_created", ["created_at"]),
        ],
    )
)

# Subproject 2: Bundle Export/Import — skill archive registry
_register(
    TableDef(
        name="skill_archives",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("skill_name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("source_type", "TEXT", "VARCHAR(16)", nullable=False),
            Column("source_url", "TEXT", "VARCHAR(1024)"),
            Column("archive_path", "TEXT", "VARCHAR(1024)"),
            Column("sha256", "TEXT", "VARCHAR(64)", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_skill_arch_user_name", ["user_id", "skill_name"], unique=True),
            Index("idx_skill_arch_user_id", ["user_id"]),
        ],
    )
)

# ----------------------------------------------------------------------------
# channel_trigger_audit — multi-channel lifecycle audit (Phase 1 / IM abstraction)
#
# Generic version of `lark_trigger_audit` with an additional `channel` column
# so all IM triggers write to one observable table. `details` stays JSON so
# new fields can be added without migrations. 30-day retention matches Lark.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="channel_trigger_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("channel", "TEXT", "VARCHAR(32)", nullable=False),
            Column("event_time", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("event_type", "TEXT", "VARCHAR(64)", nullable=False),
            Column("message_id", "TEXT", "VARCHAR(128)"),
            Column("agent_id", "TEXT", "VARCHAR(128)"),
            Column("app_id", "TEXT", "VARCHAR(128)"),
            Column("chat_id", "TEXT", "VARCHAR(128)"),
            Column("sender_id", "TEXT", "VARCHAR(128)"),
            Column("details", "TEXT", "MEDIUMTEXT"),
        ],
        indexes=[
            Index("idx_channel_trigger_audit_event_time", ["event_time"]),
            Index("idx_channel_trigger_audit_channel_event_type", ["channel", "event_type"]),
            Index("idx_channel_trigger_audit_agent_id", ["agent_id"]),
            Index("idx_channel_trigger_audit_message_id", ["message_id"]),
        ],
    )
)


# ── Artifacts (agent visual outputs) ─────────────────────────────────────
# Pointer model (2026-05-14): an artifact is a pointer to an entry file the
# agent wrote inside its own workspace. `file_path` is the entry file relative
# to settings.base_working_path; the entry file's directory is the artifact
# root and is served wholesale (multi-file HTML apps + sibling assets).
_register(
    TableDef(
        name="instance_artifacts",
        columns=[
            Column("artifact_id",    "TEXT",    "VARCHAR(32)",  nullable=False, primary_key=True),
            Column("agent_id",       "TEXT",    "VARCHAR(128)", nullable=False),
            Column("user_id",        "TEXT",    "VARCHAR(128)", nullable=False),
            Column("session_id",     "TEXT",    "VARCHAR(64)"),
            Column("original_session_id", "TEXT", "VARCHAR(64)"),    # remembers session_id at pin time so unpin can restore it
            Column("title",          "TEXT",    "VARCHAR(200)", nullable=False),
            Column("kind",           "TEXT",    "VARCHAR(64)",  nullable=False),
            Column("description",    "TEXT",    "TEXT"),
            Column("pinned",         "INTEGER", "TINYINT(1)",   nullable=False, default="0"),
            # Pointer-model columns. file_path = entry file relative to
            # base_working_path; size_bytes = recursive size of the artifact
            # root directory. Nullable so auto_migrate can add them to existing
            # DBs without a backfill — old (versioned) rows keep file_path NULL
            # and are hand-migrated per the cleanup TODO.
            Column("file_path",      "TEXT",    "VARCHAR(512)"),
            Column("size_bytes",     "INTEGER", "BIGINT",       nullable=False, default="0"),
            # DEPRECATED (2026-05-14): versioning was dropped with the pointer
            # model. Column kept so auto_migrate keeps provisioning it and
            # colleagues can hand-migrate old rows. No code reads/writes it.
            # Cleanup: reference/self_notebook/todo/2026-05-14-cleanup-dead-artifact-versions.md
            Column("latest_version", "INTEGER", "INT",          nullable=False, default="1"),
            Column("created_at",     "TEXT",    "DATETIME(6)",  nullable=False, default="(datetime('now'))"),
            Column("updated_at",     "TEXT",    "DATETIME(6)",  nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_artifact_agent_session", ["agent_id", "session_id"]),
            Index("idx_artifact_agent_pinned",  ["agent_id", "pinned"]),
            Index("idx_artifact_agent_id",      ["agent_id"]),  # agent-scoped scans
        ],
    )
)

# DEPRECATED (2026-05-14): the pointer model dropped per-version content rows.
# This table is kept registered so auto_migrate keeps provisioning it and
# colleagues with old saved HTML can hand-migrate from these rows. No code
# reads or writes it anymore.
# Cleanup: reference/self_notebook/todo/2026-05-14-cleanup-dead-artifact-versions.md
_register(
    TableDef(
        name="instance_artifact_versions",
        columns=[
            Column("id",          "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("artifact_id", "TEXT",    "VARCHAR(32)",     nullable=False),
            Column("version",     "INTEGER", "INT",             nullable=False),
            Column("file_path",   "TEXT",    "VARCHAR(512)",    nullable=False),
            Column("size_bytes",  "INTEGER", "BIGINT",          nullable=False),
            Column("created_at",  "TEXT",    "DATETIME(6)",     nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_artifact_version", ["artifact_id", "version"], unique=True)],
    )
)


# ----------------------------------------------------------------------------
# invite_codes — RETIRED feature, table kept for its data. The invite-code
# registration gate was removed 2026-06-11 (cloud signup is NetMind login
# now; everyone gets the free-tier quota). Rows are retained because they
# hold the only old-user-id -> email mapping, which the legacy-user
# migration (scripts/migrate_users_to_netmind.py) needs. No code writes
# this table anymore; drop it after the migration is complete.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="invite_codes",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("code", "TEXT", "VARCHAR(32)", nullable=False, unique=True),
            Column("email", "TEXT", "VARCHAR(255)", nullable=False),
            Column("status", "TEXT", "VARCHAR(16)", nullable=False, default="'issued'"),
            Column("source", "TEXT", "VARCHAR(32)", nullable=False, default="'website'"),
            Column("email_sent", "INTEGER", "TINYINT", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("issued_at", "TEXT", "DATETIME(6)"),
            Column("used_at", "TEXT", "DATETIME(6)"),
            Column("used_by_user_id", "TEXT", "VARCHAR(128)"),
        ],
        indexes=[
            Index("idx_invite_codes_code", ["code"], unique=True),
            Index("idx_invite_codes_email", ["email"]),
            Index("idx_invite_codes_status", ["status"]),
        ],
    )
)



# ----------------------------------------------------------------------------
# user_settings — per-user preferences. First use: analytics opt-out.
# JSON-free flat columns for the few flags we have; add columns via
# schema_registry as new prefs appear (auto_migrate is additive).
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="user_settings",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("analytics_opt_out", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_user_settings_user", ["user_id"], unique=True)],
    )
)


# ============================================================================
# Unified Agent Memory (refactor/agent-memory, 2026-06-03)
# ----------------------------------------------------------------------------
# One physical table per memory `kind`, all sharing ONE column schema (the
# `memory_record`). The MemoryRepository/MemoryEngine are generic over these
# tables; the (scope, kind) chosen at instantiation selects the table. This
# replaces the per-module bespoke memory tables + the runtime-`CREATE TABLE`
# EventMemoryRepository path (which bypassed this registry and was MySQL-only).
#
# Design: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md §4.
# No vectors anywhere here — retrieval is BM25 + grep + structured filters.
# ============================================================================

# The seven memory kinds. One table each, identical schema.
MEMORY_KINDS = ("event", "narrative", "chat", "entity", "bus", "job", "observation")


def _memory_kind_table(kind: str) -> TableDef:
    """Build the unified `memory_record` TableDef for one kind.

    Every kind table is column-for-column identical so the generic
    MemoryRepository can target any of them by name. bi-temporal is a
    first-class citizen on every kind (decision 5): valid_at/invalid_at are
    the reality axis (LLM-extracted), created_at/expired_at the system axis
    (code-written); a contradicted record is tombstoned via invalid_at +
    expired_at rather than deleted.
    """
    name = f"memory_{kind}"
    return TableDef(
        name=name,
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("record_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            # Scope: who/what this memory belongs to. agent|user|narrative|instance|global
            Column("scope_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("scope_id", "TEXT", "VARCHAR(128)", nullable=False, default="''"),
            Column("kind", "TEXT", "VARCHAR(32)", nullable=False),
            Column("subtype", "TEXT", "VARCHAR(64)"),  # observation: world|experience; entity: user|agent|group
            # ★ Unified natural-language surface — the BM25 + grep target, and
            #   the text fed to the LLM. Every kind populates this.
            Column("content_text", "TEXT", "MEDIUMTEXT"),
            Column("attributes", "TEXT", "MEDIUMTEXT"),  # JSON — kind-specific structured payload
            Column("tags", "TEXT", "JSON"),  # JSON array — strong filter keys (entity:xxx, topic:xxx)
            # --- bi-temporal (graphiti范式) ---
            Column("valid_at", "TEXT", "DATETIME(6)"),     # reality: became true (NULL = always)
            Column("invalid_at", "TEXT", "DATETIME(6)"),   # reality: stopped being true (NULL = still true)
            Column("expired_at", "TEXT", "DATETIME(6)"),   # system: superseded tombstone (NULL = live)
            # --- provenance + confidence (hindsight范式) ---
            Column("source_ids", "TEXT", "JSON"),          # which events/records produced this
            Column("source_ref", "TEXT", "JSON"),          # pointer {kind,id} back to the original (projection kinds); NULL = self-contained
            Column("proof_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("history", "TEXT", "MEDIUMTEXT"),        # JSON — evolution snapshots
            # --- lifecycle ---
            Column("salience", "REAL", "FLOAT", nullable=False, default="0"),
            Column("last_used_at", "TEXT", "DATETIME(6)"),  # recency: last recalled
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index(f"idx_{name}_record_id", ["record_id"], unique=True),
            Index(f"idx_{name}_agent", ["agent_id"]),
            Index(f"idx_{name}_scope", ["agent_id", "scope_type", "scope_id"]),
            Index(f"idx_{name}_subtype", ["agent_id", "subtype"]),
            Index(f"idx_{name}_expired", ["agent_id", "expired_at"]),  # live-record filtering
            Index(f"idx_{name}_recency", ["agent_id", "last_used_at"]),
        ],
    )


for _mem_kind in MEMORY_KINDS:
    _register(_memory_kind_table(_mem_kind))


# Consolidation dirty-scope queue (design §7.4). A turn marks a (scope, kind)
# dirty here (cheap, synchronous); the background consolidation worker drains
# it (count4 / idle90s / narrative-boundary / cap20 triggers + coalescing).
_register(
    TableDef(
        name="memory_consolidation_queue",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("scope_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("scope_id", "TEXT", "VARCHAR(128)", nullable=False, default="''"),
            Column("kind", "TEXT", "VARCHAR(32)", nullable=False),
            Column("pending_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("last_dirty_at", "TEXT", "DATETIME(6)"),
            Column("last_consolidated_at", "TEXT", "DATETIME(6)"),
            # dirty | processing | failed
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'dirty'"),
            Column("consolidation_failed_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_consolidation_scope", ["agent_id", "scope_type", "scope_id", "kind"], unique=True),
            Index("idx_consolidation_status", ["status"]),
            Index("idx_consolidation_dirty", ["status", "last_dirty_at"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# instance_executor_audit — executor/loop lifecycle + OOM events audit log.
#
# Motivation (incident lesson #5): container restart wipes docker logs; DB
# rows survive. After any OOM or runaway-loop incident the question is always
# "what was the executor doing in the 10 minutes before it died?" — this table
# is the answer. Append-only; one row per lifecycle event (container start /
# reuse / cull / orphan reap / OOM / admit queue / admit grant). `detail_json`
# stores arbitrary context so callers never need a migration per new field.
#
# Indexed on (event_type, created_at) to support the L3 monitoring query
# ExecutorAuditRepository.counts_since() which counts event types over a window.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="instance_executor_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("event_type", "TEXT", "VARCHAR(32)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)"),
            Column("container_id", "TEXT", "VARCHAR(64)"),
            Column("run_id", "TEXT", "VARCHAR(64)"),
            Column("active_loops", "INTEGER", "INT"),
            Column("active_users", "INTEGER", "INT"),
            Column("queue_depth", "INTEGER", "INT"),
            Column("free_mem_mb", "INTEGER", "INT"),
            Column("detail_json", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_exec_audit_type_time", ["event_type", "created_at"]),
        ],
    )
)


# Migration ledger — records which ordered data migrations have been applied to
# THIS database, so the startup runner skips them (run-once) and a cross-version
# upgrade applies every still-pending step in order. See migrations/.
_register(
    TableDef(
        name="schema_migrations",
        columns=[
            Column("migration_id", "TEXT", "VARCHAR(128)", nullable=False, primary_key=True),
            Column("applied_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("app_version", "TEXT", "VARCHAR(64)"),
            Column("notes", "TEXT", "MEDIUMTEXT"),
        ],
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
            if col.primary_key and not table.primary_key:
                # Single-column text primary key (non-autoincrement)
                parts.append("PRIMARY KEY")
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
            # MySQL rejects non-NULL DEFAULT on TEXT / BLOB / JSON / GEOMETRY
            # columns (error 1101). Skip the DEFAULT clause on those types;
            # the application layer must supply values at insert time.
            mysql_type_upper = (col.mysql_type or "").upper()
            is_lob = any(
                tok in mysql_type_upper
                for tok in ("TEXT", "BLOB", "JSON", "GEOMETRY")
            )
            if not is_lob:
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
                        # MySQL rejects non-NULL DEFAULT on TEXT/BLOB/JSON/GEOMETRY
                        # (error 1101). Only emit DEFAULT when the target type
                        # allows it.
                        if dialect == "mysql":
                            mysql_type_upper = (col.mysql_type or "").upper()
                            if any(
                                tok in mysql_type_upper
                                for tok in ("TEXT", "BLOB", "JSON", "GEOMETRY")
                            ):
                                default = ""
                            else:
                                default = f" DEFAULT {default_val}"
                        else:
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

    # Post-migration self-heal (2026-05-13). The CREATE / ALTER / INDEX
    # loop above is supposed to be idempotent and leave every registered
    # table in place, but field reports show it can quietly fail to
    # create some tables (older backend booting on even-older DB, write
    # lock contention, DDL generator edge case, half-applied migration
    # from a previous abrupt shutdown, …). For non-technical users
    # running ``bash run.sh`` or a packaged dmg, a missing table later
    # surfaces as "click button, nothing happens" — they have no way to
    # diagnose, no way to manually recover.
    #
    # So we explicitly re-verify, and if anything is missing we run
    # CREATE TABLE for those entries one more time, with per-table
    # error tolerance. If a table is STILL missing after the second
    # attempt we log loudly but DO NOT raise — the rest of the app keeps
    # working, only operations on that specific table will fail, which
    # is strictly less bad than a backend that won't start.
    await _self_heal_missing_tables(backend, dialect)


async def _self_heal_missing_tables(
    backend: "DatabaseBackend", dialect: str
) -> None:
    """Detect and re-create tables that the registry expects but the DB
    is missing. Idempotent; safe to call multiple times.

    The whole point is to let a non-technical user simply restart the
    app (or even the same launch) and get a working DB without ever
    having to look at logs or run SQL. If self-heal can't fix it on a
    given boot, we still log loudly and keep going.
    """
    missing = await _verify_all_tables_present(backend, dialect)
    if not missing:
        logger.info(
            f"Schema integrity verified: all {len(TABLES)} registered tables present"
        )
        return

    logger.warning(
        f"Schema self-heal: {len(missing)} table(s) missing after migrate — "
        f"re-attempting CREATE for: {missing}"
    )

    for table_name in missing:
        table_def = TABLES.get(table_name)
        if table_def is None:
            # Should be impossible — `missing` came from iterating TABLES — but
            # be defensive.
            continue
        try:
            ddl_stmts = generate_create_table_sql(table_def, dialect)
            for stmt in ddl_stmts:
                await backend.execute_write(stmt)
            logger.info(f"Schema self-heal: re-created table `{table_name}`")
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Schema self-heal: re-create of `{table_name}` failed: {e}. "
                f"Routes touching this table will return 5xx until the next "
                f"successful boot."
            )

    # Final verification.
    still_missing = await _verify_all_tables_present(backend, dialect)
    if still_missing:
        # Don't raise — degrade gracefully. The user might be able to
        # use the app's other features while this gets diagnosed.
        logger.error(
            f"Schema self-heal could NOT recover tables: {still_missing}. "
            f"The backend will continue but operations on those tables "
            f"will fail. Run `make db-doctor` to inspect, or stop the "
            f"backend and rm the SQLite file if you want a clean reset "
            f"(local mode only — you'll lose data)."
        )
    else:
        logger.info(
            f"Schema self-heal complete: all {len(TABLES)} tables present"
        )


async def _verify_all_tables_present(
    backend: "DatabaseBackend", dialect: str
) -> list[str]:
    """Return the list of TABLES entries that don't actually exist in the
    backend. Empty list means everything's fine.

    Exposed as a module-level helper so `make db-doctor` (or any other
    diagnostic CLI) can call it without re-running migration.
    """
    missing: list[str] = []
    for table_name in TABLES.keys():
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
            missing.append(table_name)
    return missing
