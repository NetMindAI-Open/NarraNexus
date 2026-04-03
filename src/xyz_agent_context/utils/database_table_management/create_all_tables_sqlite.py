"""
@file_name: create_all_tables_sqlite.py
@author: NexusAgent
@date: 2026-04-02
@description: SQLite DDL for all database tables and local database initialization

Creates all 18 existing tables (translated from MySQL) plus 5 MessageBus tables
in a SQLite database. Used on first launch of the local desktop app (Tauri).

Translation rules applied:
- BIGINT UNSIGNED AUTO_INCREMENT -> INTEGER PRIMARY KEY AUTOINCREMENT
- VARCHAR(N) -> TEXT
- MEDIUMTEXT / LONGTEXT -> TEXT
- TINYINT(1) -> INTEGER (0/1)
- DATETIME(6) -> TEXT (ISO 8601)
- ENUM(...) -> TEXT
- DEFAULT CURRENT_TIMESTAMP(6) -> DEFAULT (datetime('now'))
- ON UPDATE CURRENT_TIMESTAMP(6) -> removed (handled in Python)
- JSON -> TEXT
- ENGINE=InnoDB / charset / collate -> removed
- MySQL backtick quotes -> removed
- Indexes extracted to separate CREATE INDEX statements
"""

from __future__ import annotations

from loguru import logger

from xyz_agent_context.utils.db_backend import DatabaseBackend


# =============================================================================
# 1. agents
# =============================================================================

DDL_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    agent_description TEXT,
    agent_type TEXT,
    is_public INTEGER NOT NULL DEFAULT 0,
    agent_metadata TEXT,
    agent_create_time TEXT NOT NULL DEFAULT (datetime('now')),
    agent_update_time TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_AGENTS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_agents_created_by ON agents(created_by)",
    "CREATE INDEX IF NOT EXISTS idx_agents_agent_type ON agents(agent_type)",
    "CREATE INDEX IF NOT EXISTS idx_agents_create_time ON agents(agent_create_time)",
]

# =============================================================================
# 2. users
# =============================================================================

DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_type TEXT NOT NULL,
    display_name TEXT,
    email TEXT,
    phone_number TEXT,
    nickname TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT,
    last_login_time TEXT,
    create_time TEXT NOT NULL DEFAULT (datetime('now')),
    update_time TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_USERS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_user_type ON users(user_type)",
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
]

# =============================================================================
# 3. events
# =============================================================================

DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    env_context TEXT,
    module_instances TEXT,
    event_log TEXT,
    final_output TEXT,
    narrative_id TEXT,
    agent_id TEXT NOT NULL,
    user_id TEXT,
    event_embedding TEXT,
    embedding_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_EVENTS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_narrative_id ON events(narrative_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_agent_id ON events(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_trigger ON events(trigger)",
    "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)",
]

# =============================================================================
# 4. narratives
# =============================================================================

DDL_NARRATIVES = """
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    narrative_id TEXT NOT NULL,
    type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    narrative_info TEXT,
    main_chat_instance_id TEXT,
    active_instances TEXT,
    instance_history_ids TEXT,
    event_ids TEXT,
    dynamic_summary TEXT,
    env_variables TEXT,
    topic_keywords TEXT,
    topic_hint TEXT,
    routing_embedding TEXT,
    embedding_updated_at TEXT,
    events_since_last_embedding_update INTEGER NOT NULL DEFAULT 0,
    round_counter INTEGER NOT NULL DEFAULT 0,
    related_narrative_ids TEXT,
    is_special TEXT NOT NULL DEFAULT 'other',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_NARRATIVES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_narratives_narrative_id ON narratives(narrative_id)",
    "CREATE INDEX IF NOT EXISTS idx_narratives_agent_id ON narratives(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_narratives_type ON narratives(type)",
    "CREATE INDEX IF NOT EXISTS idx_narratives_created_at ON narratives(created_at)",
]

# =============================================================================
# 5. mcp_urls
# =============================================================================

DDL_MCP_URLS = """
CREATE TABLE IF NOT EXISTS mcp_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    connection_status TEXT,
    last_check_time TEXT,
    last_error TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_MCP_URLS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_urls_mcp_id ON mcp_urls(mcp_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_urls_agent_user ON mcp_urls(agent_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_urls_is_enabled ON mcp_urls(is_enabled)",
]

# =============================================================================
# 6. inbox_table
# =============================================================================

DDL_INBOX_TABLE = """
CREATE TABLE IF NOT EXISTS inbox_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source TEXT,
    event_id TEXT,
    message_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INBOX_TABLE = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_message_id ON inbox_table(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_inbox_user_id ON inbox_table(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_inbox_is_read ON inbox_table(is_read)",
]

# =============================================================================
# 7. agent_messages
# =============================================================================

DDL_AGENT_MESSAGES = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content TEXT NOT NULL,
    if_response INTEGER NOT NULL DEFAULT 0,
    narrative_id TEXT,
    event_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_AGENT_MESSAGES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_messages_message_id ON agent_messages(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_messages_agent_id ON agent_messages(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_messages_agent_source ON agent_messages(agent_id, source_type)",
    "CREATE INDEX IF NOT EXISTS idx_agent_messages_created_at ON agent_messages(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_messages_if_response ON agent_messages(agent_id, if_response)",
]

# =============================================================================
# 8. module_instances
# =============================================================================

DDL_MODULE_INSTANCES = """
CREATE TABLE IF NOT EXISTS module_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    module_class TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    user_id TEXT,
    is_public INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    description TEXT,
    dependencies TEXT,
    config TEXT,
    state TEXT,
    routing_embedding TEXT,
    keywords TEXT,
    topic_hint TEXT,
    last_used_at TEXT,
    completed_at TEXT,
    archived_at TEXT,
    last_polled_status TEXT,
    callback_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_MODULE_INSTANCES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_module_instances_instance_id ON module_instances(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_module_instances_agent_id ON module_instances(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_module_instances_agent_user ON module_instances(agent_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_module_instances_module_class ON module_instances(module_class)",
    "CREATE INDEX IF NOT EXISTS idx_module_instances_status ON module_instances(status)",
    "CREATE INDEX IF NOT EXISTS idx_module_instances_is_public ON module_instances(agent_id, is_public)",
]

# =============================================================================
# 9. instance_social_entities
# =============================================================================

DDL_INSTANCE_SOCIAL_ENTITIES = """
CREATE TABLE IF NOT EXISTS instance_social_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_name TEXT,
    entity_description TEXT,
    identity_info TEXT,
    contact_info TEXT,
    relationship_strength REAL DEFAULT 0.0,
    interaction_count INTEGER DEFAULT 0,
    last_interaction_time TEXT,
    tags TEXT,
    expertise_domains TEXT,
    related_job_ids TEXT,
    embedding TEXT,
    persona TEXT,
    extra_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INSTANCE_SOCIAL_ENTITIES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_instance_entity ON instance_social_entities(instance_id, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_social_instance_id ON instance_social_entities(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_social_entity_type ON instance_social_entities(entity_type)",
]

# =============================================================================
# 10. instance_jobs
# =============================================================================

DDL_INSTANCE_JOBS = """
CREATE TABLE IF NOT EXISTS instance_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    payload TEXT,
    job_type TEXT NOT NULL,
    trigger_config TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    process TEXT,
    last_error TEXT,
    notification_method TEXT DEFAULT 'inbox',
    next_run_time TEXT,
    last_run_time TEXT,
    started_at TEXT,
    embedding TEXT,
    related_entity_id TEXT,
    narrative_id TEXT,
    monitored_job_ids TEXT,
    iteration_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)
"""

IDX_INSTANCE_JOBS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_jobs_job_id ON instance_jobs(job_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_instance_jobs_instance_id ON instance_jobs(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_instance_jobs_agent_user ON instance_jobs(agent_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_instance_jobs_status ON instance_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_instance_jobs_next_run_time ON instance_jobs(next_run_time)",
    "CREATE INDEX IF NOT EXISTS idx_instance_jobs_narrative_id ON instance_jobs(narrative_id)",
]

# =============================================================================
# 11. instance_rag_store
# =============================================================================

DDL_INSTANCE_RAG_STORE = """
CREATE TABLE IF NOT EXISTS instance_rag_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    store_name TEXT NOT NULL,
    keywords TEXT,
    uploaded_files TEXT,
    file_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)
"""

IDX_INSTANCE_RAG_STORE = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_rag_store_instance_id ON instance_rag_store(instance_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_rag_display_name ON instance_rag_store(display_name)",
]

# =============================================================================
# 12. instance_narrative_links
# =============================================================================

DDL_INSTANCE_NARRATIVE_LINKS = """
CREATE TABLE IF NOT EXISTS instance_narrative_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    narrative_id TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'active',
    local_status TEXT NOT NULL DEFAULT 'active',
    linked_at TEXT,
    unlinked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INSTANCE_NARRATIVE_LINKS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_instance_narrative ON instance_narrative_links(instance_id, narrative_id)",
    "CREATE INDEX IF NOT EXISTS idx_nar_links_narrative_id ON instance_narrative_links(narrative_id)",
    "CREATE INDEX IF NOT EXISTS idx_nar_links_instance_id ON instance_narrative_links(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_nar_links_link_type ON instance_narrative_links(link_type)",
]

# =============================================================================
# 13. instance_awareness
# =============================================================================

DDL_INSTANCE_AWARENESS = """
CREATE TABLE IF NOT EXISTS instance_awareness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    awareness TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INSTANCE_AWARENESS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_awareness_instance_id ON instance_awareness(instance_id)",
]

# =============================================================================
# 14. instance_module_report_memory
# =============================================================================

DDL_INSTANCE_MODULE_REPORT_MEMORY = """
CREATE TABLE IF NOT EXISTS instance_module_report_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    report_memory TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INSTANCE_MODULE_REPORT_MEMORY = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_memory_instance_id ON instance_module_report_memory(instance_id)",
]

# =============================================================================
# 15. instance_json_format_memory
# =============================================================================

DDL_INSTANCE_JSON_FORMAT_MEMORY = """
CREATE TABLE IF NOT EXISTS instance_json_format_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    memory TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_INSTANCE_JSON_FORMAT_MEMORY = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_json_memory_instance_id ON instance_json_format_memory(instance_id)",
]

# =============================================================================
# 16. matrix_credentials
# =============================================================================

DDL_MATRIX_CREDENTIALS = """
CREATE TABLE IF NOT EXISTS matrix_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    nexus_agent_id TEXT,
    api_key TEXT NOT NULL,
    matrix_user_id TEXT NOT NULL,
    server_url TEXT NOT NULL,
    sync_token TEXT,
    next_poll_time TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_MATRIX_CREDENTIALS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_matrix_agent_id ON matrix_credentials(agent_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_matrix_user_id ON matrix_credentials(matrix_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_matrix_is_active ON matrix_credentials(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_matrix_next_poll_time ON matrix_credentials(next_poll_time)",
]

# =============================================================================
# 17. cost_records
# =============================================================================

DDL_COST_RECORDS = """
CREATE TABLE IF NOT EXISTS cost_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    event_id TEXT,
    call_type TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_COST_RECORDS = [
    "CREATE INDEX IF NOT EXISTS idx_cost_agent_id ON cost_records(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_cost_created_at ON cost_records(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_cost_call_type ON cost_records(call_type)",
]

# =============================================================================
# 18. matrix_processed_events
# =============================================================================

DDL_MATRIX_PROCESSED_EVENTS = """
CREATE TABLE IF NOT EXISTS matrix_processed_events (
    event_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (event_id, agent_id)
)
"""

IDX_MATRIX_PROCESSED_EVENTS = [
    "CREATE INDEX IF NOT EXISTS idx_mpe_processed_at ON matrix_processed_events(processed_at)",
]

# =============================================================================
# 19. embeddings_store
# =============================================================================

DDL_EMBEDDINGS_STORE = """
CREATE TABLE IF NOT EXISTS embeddings_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector TEXT NOT NULL,
    source_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

IDX_EMBEDDINGS_STORE = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_entity_model ON embeddings_store(entity_type, entity_id, model)",
    "CREATE INDEX IF NOT EXISTS idx_emb_type_model ON embeddings_store(entity_type, model)",
    "CREATE INDEX IF NOT EXISTS idx_emb_entity ON embeddings_store(entity_type, entity_id)",
]


# =============================================================================
# Aggregated lists for batch creation
# =============================================================================

# All 18 existing tables (+ 1 embeddings_store = 19 total) in creation order
ALL_TABLE_DDL = [
    DDL_AGENTS,
    DDL_USERS,
    DDL_EVENTS,
    DDL_NARRATIVES,
    DDL_MCP_URLS,
    DDL_INBOX_TABLE,
    DDL_AGENT_MESSAGES,
    DDL_MODULE_INSTANCES,
    DDL_INSTANCE_SOCIAL_ENTITIES,
    DDL_INSTANCE_JOBS,
    DDL_INSTANCE_RAG_STORE,
    DDL_INSTANCE_NARRATIVE_LINKS,
    DDL_INSTANCE_AWARENESS,
    DDL_INSTANCE_MODULE_REPORT_MEMORY,
    DDL_INSTANCE_JSON_FORMAT_MEMORY,
    DDL_MATRIX_CREDENTIALS,
    DDL_COST_RECORDS,
    DDL_MATRIX_PROCESSED_EVENTS,
    DDL_EMBEDDINGS_STORE,
]

ALL_TABLE_INDEXES = [
    *IDX_AGENTS,
    *IDX_USERS,
    *IDX_EVENTS,
    *IDX_NARRATIVES,
    *IDX_MCP_URLS,
    *IDX_INBOX_TABLE,
    *IDX_AGENT_MESSAGES,
    *IDX_MODULE_INSTANCES,
    *IDX_INSTANCE_SOCIAL_ENTITIES,
    *IDX_INSTANCE_JOBS,
    *IDX_INSTANCE_RAG_STORE,
    *IDX_INSTANCE_NARRATIVE_LINKS,
    *IDX_INSTANCE_AWARENESS,
    *IDX_INSTANCE_MODULE_REPORT_MEMORY,
    *IDX_INSTANCE_JSON_FORMAT_MEMORY,
    *IDX_MATRIX_CREDENTIALS,
    *IDX_COST_RECORDS,
    *IDX_MATRIX_PROCESSED_EVENTS,
    *IDX_EMBEDDINGS_STORE,
]

# All table names for verification
ALL_TABLE_NAMES = [
    "agents",
    "users",
    "events",
    "narratives",
    "mcp_urls",
    "inbox_table",
    "agent_messages",
    "module_instances",
    "instance_social_entities",
    "instance_jobs",
    "instance_rag_store",
    "instance_narrative_links",
    "instance_awareness",
    "instance_module_report_memory",
    "instance_json_format_memory",
    "matrix_credentials",
    "cost_records",
    "matrix_processed_events",
    "embeddings_store",
]


async def create_all_tables_sqlite(backend: DatabaseBackend) -> None:
    """
    Create ALL tables (18 existing + embeddings_store) in a SQLite database.

    Does NOT include the 5 MessageBus tables; use create_bus_tables_sqlite()
    from create_message_bus_tables.py for those.

    Args:
        backend: An initialized DatabaseBackend (SQLiteBackend).
    """
    for ddl in ALL_TABLE_DDL:
        await backend.execute_write(ddl)

    for idx_ddl in ALL_TABLE_INDEXES:
        await backend.execute_write(idx_ddl)

    logger.info(f"Created {len(ALL_TABLE_DDL)} tables and {len(ALL_TABLE_INDEXES)} indexes in SQLite")


async def init_local_database(db_path: str) -> None:
    """
    Initialize a fresh SQLite database with all tables.

    Called on first app launch. Creates all 19 core tables plus 5 MessageBus
    tables, along with all indexes.

    Args:
        db_path: File path for the SQLite database.
    """
    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
        create_bus_tables_sqlite,
    )

    backend = SQLiteBackend(db_path)
    await backend.initialize()

    try:
        await create_all_tables_sqlite(backend)
        await create_bus_tables_sqlite(backend)
        logger.info(f"Local database initialized at {db_path}")
    finally:
        await backend.close()
