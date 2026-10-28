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

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from loguru import logger

if TYPE_CHECKING:
    # Type-only: the name is referenced only in annotations, so keep the
    # runtime import graph unchanged.
    from narranexus.contracts.table import TableSpec
    from narranexus.platform.utils.db.db_backend import DatabaseBackend


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

# table name -> plugin id that owns it. Core tables registered through
# ``_register`` are owned by the kernel; plugin tables arrive through
# ``register_table`` and carry their owner so uninstall/purge can list them.
TABLE_OWNERS: Dict[str, str] = {}
KERNEL_TABLE_OWNER = "builtin.kernel"


def _register(table: TableDef) -> None:
    """Register a table definition in the global registry."""
    TABLES[table.name] = table
    TABLE_OWNERS.setdefault(table.name, KERNEL_TABLE_OWNER)


def register_table(spec: "TableSpec", owner: str) -> TableDef:
    """Register a plugin's ``TableSpec`` (contract data) as a ``TableDef``.

    Enforces the ``ext_<owner>_`` prefix for non-builtin owners, refuses to
    re-register a name under a different owner, and is idempotent for the same
    owner (the loader may run more than once in a process, e.g. tests).
    """
    from narranexus.contracts.table import TableSpec  # noqa: PLC0415 — type check only

    if not isinstance(spec, TableSpec):
        raise TypeError(f"register_table expects a contracts TableSpec, got {type(spec).__name__}")
    existing_owner = TABLE_OWNERS.get(spec.name)
    if existing_owner is not None and existing_owner != owner:
        raise ValueError(f"table {spec.name!r} is already registered by {existing_owner!r}")
    spec.check_owner(owner)
    table = TableDef(
        name=spec.name,
        columns=[
            Column(
                name=c.name,
                sqlite_type=c.sqlite_type,
                mysql_type=c.mysql_type,
                nullable=c.nullable,
                default=c.default,
                primary_key=c.primary_key,
                auto_increment=c.auto_increment,
                unique=c.unique,
            )
            for c in spec.columns
        ],
        indexes=[Index(name=i.name, columns=list(i.columns), unique=i.unique) for i in spec.indexes],
        primary_key=list(spec.primary_key) if spec.primary_key else None,
    )
    TABLES[table.name] = table
    TABLE_OWNERS[table.name] = owner
    return table


def tables_owned_by(owner: str) -> List[str]:
    return sorted(name for name, o in TABLE_OWNERS.items() if o == owner)


def get_registered_tables() -> List[TableDef]:
    """Return all registered table definitions."""
    return list(TABLES.values())


_VARCHAR_RE = re.compile(r"VARCHAR\((\d+)\)", re.IGNORECASE)


def varchar_width(table: str, column: str) -> int:
    """Return the declared ``VARCHAR(N)`` width of a column.

    The schema registry is the single source of truth for column widths, so a
    caller that must clip an attacker-influenced field to its column width — the
    gateway-key-misuse endpoint clips every field it stores, and treats an
    over-width ``user_id`` as unresolvable — derives its limit from here instead
    of hardcoding a number that could silently drift from the DDL. Raises if the
    column is not a plain ``VARCHAR(N)`` so a wrong lookup fails loudly at import
    rather than clipping to a bogus width.
    """
    col = next((c for c in TABLES[table].columns if c.name == column), None)
    if col is None:
        raise KeyError(f"{table} has no column {column!r}")
    m = _VARCHAR_RE.fullmatch(col.mysql_type.strip())
    if m is None:
        raise ValueError(
            f"{table}.{column} is {col.mysql_type!r}, not a VARCHAR(N) column"
        )
    return int(m.group(1))


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
            # When the owner asked for this run to stop. NULL = no request.
            #
            # A TIMESTAMP rather than a boolean, because the flag has to answer
            # three questions and only a timestamp answers all three: is a stop
            # pending (non-NULL), is a repeated click a no-op (idempotent write),
            # and does this request apply to THIS run (`started_at` earlier than
            # the request — a later run must not inherit an older stop).
            #
            # Written by the cancel endpoint, read by CancelWatcher (which owns
            # the token) and by `sweep_stale_runs` (a stop in flight settles as
            # 'cancelled', never 'failed').
            Column("cancel_requested_at", "TEXT", "DATETIME(6)"),
            # The trigger TREE this run belongs to: a root run stores its own
            # event_id, and every run it causes (directly or transitively)
            # inherits the same value.
            #
            # A flat inherited LABEL, deliberately not a parent pointer. The
            # only question asked of it is "which runs belong to the tree the
            # owner is stopping", which a label answers in one indexed query;
            # parent/child edges would additionally let you WALK the tree,
            # which nothing here needs (a collaboration graph would — that is
            # a different feature).
            Column("root_run_id", "TEXT", "VARCHAR(128)"),
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
            # "does this user have a live run?" — the reaper's cross-process
            # veto, asked once per candidate per pass. Without the composite,
            # MySQL picks idx_events_user_id and row-filters on state, so a
            # long-tenured user pays for their whole history — worst for the
            # heaviest accounts, and growing forever since events is never
            # pruned.
            Index("idx_events_user_state", ["user_id", "state"]),
            # Cascade stop's only hot path: "every still-running run in this
            # tree". Composite because the flag write always filters on both.
            Index("idx_events_root_state", ["root_run_id", "state"]),
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
            # JSON object {header_name: value}; holds secrets (Authorization
            # bearer tokens) — NEVER shipped in bundle exports, masked in API.
            Column("headers", "TEXT", "MEDIUMTEXT"),
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
            # WHERE this job was asked for, so its answer can go back there.
            #
            # Empty = the owner's chat, which is both the historical behaviour
            # and the only surface that always exists — so the fallback needs
            # no special case. Populated (currently `message_bus` + a team room
            # channel) makes the result land in the room that asked, which is
            # PR #230's "the reply follows its origin" applied to jobs.
            #
            # Two columns rather than one opaque string: the source picks the
            # delivery code path, the channel is its argument, and a single
            # "message_bus:ch_x" field would have every reader re-parsing it.
            Column("origin_source", "TEXT", "VARCHAR(32)"),
            Column("origin_channel_id", "TEXT", "VARCHAR(64)"),
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

# 11b. instance_agent_circuit_breaker
# Real-time-layer Agent circuit-breaker state (2026-07-13). Independent table
# keyed by agent_id — NOT columns on `agents`. Records consecutive real-time
# turn failures so a broken agent (dead key / exhausted balance) stops being
# re-triggered. auto_migrate is additive; this lands as a fresh table.
# (铁律 #14/#15: only FAILED turns accrue here; it gates SCHEDULING of new
# turns, never caps or cancels a running agent_loop.)
_register(
    TableDef(
        name="instance_agent_circuit_breaker",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("cb_status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("consecutive_failure_count", "INTEGER", "INT", nullable=False, default="0"),
            # The category the current failure streak belongs to (auth / quota
            # / transient / business). A category change resets the streak, so
            # "3 consecutive auth failures" cannot be diluted by a transient
            # blip. NULL when there is no active streak.
            Column("failure_category", "TEXT", "VARCHAR(32)"),
            Column("cooldown_until", "TEXT", "DATETIME(6)"),
            Column("paused_reason", "TEXT", "VARCHAR(32)"),
            Column("paused_at", "TEXT", "DATETIME(6)"),
            Column("last_error", "TEXT", "TEXT"),  # already redacted at write time
            # 2026-09-09 (GitHub #117 review, C2): the half-open probe claim's
            # real compare-and-swap key. Equality-filtering on cb_status alone
            # is not a CAS when the claim's own WRITE value equals the FROM
            # value (the stale-PROBING self-heal branch writes cb_status=
            # "probing" while reading cb_status=="probing" — the filter is
            # unconditionally true after the first writer, so every later
            # racer also matches). probe_token changes on every successful
            # claim (fresh random value) and is reset to NULL whenever the row
            # is written back to PAUSED/ACTIVE/COOLING, so it is always a
            # value that differs before vs. after a real claim — additive,
            # nullable, no backfill needed.
            Column("probe_token", "TEXT", "VARCHAR(64)"),
            # 2026-09-10 (#394 review I-1): the claimant's own run
            # (events.event_id), bound once its run row exists; NULL together
            # with probe_token. The crash-window fallback asks whether THIS
            # run is alive, never whether any run of the agent is.
            # Additive, nullable, no backfill.
            Column("probe_run_id", "TEXT", "VARCHAR(128)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_agent_cb_agent_id", ["agent_id"], unique=True),
            Index("idx_agent_cb_status", ["cb_status"]),
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
            # Owner attribution captured at write time. Nullable: background /
            # non-user LLM calls (memory consolidation, no auth context) leave
            # it NULL. VARCHAR(128) matches user_quotas.user_id so the two
            # tables join without truncation. Before this column, the only way
            # to attribute a cost row to a user was cost_records.agent_id ->
            # agents.created_by, which breaks the moment the agent is hard
            # deleted (see backfill_cost_records_user_id.py).
            Column("user_id", "TEXT", "VARCHAR(128)"),
            # Which provider branch served the call ("system" free-tier vs the
            # user's own key). Was only ever a ContextVar (api_config.py);
            # persisting it here makes billing auditable.
            Column("provider_source", "TEXT", "VARCHAR(32)"),
            # Exact source of the provider card selected for this call
            # (e.g. netmind_free vs netmind/byok). provider_source above is
            # only the legacy resolver branch and is always "user" now.
            Column("provider_card_source", "TEXT", "VARCHAR(64)"),
            # Prompt-cache telemetry (2026-07-23). input_tokens alone cannot
            # distinguish full-price tokens from cache writes (1.25x) and cache
            # reads (0.1x), so cache effectiveness was unmeasurable. Summed
            # across all model calls within one run. 0 = provider reported no
            # cache activity (also the value for providers without cache
            # fields in usage).
            Column("cache_read_input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("cache_creation_input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            # Model calls the CLI made during this run (ResultMessage.num_turns).
            # Nullable on purpose: NULL = the framework did not report it
            # (non-Claude CLI paths), distinct from a reported 0.
            Column("num_turns", "INTEGER", "INT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_cost_agent_id", ["agent_id"]),
            Index("idx_cost_created_at", ["created_at"]),
            Index("idx_cost_call_type", ["call_type"]),
            Index("idx_cost_records_user_id", ["user_id"]),
            # Per-turn lookup. chat_history._build_event_meta runs
            # `WHERE event_id = ?` every time a run card is expanded, and the
            # chat panel now does it per turn — without this the ledger is
            # scanned end to end for a handful of rows.
            Index("idx_cost_event_id", ["event_id"]),
        ],
    )
)

# 16b. product_analytics_events
# First-party product facts queried by narranexus-data. Local and desktop
# surfaces keep the same facts in their local SQLite database; no external
# telemetry sink receives them.
_register(
    TableDef(
        name="product_analytics_events",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("analytics_event_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("event_name", "TEXT", "VARCHAR(64)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("source", "TEXT", "VARCHAR(16)", nullable=False, default="'backend'"),
            Column("surface", "TEXT", "VARCHAR(32)"),
            Column("session_id", "TEXT", "VARCHAR(128)"),
            Column("run_id", "TEXT", "VARCHAR(128)"),
            Column("agent_id", "TEXT", "VARCHAR(128)"),
            Column("trigger_source", "TEXT", "VARCHAR(64)"),
            Column("provider_card_source", "TEXT", "VARCHAR(64)"),
            Column("failure_category", "TEXT", "VARCHAR(32)"),
            Column("failure_reason", "TEXT", "VARCHAR(64)"),
            Column("latency_ms", "INTEGER", "BIGINT"),
            Column("properties_json", "TEXT", "MEDIUMTEXT"),
            Column("occurred_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_product_analytics_event_id", ["analytics_event_id"], unique=True),
            Index("idx_product_analytics_event_time", ["event_name", "occurred_at"]),
            Index("idx_product_analytics_user_time", ["user_id", "occurred_at"]),
            Index("idx_product_analytics_run_event", ["run_id", "event_name"]),
            Index("idx_product_analytics_failure_time", ["failure_category", "occurred_at"]),
        ],
    )
)

# NOTE (2026-07-29): `agent_cli_sessions` was registered here (as #17) — one
# row per (agent_id, platform_session_id, framework) holding a resumable CLI
# session handle. The claude adapter now authors the CLI transcript itself
# every turn and deletes it when the turn ends, so there is no handle to
# store. The mechanism never shipped beyond its feature branch.
#
# `auto_migrate()` only creates and adds — it never drops — so a dev database
# that already ran the old code keeps an empty orphan table. Left alone
# deliberately: an unused empty table is harmless, whereas committing a DROP
# would put a destructive migration in the repo (铁律 #6). Drop it by hand if
# it bothers you.


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

# 21b. bus_wake — the cross-process "there is new work" nudge
#
# ONE row (id=1) whose timestamp every send bumps and the poll loop reads while
# it sleeps. Not a queue: the signal says "look now", never "look at X" — the
# loop already knows how to find pending work, and a second answer to that
# question is a second thing to keep true.
#
# Why a table at all: the in-process `asyncio.Event` in MessageBusTrigger cannot
# reach the MCP server process, and a team reply is now a tool call made there.
# See `message_bus/wake_signal.py` for the full argument.
_register(
    TableDef(
        name="bus_wake",
        columns=[
            Column("id", "INTEGER", "INT", nullable=False, primary_key=True),
            Column("bumped_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
    )
)

# 21c. steer_inbox — live-steering injections destined for a running turn
#
# One uniform inbox behind "append to the running loop": a producer (team room
# post — already in bus_messages; owner-chat interjection — NOT a bus message)
# writes one row keyed by an OPAQUE run handle; the transport drains a run's
# unconsumed rows into its SteeringInlet at the next step boundary. The table
# earns its place by DECOUPLING (the feeder drains one store, not each
# producer's native home — same outbox pattern as instance_artifact_events),
# NOT by re-persisting team messages the bus already keeps.
#
# `id` is the arrival order AND the consume cursor's unit. `(run_id, msg_id)` is
# unique so a re-delivered message injects at most once. `consumed_at` is a
# per-RUN cursor the bus's per-(agent,channel) cursor cannot lend (it is the
# trigger's, and one agent may have several concurrent runs).
# `(run_id, consumed_at)` is the pull-unconsumed access path. See
# `schema/steer_schema.py` and `repository/steer_inbox_repository.py`.
_register(
    TableDef(
        name="steer_inbox",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("run_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("msg_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("role", "TEXT", "VARCHAR(16)", nullable=False, default="'user'"),
            Column("content", "TEXT", "MEDIUMTEXT", nullable=False),
            Column("sender_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("source", "TEXT", "VARCHAR(16)", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("consumed_at", "TEXT", "DATETIME(6)", nullable=True),
        ],
        indexes=[
            Index("idx_steer_inbox_run_msg", ["run_id", "msg_id"], unique=True),
            Index("idx_steer_inbox_run_consumed", ["run_id", "consumed_at"]),
            # The daily retention sweep (cleanup_older_than_days) filters both
            # arms on created_at; without this it is a full table scan on MySQL.
            Index("idx_steer_inbox_created", ["created_at"]),
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
            # Why this message's `mentions` are what they are. NULL = the sender
            # typed them. "default_responder" = a team room had none and the
            # route picked the fallback agent so the room would not go silent.
            # Recorded at the point of the decision because nothing downstream
            # can reconstruct it: "one mention, and it is the lead" is exactly
            # what a user deliberately naming the lead looks like.
            Column("routed_by", "TEXT", "VARCHAR(32)", nullable=True),
            # JSON list of bus-attachment dicts (file_id/original_name/mime_type/
            # size_bytes/category/rel_path). rel_path is base_working_path-relative
            # and points into the per-user shared area; markers are built from it
            # at delivery time. NULL for text-only messages. See _bus_attachment_impl.
            Column("attachments", "TEXT", "TEXT", nullable=True),
            # JSON list of {kind: "monologue"|"reply", text} preserving the
            # boundary between an agent's own thinking and its answer, which
            # `content` loses by concatenation. NULL for every message written
            # before this existed and for every path that has no monologue —
            # the reader renders those as one block, which is what it did
            # before (iron rule #2: no backfill, no shim). `content` is
            # unchanged and remains what text consumers read.
            Column("segments", "TEXT", "MEDIUMTEXT", nullable=True),
            # Which KIND of turn produced this message: an owner-facing turn
            # ("chat", "job", …) means the sender was running an errand for
            # its owner, so this message is a QUESTION; "message_bus" means
            # the sender was itself answering a peer, so this is a REPLY.
            # MessageBusTrigger picks the recipient's directive from it —
            # inferring it from channel ordering is impossible, because DM
            # channels are found symmetrically and REUSED, so "who opened the
            # channel" is fixed forever while errands run in both directions
            # (P1 2026-08-03 review). NULL on legacy rows / senders that
            # predate this column; the trigger falls back then.
            Column("sender_turn_source", "TEXT", "VARCHAR(32)", nullable=True),
            # events row id of the turn that produced this message. Stamped by
            # BOTH paths an agent reply can take: the trigger's own in-turn
            # room post, and the agent's `message_team` /
            # `message_agent` (from the identity header, 2026-08-14) —
            # so this is NOT a marker of "the platform posted it". NULL for
            # user messages, for rows written before the column existed, and
            # whenever the sender could not tell which turn it was in. Powers
            # the per-message "view reasoning & tools" disclosure in the team
            # transcript, and the team room's "did this agent say anything
            # here during that turn?" check.
            Column("event_id", "TEXT", "VARCHAR(128)", nullable=True),
            # The trigger TREE the sending run belonged to (events.root_run_id).
            # Carries the lineage across the one hop where it would otherwise
            # be lost: an agent asking a peer writes a NEW message, and the run
            # that message wakes up has no other way to know which tree it
            # continues. NULL for user messages and pre-column rows — the
            # cascade treats NULL as "not part of any tree being stopped".
            Column("root_run_id", "TEXT", "VARCHAR(128)", nullable=True),
            # A long message sent in ordered parts (2026-09-09): 1-based index,
            # total, and the group id (= the first part's message_id). NULL on
            # every ordinary row. `content` is TEXT (64 KiB on MySQL), and a
            # model's own output-token budget cuts a tool call long before
            # that — so a reply that does not fit one call travels as parts and
            # the recipient's trigger reassembles them (multipart.py). Never a
            # truncation, in either direction (iron rule #16).
            Column("part_index", "INTEGER", "INT", nullable=True),
            Column("part_count", "INTEGER", "INT", nullable=True),
            Column("part_group", "TEXT", "VARCHAR(64)", nullable=True),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_bus_msg_channel_time", ["channel_id", "created_at"]),
            # `_resolve_part_group`: the sender's newest part in this channel
            # (ORDER BY created_at DESC LIMIT 1) and the group's rows; without
            # it a long-lived DM scans its whole history on every part >= 2.
            Index("idx_bus_msg_sender_time", ["channel_id", "from_agent", "created_at"]),
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

# 23b. bus_agent_activity — live "what is this agent doing" for a channel.
# Written by MessageBusTrigger while it runs a team-room agent so the team
# chat UI can show running / phase / elapsed / steps. One row per (agent_id,
# channel_id); state flips to 'idle' when the turn ends. This is NOT the events
# pipeline; it's a lightweight status mirror.
# `updated_at` is refreshed by a timer for as long as the turn runs, so a stale
# 'running' row means the writer is gone rather than "the model went quiet".
# `steps` is a capped JSON list of the CURRENT turn's phase transitions (reset
# when the turn starts, kept after it ends) so the team chat can render a
# per-agent step timeline without standing up a second events pipeline.
_register(
    TableDef(
        name="bus_agent_activity",
        columns=[
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("state", "TEXT", "VARCHAR(16)", nullable=False, default="'idle'"),
            Column("phase", "TEXT", "VARCHAR(64)"),
            Column("tool_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("steps", "TEXT", "MEDIUMTEXT"),
            Column("event_id", "TEXT", "VARCHAR(128)"),
            Column("started_at", "TEXT", "DATETIME(6)"),
            Column("updated_at", "TEXT", "DATETIME(6)"),
        ],
        primary_key=["agent_id", "channel_id"],
        indexes=[
            Index("idx_bus_activity_channel", ["channel_id"]),
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
            # --- Provider Unification (2026-05-13) ---
            #
            # driver_type    : key into agent_framework.providers.driver.driver_registry().
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
            # 2026-07-16: NetMind account identity captured at key-mint time
            # (verify_token → user_system_code + email). Lets Settings show WHICH
            # account each key belongs to, so a user with several keys from one
            # broke account tops up the right one (upstream incident). Additive,
            # nullable — non-NetMind rows and pre-existing rows stay NULL.
            Column("netmind_account_id", "TEXT", "VARCHAR(64)"),
            Column("netmind_account_email", "TEXT", "VARCHAR(255)"),
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
                default="'nexus_power'",
            ),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_us_user_slot", ["user_id", "slot_name"], unique=True),
        ],
    )
)

_register(
    TableDef(
        name="agent_capabilities",
        # Plugin platform batch 5c: the owner's per-agent capability switches.
        # One row per (agent, module class) the owner touched; no row = the
        # default rule (builtin on, plugin-installed off — context budget).
        # Read by ModuleLoader every turn, written by /api/agents/{id}/capabilities.
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("capability", "TEXT", "VARCHAR(128)", nullable=False),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_agent_capabilities_agent_cap", ["agent_id", "capability"], unique=True)],
    )
)

# 25b. agent_slots (per-agent slot OVERRIDES; falls back to user_slots)
# Mirrors user_slots column-for-column but keyed by agent_id instead of
# user_id. A row here overrides the owner's user_slots row for that slot on
# runs of THIS agent only; absence = inherit the user-level default. Both
# 'agent' and 'helper_llm' slots may be overridden (helper follows its agent).
# The identical column vocabulary is deliberate: resolve_user_runtime_llm_configs
# overlays an agent_slots row onto by_slot_name and consumes it with the exact
# same card-lookup / self-heal / driver-dispatch code — no special-casing.
_register(
    TableDef(
        name="agent_slots",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("slot_name", "TEXT", "VARCHAR(32)", nullable=False),
            Column("provider_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("model", "TEXT", "VARCHAR(128)", nullable=False),
            # Same framework-neutral per-slot JSON blob as user_slots
            # (thinking, reasoning_effort, future knobs). NULL = all auto.
            Column("params_json", "TEXT", "MEDIUMTEXT"),
            # self_heal_if_broken writes here when an overridden slot.model
            # drifts out of its provider.models array — the writeback is
            # table-aware so it repairs the OVERRIDE, not the user default.
            Column("last_auto_repaired_at", "TEXT", "DATETIME(6)"),
            # Coding-agent framework override — only meaningful on the
            # slot_name='agent' row; null falls back to the user default.
            Column(
                "agent_framework",
                "TEXT",
                "VARCHAR(32)",
                nullable=True,
                default="'nexus_power'",
            ),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_as_agent_slot", ["agent_id", "slot_name"], unique=True),
            Index("idx_as_agent_id", ["agent_id"]),
        ],
    )
)

# 25c. agent_slot_clear_audit (append-only trail for bulk clear-to-inherit)
# The owner-level "apply default to all agents" flow deletes agent_slots rows,
# which is IRREVERSIBLE (provider_id/model/params/framework are gone). Before
# each delete we snapshot the row here so a mistaken bulk clear is recoverable
# and answerable ("which agents, what were they set to"). DB trail, not just a
# log line (CLAUDE.md lesson #5: docker logs rotate on restart, DB rows don't).
_register(
    TableDef(
        name="agent_slot_clear_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("slot_name", "TEXT", "VARCHAR(32)", nullable=False),
            # Snapshot of the deleted override row (what it was set to).
            Column("provider_id", "TEXT", "VARCHAR(64)"),
            Column("model", "TEXT", "VARCHAR(128)"),
            Column("agent_framework", "TEXT", "VARCHAR(32)"),
            Column("params_json", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_asca_user", ["user_id"]),
            Index("idx_asca_agent", ["agent_id"]),
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


# 26b. owner_notice_cooldowns — "when did we last tell this owner about THIS
# thing" for the owner-facing SYSTEM_NOTICE writers.
#
# Until 2026-09-09 the bus trigger kept this as an in-process dict keyed by
# (agent_id, error category): a restart forgot every window (the next poll
# re-notified), two trigger processes each kept their own (double notices),
# and one key covered EVERY channel of the agent — a permanent failure on
# channel A silenced the notice for an unrelated failure on channel B for the
# whole window. One row per (agent, target, category) fixes all three; the
# window itself stays a constant on the writer, this table only remembers the
# last write. `target` is the channel_id for the bus writers and is left
# generic so any owner-notice path can share the row shape.
_register(
    TableDef(
        name="owner_notice_cooldowns",
        columns=[
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("target", "TEXT", "VARCHAR(64)", nullable=False),
            Column("category", "TEXT", "VARCHAR(32)", nullable=False),
            Column("last_notified_at", "TEXT", "DATETIME(6)", nullable=False),
        ],
        primary_key=["agent_id", "target", "category"],
        indexes=[],
    )
)


# 26c. bus_delivery_receipts — what became of ONE bus message at ONE recipient.
#
# The bus's "send success" only ever meant "row inserted" (upstream
# NetMindAI-Open/NarraNexus#106: the PM agent told its user "build is now in
# progress" while the Web Developer's worker had crashed three times and the
# only trace was an unread owner-inbox row). This table is the delivery-side
# ledger the sender can be shown: `accepted` / `held` at send time (the send
# tool's pre-flight), then `processed` / `relayed` / `silent` / `failed` /
# `dropped` as the recipient's trigger runs the message. One row per
# (message_id, to_agent), updated in place — a receipt, not an event log
# (`bus_message_failures` keeps the retry count; `service_audit` the lifecycle).
# `content_key` (sha256 of the batch the recipient saw) is what lets the
# trigger recognise "this silence is a RESEND of the same message" and stop
# waking the sender for it a second time.
_register(
    TableDef(
        name="bus_delivery_receipts",
        columns=[
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("to_agent", "TEXT", "VARCHAR(64)", nullable=False),
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("from_agent", "TEXT", "VARCHAR(64)", nullable=False),
            Column("status", "TEXT", "VARCHAR(24)", nullable=False),
            # Redacted at write time (redact_secrets); NULL when there is none.
            Column("reason", "TEXT", "TEXT", nullable=True),
            Column("attempts", "INTEGER", "INT", nullable=False, default="0"),
            Column("content_key", "TEXT", "VARCHAR(64)", nullable=True),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        primary_key=["message_id", "to_agent"],
        indexes=[
            # "Has this recipient already gone silent on this exact content in
            # this channel" — the resend-loop guard's lookup.
            Index("idx_bus_receipt_content", ["channel_id", "to_agent", "content_key"]),
        ],
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
            # The bot's OWN open_id, from /open-apis/bot/v3/info. Needed to
            # decide whether an inbound group message @-mentions this bot:
            # the mention list carries open_ids, and name matching alone is
            # ambiguous when a human shares the bot's display name.
            Column("bot_open_id", "TEXT", "VARCHAR(64)"),
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
        name="channel_webhook_events",
        # Plugin platform batch 4c: the webhook transport's inbox. The backend
        # API process receives a channel's webhook and appends the raw event
        # here; the channel trigger (workers process) pulls and claims rows in
        # order. A DB-backed queue on purpose — one host today, and the seam a
        # broker can replace later (rule #20).
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("channel", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("payload_json", "TEXT", "MEDIUMTEXT", nullable=False),
            Column("received_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("claimed_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            Index("idx_channel_webhook_pending", ["channel", "agent_id", "claimed_at"]),
        ],
    )
)


_register(
    TableDef(
        name="channel_credentials",
        # Plugin platform batch 4b: the ONE credential table every IM channel
        # (builtin or plugin) is served from. A row is one (channel, agent)
        # binding; identity fields live in public_json, secrets encrypted in
        # secret_json (channel/credential_codec.py), split by the channel's
        # CredentialSchema. The six per-channel tables keep working during the
        # 4d switch (channel/credential_legacy.py copies the old tables in once)
        # and are retired, never dropped (rule #6), when reads switch (4d).
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("channel", "TEXT", "VARCHAR(64)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            # The channel-wide unique external identity (bot user id, app id) — one bot, one agent.
            Column("external_id", "TEXT", "VARCHAR(255)"),
            Column("public_json", "TEXT", "MEDIUMTEXT", nullable=False, default="'{}'"),
            Column("secret_json", "TEXT", "MEDIUMTEXT", nullable=False, default="''"),
            # Optimistic concurrency for field-level patches: a writer re-reads
            # when the version moved under it (GenericCredentialStore.patch).
            Column("version", "INTEGER", "BIGINT", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_channel_cred_channel_agent", ["channel", "agent_id"], unique=True),
            Index("idx_channel_cred_external", ["channel", "external_id"], unique=True),
        ],
    )
)


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
# NarraMessenger per-agent binding. Two transports coexist during the
# 2026-07-02 migration:
#
#   connection_mode = 'gateway' (legacy) — Gateway Polling + /chat/send;
#     only secret is the bearer token (`bearer_token_encoded`).
#   connection_mode = 'matrix'  (new)    — Direct Matrix client via matrix-nio
#     against matrix.netmind.chat. Secret is `matrix_access_token_encoded`;
#     `bearer_token_encoded` is still populated because the bind / control
#     plane (fetch_setup_guide, report_profile, runtime-ready) still talks
#     to api.netmind.chat with the NM bearer.
#
# matrix_since_token is the /sync cursor. It is persisted on every sync
# tick so a NarraNexus restart doesn't force a fresh initial sync of every
# joined room (which for busy owners can be MB of state). matrix_device_id
# pins the same server-side device on reconnect — otherwise every restart
# spawns a new device entry for the same account.
#
# matrix_user_id is the bot identity; one identity binds to AT MOST one
# agent (invariant unchanged from gateway era).
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
            # ── Matrix transport columns (added 2026-07-02 for MatrixTrigger) ─
            # matrix_access_token_encoded — base64-encoded Matrix access
            # token (syt_...), identical convention to bearer_token_encoded.
            # NULL/empty on legacy 'gateway' rows.
            Column("matrix_access_token_encoded", "TEXT", "VARCHAR(512)"),
            # matrix_device_id — the device the token is bound to. First
            # sync auto-registers a device if empty; we persist it back so
            # subsequent syncs re-use it.
            Column("matrix_device_id", "TEXT", "VARCHAR(64)"),
            # matrix_since_token — opaque /sync cursor. High-frequency
            # write on every sync tick; use `update_since_token()` for the
            # single-column touch, don't round-trip the whole row.
            Column("matrix_since_token", "TEXT", "VARCHAR(256)"),
            # ─────────────────────────────────────────────────────────────
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_nm_cred_agent_id", ["agent_id"], unique=True),
            # Same Matrix bot identity binds to AT MOST one agent — two agents
            # polling the same bearer would split invocations arbitrarily.
            Index("idx_nm_cred_matrix_user", ["matrix_user_id"], unique=True),
            # MatrixTrigger's credential watcher filters by connection_mode;
            # indexed so N > 1000 agents don't scan the whole table each tick.
            Index("idx_nm_cred_conn_mode_enabled", ["connection_mode", "enabled"]),
            # Reverse lookup for the prewarm endpoint (NarraMessenger calls
            # in with its own agent_profile_id). Non-unique: only rows bound
            # after 2026-08-11 carry a profile id (do_bind started
            # persisting it); older rows still have it empty.
            Index("idx_nm_cred_profile", ["nexus_profile_id"]),
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


# 28b. quota_deductions — per-deduction audit ledger for the free-tier quota.
#
# user_quotas holds only cumulative scalars (used_input/output_tokens), so a
# single wrong charge could never be isolated or refunded — the only remedy
# was a platform-wide reset. The retired quota repository wrote one row
# here per deduction. It is NOT wrapped in a DB transaction with the user_quotas
# UPDATE (the client's transaction() is single-connection and unsafe on this
# concurrent hot path — see atomic_deduct's docstring): the UPDATE runs first as
# the spend-control invariant, the ledger row follows best-effort. So ledger and
# total are reconcilable, not transactionally identical — `used_* >= SUM(rows)`,
# with a hard-crash-between-writes gap at most. Rows are self-auditing:
# provider_source / model / agent_id are stored redundantly so a ledger entry
# stands on its own even if the linked cost_records row is missing (insert
# failed -> cost_record_id NULL) or later purged. Sum a user's rows to compute
# an exact refund instead of resetting everyone.
_register(
    TableDef(
        name="quota_deductions",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("input_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            Column("output_tokens", "INTEGER", "BIGINT UNSIGNED", nullable=False, default="0"),
            # Link to the cost_records row that triggered this deduction.
            # Nullable: if the cost_records insert failed, the deduction still
            # happened and must still be recorded — just without the link.
            Column("cost_record_id", "INTEGER", "BIGINT UNSIGNED"),
            # Redundant self-audit columns (see table comment).
            Column("provider_source", "TEXT", "VARCHAR(32)"),
            Column("model", "TEXT", "VARCHAR(128)"),
            Column("agent_id", "TEXT", "VARCHAR(64)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_quota_deductions_user", ["user_id"]),
            Index("idx_quota_deductions_created", ["created_at"]),
            Index("idx_quota_deductions_cost_record", ["cost_record_id"]),
        ],
    )
)


# 28c. instance_gateway_session_keys — per-run LiteLLM gateway session keys.
#
# Free-tier runs no longer inject the shared master key into the agent
# subprocess env; each run mints one short-lived gateway key (the "会话票") that
# is injected in its place and revoked when the run ends. This table is the
# ledger that makes revocation and orphan-reaping (crash between mint and
# revoke) possible. run_id doubles as the gateway key_alias — the only handle
# needed to revoke — so we never persist the raw usable secret here; key_hash is
# LiteLLM's non-secret token hash, audit-only. See
# the retired GatewayKeyService.
_register(
    TableDef(
        name="instance_gateway_session_keys",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("run_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(64)"),
            # LiteLLM's returned token HASH (not the secret). Audit/reconcile only.
            Column("key_hash", "TEXT", "VARCHAR(256)"),
            Column("status", "TEXT", "VARCHAR(32)", nullable=False, default="'active'"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("revoked_at", "TEXT", "DATETIME(6)"),
            # Set once the run's LiteLLM SpendLogs have been summed and deducted
            # from the user's free-tier quota (retired reconciler). NULL =
            # not yet metered; the idempotency guard that stops double-charging.
            Column("metered_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            Index("idx_gateway_session_keys_run", ["run_id"], unique=True),
            Index("idx_gateway_session_keys_status", ["status", "created_at"]),
            Index("idx_gateway_session_keys_user", ["user_id"]),
            # Reconciler scans revoked-but-unmetered runs by this pair.
            Index("idx_gateway_session_keys_metered", ["status", "metered_at"]),
        ],
    )
)


# 28d. gateway_key_misuse — records of abnormal / unauthorized use of a gateway
# key, for security monitoring.
#
# Single writer: the backend admin gateway-key-misuse endpoint, which persists
# what the caller AUTHORITATIVELY reverse-resolved for the offending key (the
# gateway is the authority on which identity the key is bound to) — never
# anything an attacker controls. This endpoint records ONLY the fields it is
# handed; it parses no free-form text. The security monitor reads this table
# read-only and drives its response ladder off it. executor/agent have no
# credential to write here (admin-secret gated, same lock as suspend).
#
# user_id is nullable on purpose: an unresolved event (upstream hiccup / key
# gone) is recorded as an ALERT-ONLY row with user_id=NULL — a human triages it;
# the ladder never acts on a NULL id, so we never fabricate an attributable id.
#
# (key_hash, hit_at) is UNIQUE for idempotency: when the caller supplies the
# authoritative event time, a write-succeeded-but-response-timed-out retry of the
# same event collapses to the same row instead of a duplicate the response ladder
# would act on twice. A NULL key_hash (unresolved event) does not participate —
# SQL uniqueness treats NULLs as distinct, so alert-only rows never false-dedup.
_register(
    TableDef(
        name="gateway_key_misuse",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("user_id", "TEXT", "VARCHAR(64)"),  # matches users.user_id VARCHAR(64); nullable: unresolved event → alert-only row, never actioned
            Column("run_id", "TEXT", "VARCHAR(128)"),
            Column("key_hash", "TEXT", "VARCHAR(256)"),
            Column("caller_ip", "TEXT", "VARCHAR(64)"),
            Column("caller_ua", "TEXT", "VARCHAR(256)"),
            Column("model", "TEXT", "VARCHAR(128)"),
            Column("hit_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("disposition_status", "TEXT", "VARCHAR(32)", nullable=False, default="'pending'"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_gateway_key_misuse_user", ["user_id"]),
            Index("idx_gateway_key_misuse_status", ["disposition_status", "created_at"]),
            Index("idx_gateway_key_misuse_dedup", ["key_hash", "hit_at"], unique=True),
        ],
    )
)


# ----------------------------------------------------------------------------
# 29. user_notifications — out-of-band messages to surface in UI
#
# Introduced by the Provider Unification work (2026-05-13).
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
# Introduced by the Agent Runtime Lifecycle work (2026-05-13).
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
#   * `kind` is small and bounded — VARCHAR(32). The longest value today
#     is `thinking_segment_monologue` (26), leaving 6 characters. Mind
#     that gap: a longer kind passes every local test, because SQLite
#     ignores the declared length, and fails only on MySQL (error 1406).
#     Count the characters before adding one.
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
# 31 / 32. inbox_threads + inbox_thread_messages — the RECORD layer.
#
# NAMED AROUND AN EXISTING COLLISION: `inbox_table` / `InboxRepository` /
# `InboxMessage` already exist and are a NOTIFICATION store (system alerts the
# platform pushes to an owner). Different feature, same word. `inbox_messages`
# would have read as that feature's table, so the conversation record carries
# `inbox_thread_messages` — the pairing with `inbox_threads` is then obvious and
# neither name reaches for `InboxMessage`'s. The deeper fix (the notification
# store is the one misusing the word) needs a rename of a live table, so it is
# recorded in todo/ rather than done here.
#
# The inbox is what the user reads about conversations they were not in: IM
# channel traffic and agent-to-agent DMs. It used to live in `bus_messages` /
# `bus_channel_members`, which cost two things measured on prod 2026-08-17:
#
#   * 86% of `bus_messages` (28,605 of 33,164 rows) was IM inbox content. The
#     table's name described its minority.
#   * The writer created a `bus_channel_members` row per agent so the panel
#     could find the thread — and NOTHING ever advanced its `last_read_at`
#     (159 of 172 IM memberships, 92%, had a NULL cursor). The bus unread
#     predicate is `created_at > COALESCE(last_read_at, epoch)`, so 1,364 IM
#     messages were permanently "unread" and rode into 90 agents' turn context
#     attributed to pseudo-agents like `lark_user_<id>`.
#
# Separate tables end both: the agent's unread injection reads `bus_messages`
# JOIN `bus_channel_members`, and these rows are simply not there. The
# containment is structural, not a filter that a future channel can be
# forgotten from — which is exactly how the 2026-07-03 wechat double-dispatch
# happened (`im_channel_prefixes` drifted).
#
# OPERATIONAL vs OBSERVATIONAL is the line: the bus tables carry delivery
# mechanics (cursors, pending work, routing); these carry the record a person
# reads. `inbox_threads.last_read_at` is therefore the USER's read state and
# has no effect on what any agent is handed — the two were one column before,
# so clicking "mark read" in the panel changed the agent's next turn.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="inbox_threads",
        columns=[
            # `im_<channel>_<chat_id>` / `nx_dm_<peer_agent_id>` — the family
            # prefix comes first so the namespace says what it is before it
            # says which one.
            Column("thread_id", "TEXT", "VARCHAR(128)", nullable=False, primary_key=True),
            Column("owner_user_id", "TEXT", "VARCHAR(128)", nullable=False),
            # The agent whose conversation this is. A user may own several.
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            # "lark" / "wechat" / … / "agent_dm". Matches MessageSourceHandler.name
            # for IM sources so the record layer and the registry agree.
            Column("source", "TEXT", "VARCHAR(32)", nullable=False),
            Column("title", "TEXT", "VARCHAR(255)"),
            # Who the agent is talking to: the IM sender id, or the peer agent_id.
            Column("counterpart_id", "TEXT", "VARCHAR(128)"),
            Column("counterpart_name", "TEXT", "VARCHAR(255)"),
            Column("last_message_at", "TEXT", "DATETIME(6)"),
            Column("last_message_preview", "TEXT", "TEXT"),
            # The USER's read state. Deliberately NOT the agent's context gate —
            # see the header note.
            Column("last_read_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            # The panel's query: this user's threads, newest first, every poll.
            Index("idx_inbox_threads_owner", ["owner_user_id", "last_message_at"]),
            Index("idx_inbox_threads_agent", ["agent_id"]),
        ],
    )
)

_register(
    TableDef(
        name="inbox_thread_messages",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("message_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("thread_id", "TEXT", "VARCHAR(128)", nullable=False),
            # "in" = from the counterpart, "out" = from the agent. The pair is
            # one conversational turn but two rows, and they must sort
            # inbound-then-reply — see the writer's microsecond stagger.
            Column("direction", "TEXT", "VARCHAR(8)", nullable=False),
            Column("sender_id", "TEXT", "VARCHAR(128)"),
            Column("sender_name", "TEXT", "VARCHAR(255)"),
            Column("content", "TEXT", "MEDIUMTEXT"),
            # JSON list of attachment dicts, carried verbatim from the source so
            # a backfilled row renders the same as a live one.
            Column("attachments", "TEXT", "TEXT"),
            # The `bus_messages.message_id` this row was backfilled from. NULL
            # for anything written live.
            #
            # This column is where the backfill's idempotency key BELONGS.
            # Decision ③ (2026-08-17): history is backfilled by hand after
            # deploy, so the live write path is already filling this table when
            # the script starts and the overlapping window would otherwise double
            # every message — in a surface the user looks at. Keeping the key in
            # the schema rather than in the script keeps it somewhere it cannot be
            # forgotten.
            #
            # Two things this does NOT yet do, stated plainly because an
            # optimistic comment here is worse than none:
            #
            #   * Nothing populates it. `_insert_message`'s parameter defaults to
            #     None and no caller passes it, so today the unique index below
            #     sits over an all-NULL column (legal in both dialects: multiple
            #     NULLs do not collide). The backfill script is what fills it —
            #     and the script does not exist yet, by the Owner's decision that
            #     the migration is manual and post-deploy.
            #   * A unique index does not make a repeat insert a no-op. It makes
            #     it RAISE. An earlier version of this comment claimed otherwise;
            #     the script has to catch the duplicate and re-read, the same
            #     shape `InboxRecorder._ensure_thread` uses for its create race.
            #     The runbook carries that requirement.
            Column("source_message_id", "TEXT", "VARCHAR(64)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_inbox_thread_msgs_thread", ["thread_id", "created_at"]),
            Index("idx_inbox_thread_msgs_msgid", ["message_id"], unique=True),
            Index("idx_inbox_thread_msgs_source", ["source_message_id"], unique=True),
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


# ban_audit — append-only trail for administrative account-state changes.
# One row per suspend / reinstate action. `reason` and `evidence_ref` are
# OPAQUE free-text supplied by the caller (never an enum) so this table
# carries no policy vocabulary of its own; `actor` records who made the
# change. Append-only, queried by user_id.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="ban_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("action", "TEXT", "VARCHAR(32)", nullable=False),
            Column("reason", "TEXT", "MEDIUMTEXT"),
            Column("evidence_ref", "TEXT", "MEDIUMTEXT"),
            Column("actor", "TEXT", "VARCHAR(128)"),
            # The account state the row was in immediately BEFORE this action.
            # Additive column (auto_migrate backfills it incrementally on
            # existing deployments — no destructive migration). Lets a reinstate
            # record what it reverted from, and lets suspend record what it
            # replaced, without the audit trail carrying any policy vocabulary.
            Column("prev_status", "TEXT", "VARCHAR(32)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_ban_audit_user_id", ["user_id"]),
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
            # Agent that responds to a team-chat message with NO @mention.
            # NULL = fall back to the earliest-joined member. auto_migrate adds
            # this to pre-existing tables. See plugins/builtin.teams/src/narranexus_plugins/teams/routes.py.
            Column("lead_agent_id", "TEXT", "VARCHAR(64)"),
            # --- Leader patrol (2026-08-07) ---
            #
            # Whether the lead periodically checks the work board and chases
            # stalled items. NULL is the un-decided default and reads as ON for
            # a team that HAS a lead — setting a lead is the act of saying "this
            # one is responsible", so patrol follows it rather than asking
            # again. A team with no lead never patrols: the platform does not
            # appoint someone in charge on the user's behalf.
            Column("patrol_enabled", "INTEGER", "TINYINT(1)"),
            Column("last_patrol_at", "TEXT", "DATETIME(6)"),
            # Patrol's own rate limit, DELIBERATELY on disk.
            #
            # The bus already has a per (agent, channel) limiter, but it lives
            # in `MessageBusTrigger._rate_counters` — an in-memory dict keyed on
            # `time.monotonic()`, so a workers restart clears it. That is fine
            # for its original job (dampening a chatty agent) and NOT fine here:
            # patrol messages are exempted from the cascade-depth cap that stops
            # runaway @-loops, so this counter is the only remaining backstop.
            # A backstop that a restart erases is not a backstop.
            Column("patrol_spoke_at", "TEXT", "DATETIME(6)"),
            Column("patrol_spoke_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_teams_team_id", ["team_id"], unique=True),
            Index("idx_teams_owner_user_id", ["owner_user_id"]),
        ],
    )
)

# ---------------------------------------------------------------------------
# Team work board — the first task-level object in the product.
#
# Everything before this recorded CONVERSATION (messages, runs, errands); a
# task existed only inside the turn that happened to be discussing it, so a
# Leader's assignment died with its own run. The board is what lets a task
# outlive the run that created it — and therefore what lets anyone notice it
# stalled.
#
# Layered with, not merged into, the errand object (owner decision 2026-08-07):
# an errand is a MESSAGE-level fact recorded automatically (A @ B opens it, B's
# reply closes it), a work item is a TASK-level object maintained explicitly by
# tools. One work item routinely spans several errands.
# ---------------------------------------------------------------------------
_register(
    TableDef(
        name="team_work_items",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("item_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False),
            # The room this item lives in. Denormalised from team_id on purpose:
            # patrol wakes the lead INTO a channel, and resolving it per item
            # would be one join per candidate on every poll cycle.
            Column("channel_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("title", "TEXT", "TEXT", nullable=False),
            # NULL = unclaimed. Patrol reads this to tell "nobody picked it up"
            # from "somebody has it and went quiet" — two different prompts.
            Column("assignee_id", "TEXT", "VARCHAR(64)"),
            # open | in_progress | stalled | done | paused | cancelled
            #
            # `stalled` is written by the PLATFORM, never by a model: it is
            # derived from bus_agent_activity + errand timeouts (iron rule #15 —
            # a correctness-critical fact must not depend on model obedience).
            # The lead's judgement applies to what to DO about it, not to
            # whether it is true.
            #
            # `paused` is what a stop leaves behind. Stopping a run tree stops
            # the RUNNING, not the task; without this state the item stays
            # "open" and the next patrol dutifully revives exactly what the
            # owner just stopped. Resuming is an explicit user action.
            Column("status", "TEXT", "VARCHAR(16)", nullable=False, default="'open'"),
            Column("created_by", "TEXT", "VARCHAR(64)", nullable=False),
            Column("source_message_id", "TEXT", "VARCHAR(64)"),
            # tool | auto — which layer put this row here (see WorkItemOrigin).
            #
            # Load-bearing, not descriptive: `auto` rows are the message-level
            # errands the platform opens from an @mention, and they are the
            # only ones the platform may close by itself. A `tool` row is a
            # TASK that spans several errands (owner decision 2026-08-07), so
            # auto-closing it would collapse the two layers that decision
            # separated. Defaults to `tool` so pre-existing rows keep their
            # meaning.
            Column("origin", "TEXT", "VARCHAR(16)", nullable=False, default="'tool'"),
            # The trigger tree that was running when this item was created, so a
            # cascade stop can pause the items it silenced. Shipped by #252.
            Column("root_run_id", "TEXT", "VARCHAR(128)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_work_items_item_id", ["item_id"], unique=True),
            # Patrol's only hot path: "does this team have anything unfinished".
            Index("idx_work_items_team_status", ["team_id", "status"]),
            # Stop → pause, by tree.
            Index("idx_work_items_root", ["root_run_id"]),
            # The errand layer's two hot reads, both per-ROOM rather than per-
            # team: an agent belongs to several teams, and speaking in one room
            # must not settle what it owes in another.
            Index("idx_work_items_channel_assignee", ["channel_id", "assignee_id"]),
            Index("idx_work_items_source_msg", ["source_message_id"]),
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

# ----------------------------------------------------------------------------
# channel_ingress_breaker — message-ingress circuit breaker (2026-08-24)
#
# One row per session key `agent_id|channel|chat_id|sender_id`. Written ONLY on tier
# transitions: the sliding-window message counts and content fingerprints
# that drive those transitions live in process memory, because a row per
# inbound message is pure write amplification for data that expires in ten
# minutes. What must outlive the process is the COOLDOWN — the 8/14 ping-pong
# loop ran 70+ hours, so any redeploy inside that span would have handed a
# fresh budget back to a sender we had already isolated for 24h.
#
# `suppressed_count` is reset on each trip, so it answers "how much did THIS
# isolation absorb?" rather than accumulating a meaningless lifetime total.
#
# Retention: swept by ChannelTriggerBase._run_cleanup alongside the dedup
# and audit tables, at INGRESS_BREAKER_RETENTION_DAYS. Only CLOSED
# (tier = 0) rows go — a row still carrying escalation memory is exactly
# what we promised to remember, and deleting it because it went quiet
# would hand a re-offender a fresh budget. Rows reach tier 0 by decaying,
# which is what makes this sweep able to reclaim anything at all.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="channel_ingress_breaker",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            # agent_id|channel|chat_id|sender_id — see
            # schema/channel_ingress_breaker_schema.session_key().
            # 419 = the four parts (128 + 32 + 128 + 128) plus three
            # separators; 448 leaves headroom. Sizing this too small is a
            # SQLite-invisible bug: TEXT never truncates, so a short MySQL
            # key would only show up on dev, where two agents' rows would
            # collide on the unique index and overwrite each other.
            Column("session_key", "TEXT", "VARCHAR(448)", nullable=False, unique=True),
            Column("channel", "TEXT", "VARCHAR(32)", nullable=False),
            Column("agent_id", "TEXT", "VARCHAR(128)"),
            Column("chat_id", "TEXT", "VARCHAR(128)"),
            Column("sender_id", "TEXT", "VARCHAR(128)"),
            # 0 = closed. N = has tripped N times; the escalation memory that
            # makes a re-offending session resume at the NEXT cooldown step.
            Column("tier", "INTEGER", "INT", nullable=False, default="0"),
            Column("cooldown_until", "TEXT", "DATETIME(6)"),
            Column("suppressed_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("last_reason", "TEXT", "VARCHAR(64)"),
            Column("last_tripped_at", "TEXT", "DATETIME(6)"),
            # When `tier` last MOVED, in the guard's own clock. Distinct
            # from both neighbours on purpose:
            #   `last_tripped_at` stands still while the tier walks back
            #   down, so anchoring decay there would re-credit silence that
            #   has already been paid out;
            #   `updated_at` is stamped by the repository with wall-clock
            #   time, while the guard runs on a caller-supplied `now` — one
            #   column cannot be both row bookkeeping and state-machine
            #   time.
            Column("tier_changed_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("uk_channel_ingress_breaker_key", ["session_key"], unique=True),
            Index("idx_channel_ingress_breaker_cooldown", ["cooldown_until"]),
            Index("idx_channel_ingress_breaker_updated", ["updated_at"]),
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

# The team bulletin: the standing rules every member loads on every team turn.
#
# `source` and `author_id` are separate columns because they answer different
# questions. `source` decides the RULES — who may delete the row, whether it
# spends the shared entry budget, how it renders. `author_id` decides the
# DISPLAY ("by Ana"). Folding them into one column would make a permission
# check parse a string prefix.
#
# `source='auto_summary'` is a SLOT, not a kind: at most one row per team,
# overwritten in place, `author_id` NULL. It is in every turn's prompt, so an
# accumulating summary would reproduce the very problem the bulletin exists to
# fix, and a poor summary would compound rather than be replaced.
_register(
    TableDef(
        name="team_bulletin_entries",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("entry_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("content", "TEXT", "TEXT", nullable=False),
            # 'user' | 'agent' | 'auto_summary'
            Column("source", "TEXT", "VARCHAR(16)", nullable=False, default="'user'"),
            # user_id or agent_id; NULL for auto_summary (nobody wrote it).
            Column("author_id", "TEXT", "VARCHAR(64)"),
            # 'long_term' | 'current_task' — the second is cleared per task.
            Column("tier", "TEXT", "VARCHAR(16)", nullable=False, default="'long_term'"),
            # Only ever set on the auto_summary row: the ``created_at`` of the
            # newest message that summary covers, so the worker can ask "how
            # much has happened since" without a counter to keep in sync.
            # A dedicated nullable column rather than reusing author_id — that
            # column already means "who wrote this", and a second meaning
            # depending on `source` is what makes a schema unreadable later.
            Column("watermark_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_bulletin_entry_id", ["entry_id"], unique=True),
            Index("idx_bulletin_team", ["team_id"]),
            Index("idx_bulletin_team_source", ["team_id", "source"]),
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
# Skill Marketplace (spec: 2026-07-20-skill-marketplace-tech-design-v1.1.md)
#
# skill_catalog / skill_scan_results are written only by the cloud registry
# instance; they exist (empty) on desktop deployments because auto_migrate
# is unconditional. skill_installations is written on both sides and is an
# AUDIT FOLLOWER of the filesystem truth (skills/ + .skill_meta.json) — the
# reconciler only ever updates rows, never touches user files.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="skill_catalog",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("version", "TEXT", "VARCHAR(32)", nullable=False),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("description", "TEXT", "TEXT"),
            Column("author_json", "TEXT", "TEXT"),
            Column("license", "TEXT", "VARCHAR(64)"),
            Column("category", "TEXT", "VARCHAR(32)"),
            Column("capabilities_json", "TEXT", "TEXT"),
            Column("tags_json", "TEXT", "TEXT"),
            Column("config_schema_json", "TEXT", "MEDIUMTEXT"),
            Column("dependencies_json", "TEXT", "TEXT"),
            Column("compatibility_json", "TEXT", "TEXT"),
            Column("s3_key", "TEXT", "VARCHAR(512)", nullable=False),
            Column("package_hash", "TEXT", "VARCHAR(80)", nullable=False),
            Column("publisher", "TEXT", "VARCHAR(128)"),
            Column("scan_status", "TEXT", "VARCHAR(16)", nullable=False),
            Column("status", "TEXT", "VARCHAR(16)", nullable=False),
            Column("downloads", "INTEGER", "BIGINT", nullable=False, default="0"),
            Column("is_default", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("avg_rating", "REAL", "DECIMAL(3,2)"),
            # Where the skill comes from: marketplace (default) | plugin | builtin.
            # Additive (auto_migrate adds the column with its default); lets the
            # catalog tell a plugin-shipped skill from a published one.
            Column("kind", "TEXT", "VARCHAR(16)", nullable=False, default="'marketplace'"),
            Column("published_at", "TEXT", "DATETIME(6)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_skill_catalog_id_ver", ["skill_id", "version"], unique=True),
            Index("idx_skill_catalog_category", ["category"]),
            Index("idx_skill_catalog_status", ["status"]),
        ],
    )
)

_register(
    TableDef(
        name="skill_installations",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("version", "TEXT", "VARCHAR(32)"),
            Column("source_type", "TEXT", "VARCHAR(16)", nullable=False),
            Column("source_url", "TEXT", "VARCHAR(1024)"),
            Column("package_hash", "TEXT", "VARCHAR(80)"),
            Column("status", "TEXT", "VARCHAR(24)", nullable=False),
            Column("last_event", "TEXT", "VARCHAR(24)"),
            Column("installed_at", "TEXT", "DATETIME(6)"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_skill_inst_agent_user_skill", ["agent_id", "user_id", "skill_id"], unique=True),
            Index("idx_skill_inst_user", ["user_id"]),
        ],
    )
)

_register(
    TableDef(
        name="skill_scan_results",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("version", "TEXT", "VARCHAR(32)", nullable=False),
            Column("status", "TEXT", "VARCHAR(16)", nullable=False),
            Column("high_issues", "INTEGER", "INT", nullable=False, default="0"),
            Column("low_issues", "INTEGER", "INT", nullable=False, default="0"),
            Column("issues_json", "TEXT", "MEDIUMTEXT"),
            Column("scanner_version", "TEXT", "VARCHAR(16)"),
            Column("scanned_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_skill_scan_id_ver", ["skill_id", "version"])],
    )
)

# ----------------------------------------------------------------------------
# Team Marketplace (spec: 2026-07-21-team-marketplace-tech-design.md)
#
# Catalog INDEX for team/agent bundle templates. The .nxbundle blob lives in
# the artifact store (get_template_store, own S3 prefix / local subfolder);
# this table only points at it via store_key + bundle_sha256. Written only by
# the cloud registry instance; desktop deployments have the (empty) table and
# proxy browse/install to the cloud API.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="team_catalog",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("template_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("name", "TEXT", "VARCHAR(255)", nullable=False),
            Column("description", "TEXT", "TEXT"),
            Column("categories_json", "TEXT", "TEXT"),
            Column("author", "TEXT", "VARCHAR(128)"),
            Column("agent_count", "INTEGER", "INT", nullable=False, default="1"),
            Column("thumbnail_url", "TEXT", "VARCHAR(1024)"),
            Column("store_key", "TEXT", "VARCHAR(512)", nullable=False),
            Column("bundle_sha256", "TEXT", "VARCHAR(80)", nullable=False),
            Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            Column("sort_order", "INTEGER", "INT", nullable=False, default="0"),
            Column("downloads", "INTEGER", "BIGINT", nullable=False, default="0"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_team_catalog_tid", ["template_id"], unique=True),
            Index("idx_team_catalog_enabled_sort", ["enabled", "sort_order"]),
        ],
    )
)

# Placeholder registration (Team Recommended phase implements the logic).
_register(
    TableDef(
        name="team_skill_policies",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("policy_type", "TEXT", "VARCHAR(16)", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_team_skill_policy", ["team_id", "skill_id"], unique=True),
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
            Column("artifact_id", "TEXT", "VARCHAR(32)", nullable=False, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("session_id", "TEXT", "VARCHAR(64)"),
            Column(
                "original_session_id", "TEXT", "VARCHAR(64)"
            ),  # remembers session_id at pin time so unpin can restore it
            Column("title", "TEXT", "VARCHAR(200)", nullable=False),
            Column("kind", "TEXT", "VARCHAR(64)", nullable=False),
            Column("description", "TEXT", "TEXT"),
            Column("pinned", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            # Pointer-model columns. file_path = entry file relative to
            # base_working_path; size_bytes = recursive size of the artifact
            # root directory. Nullable so auto_migrate can add them to existing
            # DBs without a backfill — old (versioned) rows keep file_path NULL
            # and are hand-migrated per the cleanup TODO.
            Column("file_path", "TEXT", "VARCHAR(512)"),
            Column("size_bytes", "INTEGER", "BIGINT", nullable=False, default="0"),
            # Team dimension. NULL = a private artifact, which is every row
            # that existed before this column and everything registered
            # outside a team turn — so the private semantics are unchanged by
            # construction and no backfill is needed (iron rule #6: additive
            # only). Set on artifacts produced in a team turn; the team room's
            # panel lists by it, and the owning agent still shows in agent_id
            # so "who produced this" survives the ownership move.
            Column("team_id", "TEXT", "VARCHAR(64)"),
            # sha256 hex of the entry file at last registration. Heal uses it
            # to verify a candidate IS the same content (rename detection):
            # a hash match upgrades the single-candidate auto-repoint from a
            # guess to a verified identity. Nullable: legacy rows stay NULL
            # (heal skips the hash tier) and hashing is best-effort — a
            # hashing failure must never block a registration.
            Column("content_hash", "TEXT", "VARCHAR(64)"),
            # DEPRECATED (2026-05-14): versioning was dropped with the pointer
            # model. Column kept registered because dropping a column is a
            # destructive migration (铁律 #6) — removal is Owner-gated
            # (author-local todo).
            Column("latest_version", "INTEGER", "INT", nullable=False, default="1"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_artifact_agent_session", ["agent_id", "session_id"]),
            Index("idx_artifact_agent_pinned", ["agent_id", "pinned"]),
            Index("idx_artifact_agent_id", ["agent_id"]),  # agent-scoped scans
            # Team-panel listing (and the agent prompt's team half, which
            # unions private with every team the agent belongs to). Without
            # it that query scans every artifact row in the database.
            Index("idx_artifact_team_updated", ["team_id", "updated_at"]),
        ],
    )
)

# ----------------------------------------------------------------------------
# instance_artifact_history — attribution log for artifact (re-)registrations.
#
# Artifacts are POINTERS and re-registering overwrites in place, so today
# "who changed this and when" is unanswerable. That is tolerable for a single
# agent in a private chat; in a team, where several agents hand the same
# artifact back and forth, it is the difference between a shared document and
# an anonymous one.
#
# This is deliberately NOT a revival of the retired `instance_artifact_versions`
# (see the note below): it stores no CONTENT, only who/when/where-it-pointed.
# One row is a few dozen bytes, so the log is always-on and needs no agent
# cooperation — which is the point, because attribution has to be correct even
# when the model does nothing to help. Rolling back to an old version remains
# impossible by design; the agent-initiated backup capability covers that case
# and pays storage only when someone asks for it.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="instance_artifact_history",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("artifact_id", "TEXT", "VARCHAR(32)", nullable=False),
            # The agent that performed THIS registration — not necessarily the
            # artifact's original producer, which is exactly what makes the log
            # worth keeping once teammates start updating each other's work.
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            # This codebase's turn handle (same meaning as bus_messages.event_id),
            # so a history entry can open the turn that produced it. Nullable:
            # the MCP registration path has no event id in scope yet.
            Column("event_id", "TEXT", "VARCHAR(64)"),
            # Where the pointer aimed at the time, so the log stays readable
            # after the artifact is later re-pointed somewhere else.
            Column("file_path", "TEXT", "VARCHAR(512)"),
            Column("size_bytes", "INTEGER", "BIGINT", nullable=False, default="0"),
            # "created" | "updated" | "healed" | "user_edited" |
            # "external_edited" — lets a reader tell the first registration
            # from later overwrites, an intentional update from a heal
            # repoint, and an agent commit from a user/external one.
            # VARCHAR(16): the longest current value (external_edited) is 15
            # chars; a longer action needs the column widened FIRST or MySQL
            # strict mode 1406s the insert.
            Column("action", "TEXT", "VARCHAR(16)", nullable=False, default="'updated'"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            # The only read pattern: one artifact's history, newest first.
            Index("idx_artifact_history_artifact", ["artifact_id", "created_at"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# instance_artifact_events — cross-process outbox for artifact_changed events.
#
# Registry writes happen in two different processes: register_artifact runs in
# the MCP tool server, while the run's Broadcaster (the only road to the
# frontend's WebSocket) lives in the backend. Writers therefore stage one
# self-contained event payload here; BackgroundRun drains unconsumed rows for
# its agent right after each tool-output event and re-emits them through the
# normal recorder+broadcaster pipeline, which buys persistence (event_stream)
# and fan-out in one move.
#
# Rows staged while no run is active (e.g. an HTTP delete) are drained late,
# at the next run's first tool output. That is deliberate: late events are
# neutralised client-side by the updated_at monotonic guard, and the frontend
# full-pull on open/switch/reconnect is the self-healing floor — so the outbox
# needs no TTL and no cross-process locking. Staging is best-effort by
# contract: a failed insert logs a warning and never breaks the registry write
# it accompanies (spec 2026-08-18-artifact-events-inventory-pointer §3).
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="instance_artifact_events",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            # The full wire event (type/action/external/artifact/extra), JSON.
            # Self-contained so the drain is a dumb pump: no joins, no
            # reconstruction, no schema coupling between staging and delivery.
            Column("payload_json", "TEXT", "MEDIUMTEXT", nullable=False),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            # NULL = pending. Set when a drain delivered (or deliberately
            # skipped) the row; never deleted, so the recent tail doubles as
            # a delivery audit.
            Column("consumed_at", "TEXT", "DATETIME(6)"),
        ],
        indexes=[
            # The drain's only read pattern: this agent's pending rows, oldest
            # first (ORDER BY id rides the PK).
            Index("idx_artifact_events_agent_pending", ["agent_id", "consumed_at"]),
        ],
    )
)


# ----------------------------------------------------------------------------
# team_files — index for the team shared folder (`_shared/teams/{team_id}`).
#
# The folder itself has existed for a while, along with `bus_share_to_team` to
# put files in it, and the team prompt advertises it. What it never had was a
# row anywhere: files landed on disk under a generated file_id and nothing
# could enumerate them, so "what is in our shared folder" was answerable only
# by agents reciting paths at each other in chat.
#
# `content_hash` (sha256) is what makes de-duplication honest: sharing the same
# NAME twice does not make it the same file. Identical name + identical hash is
# a true re-share and reuses the existing row; identical name + different hash
# is a different file and BOTH are kept, because silently overwriting one with
# the other would be a destructive write (iron rule #6). sha256 rather than md5
# because the eventual content-addressed store would reuse this same hash, and
# it costs the same.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="team_files",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            # The generated id that is also the on-disk filename.
            Column("file_id", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("team_id", "TEXT", "VARCHAR(64)", nullable=False),
            # Scoping + cleanup: the shared tree lives under the OWNER's root
            # ({base}/{user_id}/_shared/teams/{team_id}), and team membership
            # implies a single owner, so this is the tenant boundary.
            Column("owner_user_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("shared_by_agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            # Human-facing name; the disk name is file_id, so this is the only
            # place the original is recorded.
            Column("original_name", "TEXT", "VARCHAR(512)", nullable=False),
            Column("rel_path", "TEXT", "VARCHAR(1024)", nullable=False),
            Column("mime_type", "TEXT", "VARCHAR(255)"),
            Column("category", "TEXT", "VARCHAR(64)"),
            Column("size_bytes", "INTEGER", "BIGINT", nullable=False, default="0"),
            Column("content_hash", "TEXT", "VARCHAR(64)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_team_files_file_id", ["file_id"], unique=True),
            # Listing: a team's files, newest first.
            Index("idx_team_files_team_created", ["team_id", "created_at"]),
            # Dedup, race-safe at the database level. Keyed on the hash as well
            # as the name so a same-name-different-content share is still
            # insertable — collapsing those would lose data.
            Index(
                "idx_team_files_dedup",
                ["team_id", "original_name", "content_hash"],
                unique=True,
            ),
            # Cheap pre-filter for the write path: hashing costs a full read,
            # so only hash when (team, name, size) already collides. Without
            # this index that "cheap" check is itself a table scan.
            Index("idx_team_files_prefilter", ["team_id", "original_name", "size_bytes"]),
        ],
    )
)


# RETIRED (2026-07-21): `instance_artifact_versions` is no longer registered.
# The pointer model (2026-05-14) dropped per-version content rows; no code has
# read or written the table since. Existing databases keep the table and its
# rows untouched (auto_migrate never drops) so old saved HTML can still be
# hand-migrated; fresh databases simply stop provisioning it. Dropping the
# table (and `instance_artifacts.latest_version`) remains an explicit
# Owner-gated migration (author-local todo).


# ----------------------------------------------------------------------------
# invite_codes — RETIRED feature, table kept for its data. The invite-code
# registration gate was removed 2026-06-11 (cloud signup is NetMind login
# now; everyone gets the free-tier quota). Rows are retained because they
# hold the only old-user-id -> email mapping, which the legacy-user
# migration (scripts/data_migrations/migrate_users_to_netmind.py) needs. No code writes
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
            # Reply language preference (i18n code, e.g. "zh"); NULL = never
            # set — the model picks freely (historical behavior).
            Column("reply_language", "TEXT", "VARCHAR(16)"),
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
            Column("valid_at", "TEXT", "DATETIME(6)"),  # reality: became true (NULL = always)
            Column("invalid_at", "TEXT", "DATETIME(6)"),  # reality: stopped being true (NULL = still true)
            Column("expired_at", "TEXT", "DATETIME(6)"),  # system: superseded tombstone (NULL = live)
            # --- provenance + confidence (hindsight范式) ---
            Column("source_ids", "TEXT", "JSON"),  # which events/records produced this
            Column(
                "source_ref", "TEXT", "JSON"
            ),  # pointer {kind,id} back to the original (projection kinds); NULL = self-contained
            Column("proof_count", "INTEGER", "INT", nullable=False, default="0"),
            Column("history", "TEXT", "MEDIUMTEXT"),  # JSON — evolution snapshots
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

# Login handoffs are notifications, never origin grants. Only the live session's
# authenticated control connection can complete a request by explicitly releasing.
_register(
    TableDef(
        name="instance_browser_login_requests",
        columns=[
            Column("request_id", "TEXT", "VARCHAR(64)", nullable=False, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("session_key", "TEXT", "VARCHAR(64)", nullable=False),
            Column("turn_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("thread_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("reason", "TEXT", "TEXT", nullable=False),
            Column("state", "TEXT", "VARCHAR(32)", nullable=False),
            Column("connection_id", "TEXT", "VARCHAR(64)", nullable=False),
            Column("requested_at", "TEXT", "DATETIME(6)", nullable=False),
        ],
        indexes=[Index("idx_browser_login_agent", ["agent_id"])],
    )
)

# Privileged browser capabilities, one policy document per agent. The shape is
# BrowserPolicy.to_dict(); it contains no website-access rules. Scoped grants
# and decision receipts are persisted for cross-process visibility. The trusted
# runtime turn/conversation scope determines whether a grant applies.
_register(
    TableDef(
        name="instance_browser_policies",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("policy_json", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_browser_policies_agent", ["agent_id"], unique=True)],
    )
)

# Home Assistant binding — one row per HomeAssistantModule instance. config_json
# holds {base_url, token, verify_tls}; token is a sensitive credential (redacted
# on bundle export, masked in the frontend).
_register(
    TableDef(
        name="instance_homeassistant_bindings",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
            Column("config_json", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_ha_bindings_agent", ["agent_id"], unique=True)],
    )
)

# Model probe ledger — one row per aggregator source ("netmind" / "openrouter" /
# "yunwu"). models_json holds that source's per-model probe verdicts (the same
# {"models": {...}} shape as the committed JSON snapshot's per-source entry).
# The DB copy is the durable one in cloud: the committed file resets to the
# release-time snapshot on every deploy, which used to erase all revalidation
# history between releases.
_register(
    TableDef(
        name="model_probe_ledger",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("source", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("models_json", "TEXT", "MEDIUMTEXT"),
            Column("generated_at", "TEXT", "VARCHAR(64)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_mpl_source", ["source"], unique=True)],
    )
)

# Runtime model suspects — (source, model_id, protocol) tuples whose LIVE calls
# hit a definitive model rejection (classify_self_serviceable ->
# model_not_found). The daily model sync revalidates suspects ahead of the TTL
# queue and clears them afterwards; the probe verdict stays authoritative, so a
# false report costs one probe call and nothing else.
_register(
    TableDef(
        name="model_probe_suspects",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("source", "TEXT", "VARCHAR(64)", nullable=False),
            Column("model_id", "TEXT", "VARCHAR(255)", nullable=False),
            Column("protocol", "TEXT", "VARCHAR(32)", nullable=False),
            Column("reason", "TEXT", "VARCHAR(64)"),
            Column("occurrences", "INTEGER", "INT", nullable=False, default="1"),
            Column("last_seen_at", "TEXT", "VARCHAR(64)"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_mps_source_model_proto", ["source", "model_id", "protocol"], unique=True),
        ],
    )
)


# ----------------------------------------------------------------------------
# Narrative routing audit (E1) — the decision trail for narrative selection.
#
# Until this table, `selection_method` / `retrieval_method` / `selection_reason`
# / scores went only to a ProgressMessage and loguru. Docker logs rotate and a
# grep only finds what you thought to search for (incident lesson #5), so there
# was no denominator: "routing accuracy improved by X%" had nothing to measure
# against, and the continuity tier — which can lock a thread for several turns
# on one false positive — left no trace at all.
#
# `candidates_json` holds the WHOLE pool, not top-K, each entry pointing at a
# content-addressed text snapshot. That is not belt-and-braces: `bm25_rank`
# computes IDF and avgdl over the candidate set it is handed, so a partial
# pool replays to different numbers — and the scored text itself (name /
# current_summary / topic_keywords) is rewritten wholesale by the async LLM
# updater on almost every turn with no history kept, so re-reading `narratives`
# later reconstructs a pool that never existed.
# ----------------------------------------------------------------------------
_register(
    TableDef(
        name="narrative_routing_audit",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("agent_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("user_id", "TEXT", "VARCHAR(128)"),
            Column("query_text", "TEXT", "MEDIUMTEXT"),
            Column("trigger", "TEXT", "VARCHAR(64)"),
            Column("is_user_chat", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
            # tier 1 — continuity
            Column("continuity_ran", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("continuity_is_continuous", "INTEGER", "TINYINT(1)"),
            Column("continuity_confidence", "REAL", "DOUBLE"),
            Column("continuity_reason", "TEXT", "TEXT"),
            # tier 2 — BM25 pool + gate
            Column("candidates_json", "TEXT", "MEDIUMTEXT"),
            Column("gate_short_circuit", "INTEGER", "TINYINT(1)"),
            Column("gate_reason", "TEXT", "TEXT"),
            Column("gate_top1_raw", "REAL", "DOUBLE"),
            Column("gate_top2_raw", "REAL", "DOUBLE"),
            Column("gate_margin", "REAL", "DOUBLE"),
            # The bypass decision, split from the score gate on purpose.
            # `gate_short_circuit` stays "did this turn skip the judge";
            # `bypass_score_gate` is floor+margin alone, so the score-gate
            # distribution keeps accumulating for calibration even on the turns
            # the anchor rule denies. `bypass_reason` is which rule decided.
            Column("bypass_score_gate", "INTEGER", "TINYINT(1)"),
            Column("bypass_reason", "TEXT", "VARCHAR(32)"),
            # Slice 0: this row's pool was recorded, not consulted (a continuity
            # turn). Nullable ONLY for the rows that predate the column — the
            # write side never produces NULL (`RoutingAudit.pool_is_shadow` is a
            # plain bool with no "did not run" third state, unlike
            # `bypass_score_gate`). So on the READ side, NULL and 0 mean the
            # same thing and a filter must treat them alike; see
            # `mirror/.../narrative_routing_audit_repository.py.md` 2026-08-26.
            Column("pool_is_shadow", "INTEGER", "TINYINT(1)"),
            # merged routing — one call replaces the continuity/judge pair.
            # All nullable and all additive: prod rows predate them. NULL means
            # "written before this deploy"; the two-call path itself writes
            # 0 / "" for merged_call / merged_verdict / merged_truncated — so
            # read-side filters must be NULL-safe on BOTH dialects
            # (`COALESCE(col,0) <> 1`; never `= 0`, which drops the whole
            # pre-deploy baseline window, and never `IS NOT 1` — SQLite-only).
            # `merged_call` marks the PATH; a turn released by the zero-LLM
            # shutter has merged_call=1 with merged_verdict empty and merged_ms
            # NULL (nothing was asked, so nothing was paid).
            Column("merged_call", "INTEGER", "TINYINT(1)"),
            Column("merged_verdict", "TEXT", "VARCHAR(32)"),
            # SELF time. Says so explicitly because `retrieve_ms` nests
            # `judge_ms` and the ambiguity cost two readers the same wrong
            # conclusion — see todo/2026-08-25-retrieve-ms-nests-judge-ms.md.
            Column("merged_ms", "INTEGER", "INT"),
            # Rendered prompt size: the x-axis of the input→latency model.
            Column("merged_input_chars", "INTEGER", "INT"),
            # Which prompt sections hit their read-side cap, comma-separated.
            Column("merged_truncated", "TEXT", "VARCHAR(128)"),
            # The anchor's standing in BM25 at decision time — the ONLY
            # production instrument for §3.2 (how often the unconditional anchor
            # injection is the sole reason the anchored thread is on the
            # ballot). NULL rank = the anchor scored nothing.
            Column("anchor_bm25_rank", "INTEGER", "INT"),
            Column("anchor_raw_score", "REAL", "DOUBLE"),
            Column("anchor_in_menu", "INTEGER", "TINYINT(1)"),
            # tier 3 — LLM arbitration
            Column("judge_ran", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            Column("judge_category", "TEXT", "VARCHAR(32)"),
            Column("judge_matched_id", "TEXT", "VARCHAR(128)"),
            Column("judge_reason", "TEXT", "TEXT"),
            # outcome
            Column("selection_method", "TEXT", "VARCHAR(64)"),
            Column("retrieval_method", "TEXT", "VARCHAR(32)"),
            Column("chosen_narrative_id", "TEXT", "VARCHAR(128)"),
            Column("is_new", "INTEGER", "TINYINT(1)", nullable=False, default="0"),
            # cost, per tier — joined to the decision that paid for it.
            # NULLABLE ON PURPOSE: NULL = this tier did not run. A
            # short-circuited decision skips the judge, and a 0 there would
            # make "how expensive is arbitration" answer far too low, which is
            # precisely the comparison these columns exist to support.
            # Shadow rows (pool_is_shadow=1) hold the instrument's own tier-2
            # cost in retrieve_ms — filter on pool_is_shadow before any
            # cross-population aggregate, or the continuation majority's ~13ms
            # dilutes the arbitration numbers the same way a stored 0 would.
            # Merged rows (merged_call=1) are the same shape for a different
            # reason: there the LLM has its own column, so retrieve_ms is the
            # BM25 pass alone. "tiers 2+3 together" therefore means
            # `COALESCE(pool_is_shadow,0) <> 1 AND COALESCE(merged_call,0) <> 1`
            # (NULL-safe on both dialects — `= 0` silently drops every
            # pre-deploy row; `IS NOT 1` parses only on SQLite).
            Column("continuity_ms", "INTEGER", "INT"),
            Column("retrieve_ms", "INTEGER", "INT"),
            Column("keyword_ms", "INTEGER", "INT"),
            Column("judge_ms", "INTEGER", "INT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[
            Index("idx_nra_agent_time", ["agent_id", "created_at"]),
            Index("idx_nra_selection_method", ["selection_method"]),
            Index("idx_nra_chosen", ["chosen_narrative_id"]),
        ],
    )
)


# Content-addressed store for the searchable text each narrative carried at
# decision time. Keyed by sha256 of the text, so a pool that did not change
# between turns costs one row total, not one row per turn — the 100-candidate
# pool of a busy agent adds ~1 new snapshot per turn (only the main narrative's
# summary moves), with the audit row storing pointers.
_register(
    TableDef(
        name="narrative_text_snapshots",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, auto_increment=True, primary_key=True),
            Column("text_hash", "TEXT", "VARCHAR(64)", nullable=False, unique=True),
            Column("text", "TEXT", "MEDIUMTEXT"),
            Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_nts_hash", ["text_hash"], unique=True)],
    )
)


# plugin_settings: one row per (plugin, key) of a plugin's declared settings
# (contracts.settings.SettingsSchema). Secrets are stored Fernet-encrypted by
# the platform settings store (is_secret=1) and never echoed back. Env
# ``NXP_<ID>_<KEY>`` overrides win over rows at read time; rows are the
# user-edited values from the plugin factory UI.
_register(
    TableDef(
        name="plugin_settings",
        columns=[
            Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            Column("plugin_id", "TEXT", "VARCHAR(128)", nullable=False),
            Column("key", "TEXT", "VARCHAR(64)", nullable=False),
            Column("value_json", "TEXT", "MEDIUMTEXT"),
            Column("is_secret", "INTEGER", "TINYINT", nullable=False, default="0"),
            Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        ],
        indexes=[Index("idx_plugin_settings_plugin_key", ["plugin_id", "key"], unique=True)],
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

    create_sql = f"CREATE TABLE IF NOT EXISTS {table.name} (\n" + ",\n".join(f"    {d}" for d in col_defs) + "\n)"
    stmts.append(create_sql)

    # Indexes
    for idx in table.indexes:
        unique = "UNIQUE " if idx.unique else ""
        cols = ", ".join(idx.columns)
        stmts.append(f"CREATE {unique}INDEX IF NOT EXISTS {idx.name} ON {table.name}({cols})")

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
            is_lob = any(tok in mysql_type_upper for tok in ("TEXT", "BLOB", "JSON", "GEOMETRY"))
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
        stmts.append(f"CREATE {unique}INDEX `{idx.name}` ON `{table.name}`({cols})")

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
                "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
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
                existing = await backend.execute(f"PRAGMA table_info({table_name})", None)
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
                            if any(tok in mysql_type_upper for tok in ("TEXT", "BLOB", "JSON", "GEOMETRY")):
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
                            f"ALTER TABLE `{table_name}` ADD COLUMN `{col.name}` {col_type}{null_clause}{default}"
                        )
                    else:
                        await backend.execute_write(
                            f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{null_clause}{default}"
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
                            f"CREATE {unique}INDEX IF NOT EXISTS {idx.name} ON {table_name}({cols})"
                        )
                    else:
                        cols = ", ".join(f"`{c}`" for c in idx.columns)
                        await backend.execute_write(f"CREATE {unique}INDEX `{idx.name}` ON `{table_name}`({cols})")
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


async def _self_heal_missing_tables(backend: "DatabaseBackend", dialect: str) -> None:
    """Detect and re-create tables that the registry expects but the DB
    is missing. Idempotent; safe to call multiple times.

    The whole point is to let a non-technical user simply restart the
    app (or even the same launch) and get a working DB without ever
    having to look at logs or run SQL. If self-heal can't fix it on a
    given boot, we still log loudly and keep going.
    """
    missing = await _verify_all_tables_present(backend, dialect)
    if not missing:
        logger.info(f"Schema integrity verified: all {len(TABLES)} registered tables present")
        return

    logger.warning(
        f"Schema self-heal: {len(missing)} table(s) missing after migrate — re-attempting CREATE for: {missing}"
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
        logger.info(f"Schema self-heal complete: all {len(TABLES)} tables present")


async def _verify_all_tables_present(backend: "DatabaseBackend", dialect: str) -> list[str]:
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
                "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                (table_name,),
            )
        if not rows:
            missing.append(table_name)
    return missing
