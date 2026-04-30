"""
@file_name: event_memory_module.py
@author: NetMind.AI
@date: 2025-12-09
@description: Event Memory Module

This module is an infrastructure module that provides Narrative-level Memory storage capabilities for other Modules.

Design Principles:
==================
Based on the Module Memory layered design (see requirements/narrative_refactor/module_memory.md):

1. Agent-level data: Isolated by agent_id, shared across all Narratives
   - Managed by each Module's own tables (e.g., awareness, social_network_entities)
   - Not related to this module

2. Narrative-level data: Isolated by narrative_id, belongs to a specific storyline
   - Managed centrally by this module (EventMemoryModule)
   - Stored in json_format_event_memory_{module_name} tables
   - This is the Module Instance's "memory", determining differences between instances

Key Concepts:
=============
- Module Instance = Module + Narrative
- narrative_id is the unique identifier of an instance
- Narrative-level data determines the differences between Module instances

Table Structure:
================
1. json_format_event_memory_{module_name} - Structured Memory for each Module
   - narrative_id: Narrative ID (key!)
   - memory: Memory data in JSON format

2. module_report_memory - Module status reports to the Narrative
   - narrative_id: Narrative ID
   - module_name: Module name
   - report_memory: Report content (used by Narrative to determine whether to activate the Module)

Usage:
======
Other Modules can use EventMemoryModule in the following ways:
1. Create an EventMemoryModule instance in __init__
2. Call search_json_format_memory in hook_data_gathering to retrieve Memory
3. Call add_json_format_memory in hook_after_event_execution to store Memory
4. Call update_report_memory in hook_after_event_execution to update reports
"""

from typing import Optional, Dict, Any, List
from loguru import logger

from xyz_agent_context.module import XYZBaseModule
from xyz_agent_context.schema import MCPServerConfig, ModuleConfig
from xyz_agent_context.utils import DatabaseClient


class EventMemoryModule(XYZBaseModule):
    """
    Event Memory Module

    Provides Narrative-level Memory storage capabilities for other Modules.

    Core Responsibilities:
    1. Manage {module_name}_json_format_event_memory tables (structured Memory)
    2. Manage module_report_memory table (Module status reports)
    3. Provide a unified Memory CRUD interface

    Design Notes:
    - Each Module's Narrative-level data is stored in independent tables
    - Table name format: {module_name}_json_format_event_memory
    - All data is isolated by narrative_id
    """

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None
    ):
        super().__init__(agent_id, user_id, database_client)
        self.instructions = ""

        # Cache of already-checked tables
        self._checked_tables: set = set()

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="EventMemoryModule",
            priority=99,  # High priority (infrastructure module)
            enabled=True,
            description="Provides Narrative-level Memory storage capabilities"
        )

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """EventMemoryModule does not provide an MCP Server"""
        return None

    # ================================================================================================
    # JSON Format Memory - Structured Memory Storage
    # ================================================================================================

    async def if_json_format_table_exists(self, module_name: str) -> bool:
        """
        Check if the JSON Format Memory table for the specified Module exists

        Args:
            module_name: Module name (e.g., "ChatModule", "JobModule")

        Returns:
            bool: Whether the table exists
        """
        table_name = self._get_json_format_table_name(module_name)

        # Check cache first
        if table_name in self._checked_tables:
            return True

        query = """
            SELECT COUNT(*) as cnt
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = %s
        """

        result = await self.db.execute(query, params=(table_name,), fetch=True)
        exists = result and len(result) > 0 and result[0].get("cnt", 0) > 0

        if exists:
            self._checked_tables.add(table_name)

        return exists

    async def create_json_format_table(self, module_name: str) -> bool:
        """
        Create JSON Format Memory table for the specified Module

        Table structure:
        - id: Auto-increment primary key
        - narrative_id: Narrative ID (key! isolated by this)
        - memory: Memory data in JSON format (MEDIUMTEXT supports large data)
        - created_at: Creation time
        - updated_at: Update time

        Args:
            module_name: Module name

        Returns:
            bool: Whether creation was successful
        """
        table_name = self._get_json_format_table_name(module_name)

        create_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `narrative_id` VARCHAR(128) NOT NULL COMMENT 'Narrative ID, used to isolate Memory across different storylines',
                `memory` MEDIUMTEXT COMMENT 'Memory data in JSON format',
                `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
                `updated_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                PRIMARY KEY (`id`),
                UNIQUE INDEX `idx_narrative` (`narrative_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='Narrative-level {module_name} Memory storage';
        """

        try:
            await self.db.execute(create_sql, fetch=False)
            self._checked_tables.add(table_name)
            logger.info(f"EventMemoryModule: created table {table_name} successfully")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to create table {table_name}: {e}")
            return False

    async def ensure_json_format_table(self, module_name: str) -> bool:
        """
        Ensure JSON Format Memory table exists, create if not

        Args:
            module_name: Module name

        Returns:
            bool: Whether the table is available
        """
        if await self.if_json_format_table_exists(module_name):
            return True
        return await self.create_json_format_table(module_name)

    async def add_json_format_memory(
        self,
        module_name: str,
        narrative_id: str,
        memory: Dict[str, Any]
    ) -> bool:
        """
        Add or update JSON Format Memory

        Uses UPSERT strategy:
        - If narrative_id does not exist, insert a new record
        - If narrative_id already exists, update memory

        Args:
            module_name: Module name
            narrative_id: Narrative ID
            memory: Memory data (dict, will be converted to JSON)

        Returns:
            bool: Whether the operation was successful
        """
        # Ensure table exists
        if not await self.ensure_json_format_table(module_name):
            return False

        table_name = self._get_json_format_table_name(module_name)

        import json
        memory_json = json.dumps(memory, ensure_ascii=False, default=str)

        # Use INSERT ... ON DUPLICATE KEY UPDATE to implement UPSERT
        # Use alias syntax to avoid MySQL 8.0.20+ VALUES() function deprecation warning
        upsert_sql = f"""
            INSERT INTO `{table_name}` (`narrative_id`, `memory`)
            VALUES (%s, %s) AS new_values
            ON DUPLICATE KEY UPDATE
                `memory` = new_values.`memory`,
                `updated_at` = CURRENT_TIMESTAMP(6)
        """

        try:
            await self.db.execute(upsert_sql, params=(narrative_id, memory_json), fetch=False)
            logger.debug(f"EventMemoryModule: saved {module_name} Memory successfully, narrative_id={narrative_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to save {module_name} Memory: {e}")
            return False

    async def search_json_format_memory(
        self,
        module_name: str,
        narrative_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Query JSON Format Memory for the specified Narrative

        Args:
            module_name: Module name
            narrative_id: Narrative ID

        Returns:
            Memory data (dict), returns None if not found
        """
        # Ensure table exists
        if not await self.ensure_json_format_table(module_name):
            return None

        table_name = self._get_json_format_table_name(module_name)

        query = f"""
            SELECT `memory` FROM `{table_name}`
            WHERE `narrative_id` = %s
        """

        try:
            result = await self.db.execute(query, params=(narrative_id,), fetch=True)

            if result and len(result) > 0 and result[0].get("memory"):
                import json
                memory_str = result[0]["memory"]
                return json.loads(memory_str)
            return None

        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to query {module_name} Memory: {e}")
            return None

    async def delete_json_format_memory(
        self,
        module_name: str,
        narrative_id: str
    ) -> bool:
        """
        Delete JSON Format Memory for the specified Narrative

        Args:
            module_name: Module name
            narrative_id: Narrative ID

        Returns:
            bool: Whether deletion was successful
        """
        table_name = self._get_json_format_table_name(module_name)

        # Check if table exists
        if not await self.if_json_format_table_exists(module_name):
            return True  # Table does not exist, treat as successful deletion

        delete_sql = f"""
            DELETE FROM `{table_name}`
            WHERE `narrative_id` = %s
        """

        try:
            await self.db.execute(delete_sql, params=(narrative_id,), fetch=False)
            logger.debug(f"EventMemoryModule: deleted {module_name} Memory successfully, narrative_id={narrative_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to delete {module_name} Memory: {e}")
            return False

    def _get_json_format_table_name(self, module_name: str) -> str:
        """
        Get JSON Format Memory table name

        Naming convention: json_format_event_memory_{module_name}
        Example: ChatModule -> json_format_event_memory_chat
        """
        # Convert to lowercase, remove "Module" suffix (if present)
        name = module_name.lower()
        if name.endswith("module"):
            name = name[:-6]  # Remove "module"
        return f"json_format_event_memory_{name}"

    # ================================================================================================
    # Report Memory - Module Status Reports (for Narrative orchestration decisions)
    # ================================================================================================

    async def ensure_report_memory_table(self) -> bool:
        """
        Ensure module_report_memory table exists

        This table is used by Modules to report their status to the Narrative,
        helping the Narrative decide whether to activate a certain Module.

        The table is normally created by auto_migrate (schema_registry).
        This method serves as a fallback safety net.

        Returns:
            bool: Whether the table is available
        """
        table_name = "module_report_memory"

        # Check cache
        if table_name in self._checked_tables:
            return True

        # Check if table exists (dialect-aware)
        is_sqlite = getattr(getattr(self.db, '_backend', None), 'dialect', None) == 'sqlite'

        if is_sqlite:
            query = "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?"
        else:
            query = """
                SELECT COUNT(*) as cnt
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = %s
            """
        result = await self.db.execute(query, params=(table_name,), fetch=True)

        if result and len(result) > 0 and result[0].get("cnt", 0) > 0:
            self._checked_tables.add(table_name)
            return True

        # Create table (dialect-aware fallback)
        if is_sqlite:
            create_sql = """
                CREATE TABLE IF NOT EXISTS module_report_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    narrative_id TEXT NOT NULL,
                    module_name TEXT NOT NULL,
                    report_memory TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """
        else:
            create_sql = """
                CREATE TABLE IF NOT EXISTS `module_report_memory` (
                    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `narrative_id` VARCHAR(128) NOT NULL COMMENT 'Narrative ID',
                    `module_name` VARCHAR(128) NOT NULL COMMENT 'Module name',
                    `report_memory` TEXT COMMENT 'Status information reported by Module to Narrative',
                    `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
                    `updated_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (`id`),
                    UNIQUE INDEX `idx_narrative_module` (`narrative_id`, `module_name`),
                    INDEX `idx_narrative` (`narrative_id`),
                    INDEX `idx_module` (`module_name`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                COMMENT='Module status report table, used for Narrative orchestration decisions';
            """

        try:
            await self.db.execute(create_sql, fetch=False)
            if is_sqlite:
                # Create indexes separately for SQLite
                for idx_sql in [
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_module ON module_report_memory(narrative_id, module_name)",
                    "CREATE INDEX IF NOT EXISTS idx_report_narrative ON module_report_memory(narrative_id)",
                    "CREATE INDEX IF NOT EXISTS idx_report_module ON module_report_memory(module_name)",
                ]:
                    await self.db.execute(idx_sql, fetch=False)
            self._checked_tables.add(table_name)
            logger.info(f"EventMemoryModule: created table {table_name} successfully")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to create table {table_name}: {e}")
            return False

    async def update_report_memory(
        self,
        narrative_id: str,
        module_name: str,
        report_memory: str
    ) -> bool:
        """
        Update Module's status report

        After each Event execution, a Module can call this method to update its status report.
        The Narrative can use these reports to decide whether to activate a certain Module.

        Args:
            narrative_id: Narrative ID
            module_name: Module name
            report_memory: Status report content (natural language description)

        Returns:
            bool: Whether the update was successful

        Example:
            await event_memory.update_report_memory(
                narrative_id="narrative_001",
                module_name="ChatModule",
                report_memory="Conducted 5 rounds of conversation, mainly discussing AI alignment, user expressed interest in RLHF"
            )
        """
        if not await self.ensure_report_memory_table():
            return False

        # Use alias syntax to avoid MySQL 8.0.20+ VALUES() function deprecation warning
        upsert_sql = """
            INSERT INTO `module_report_memory` (`narrative_id`, `module_name`, `report_memory`)
            VALUES (%s, %s, %s) AS new_values
            ON DUPLICATE KEY UPDATE
                `report_memory` = new_values.`report_memory`,
                `updated_at` = CURRENT_TIMESTAMP(6)
        """

        try:
            await self.db.execute(
                upsert_sql,
                params=(narrative_id, module_name, report_memory),
                fetch=False
            )
            logger.debug(f"EventMemoryModule: updated {module_name} status report successfully, narrative_id={narrative_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to update {module_name} status report: {e}")
            return False

    async def get_report_memory(
        self,
        narrative_id: str,
        module_name: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Get Module's status report

        Args:
            narrative_id: Narrative ID
            module_name: Module name (optional, returns all Module reports if not provided)

        Returns:
            If module_name is specified, returns {"module_name": report_memory}
            If not specified, returns {module_name1: report_memory1, module_name2: report_memory2, ...}
            Returns None if not found
        """
        if not await self.ensure_report_memory_table():
            return None

        if module_name:
            query = """
                SELECT `module_name`, `report_memory`
                FROM `module_report_memory`
                WHERE `narrative_id` = %s AND `module_name` = %s
            """
            params = (narrative_id, module_name)
        else:
            query = """
                SELECT `module_name`, `report_memory`
                FROM `module_report_memory`
                WHERE `narrative_id` = %s
            """
            params = (narrative_id,)

        try:
            result = await self.db.execute(query, params=params, fetch=True)

            if not result:
                return None

            return {row["module_name"]: row["report_memory"] for row in result}

        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to get status report: {e}")
            return None

    async def delete_report_memory(
        self,
        narrative_id: str,
        module_name: Optional[str] = None
    ) -> bool:
        """
        Delete Module's status report

        Args:
            narrative_id: Narrative ID
            module_name: Module name (optional, deletes all reports under the Narrative if not provided)

        Returns:
            bool: Whether deletion was successful
        """
        if not await self.ensure_report_memory_table():
            return True

        if module_name:
            delete_sql = """
                DELETE FROM `module_report_memory`
                WHERE `narrative_id` = %s AND `module_name` = %s
            """
            params = (narrative_id, module_name)
        else:
            delete_sql = """
                DELETE FROM `module_report_memory`
                WHERE `narrative_id` = %s
            """
            params = (narrative_id,)

        try:
            await self.db.execute(delete_sql, params=params, fetch=False)
            logger.debug(f"EventMemoryModule: deleted status report successfully, narrative_id={narrative_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to delete status report: {e}")
            return False

    # ================================================================================================
    # Instance-based JSON Format Memory - Structured Memory Storage based on Instance
    # For modules that need data isolation by Instance (rather than Narrative), e.g., ChatModule
    # ================================================================================================

    async def if_instance_json_format_table_exists(self, module_name: str) -> bool:
        """
        Check if the Instance-based JSON Format Memory table for the specified Module exists

        Args:
            module_name: Module name (e.g., "ChatModule", "JobModule")

        Returns:
            bool: Whether the table exists
        """
        table_name = self._get_instance_json_format_table_name(module_name)

        # Check cache first
        if table_name in self._checked_tables:
            return True

        query = """
            SELECT COUNT(*) as cnt
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = %s
        """

        result = await self.db.execute(query, params=(table_name,), fetch=True)
        exists = result and len(result) > 0 and result[0].get("cnt", 0) > 0

        if exists:
            self._checked_tables.add(table_name)

        return exists

    async def create_instance_json_format_table(self, module_name: str) -> bool:
        """
        Create Instance-based JSON Format Memory table for the specified Module

        Table structure:
        - id: Auto-increment primary key
        - instance_id: Instance ID (key! isolated by this, each user's ChatModule instance in a Narrative)
        - memory: Memory data in JSON format (MEDIUMTEXT supports large data)
        - created_at: Creation time
        - updated_at: Update time

        Args:
            module_name: Module name

        Returns:
            bool: Whether creation was successful
        """
        table_name = self._get_instance_json_format_table_name(module_name)

        create_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `instance_id` VARCHAR(128) NOT NULL COMMENT 'Instance ID, used to isolate Memory across different users',
                `memory` MEDIUMTEXT COMMENT 'Memory data in JSON format',
                `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
                `updated_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                PRIMARY KEY (`id`),
                UNIQUE INDEX `idx_instance` (`instance_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            COMMENT='Instance-level {module_name} Memory storage';
        """

        try:
            await self.db.execute(create_sql, fetch=False)
            self._checked_tables.add(table_name)
            logger.info(f"EventMemoryModule: created table {table_name} successfully")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to create table {table_name}: {e}")
            return False

    async def ensure_instance_json_format_table(self, module_name: str) -> bool:
        """
        Ensure Instance-based JSON Format Memory table exists, create if not

        Args:
            module_name: Module name

        Returns:
            bool: Whether the table is available
        """
        if await self.if_instance_json_format_table_exists(module_name):
            return True
        return await self.create_instance_json_format_table(module_name)

    async def add_instance_json_format_memory(
        self,
        module_name: str,
        instance_id: str,
        memory: Dict[str, Any]
    ) -> bool:
        """
        Add or update Instance-based JSON Format Memory

        Uses UPSERT strategy:
        - If instance_id does not exist, insert a new record
        - If instance_id already exists, update memory

        Args:
            module_name: Module name
            instance_id: Instance ID (e.g., chat_xxxxxxxx)
            memory: Memory data (dict, will be converted to JSON)

        Returns:
            bool: Whether the operation was successful
        """
        # Ensure table exists
        if not await self.ensure_instance_json_format_table(module_name):
            return False

        table_name = self._get_instance_json_format_table_name(module_name)

        import json
        memory_json = json.dumps(memory, ensure_ascii=False, default=str)

        # Use INSERT ... ON DUPLICATE KEY UPDATE to implement UPSERT
        upsert_sql = f"""
            INSERT INTO `{table_name}` (`instance_id`, `memory`)
            VALUES (%s, %s) AS new_values
            ON DUPLICATE KEY UPDATE
                `memory` = new_values.`memory`,
                `updated_at` = CURRENT_TIMESTAMP(6)
        """

        try:
            await self.db.execute(upsert_sql, params=(instance_id, memory_json), fetch=False)
            logger.debug(f"EventMemoryModule: saved {module_name} Instance Memory successfully, instance_id={instance_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to save {module_name} Instance Memory: {e}")
            return False

    async def search_instance_json_format_memory(
        self,
        module_name: str,
        instance_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Query JSON Format Memory for the specified Instance

        Args:
            module_name: Module name
            instance_id: Instance ID

        Returns:
            Memory data (dict), returns None if not found
        """
        # Ensure table exists
        if not await self.ensure_instance_json_format_table(module_name):
            return None

        table_name = self._get_instance_json_format_table_name(module_name)

        query = f"""
            SELECT `memory` FROM `{table_name}`
            WHERE `instance_id` = %s
        """

        try:
            result = await self.db.execute(query, params=(instance_id,), fetch=True)

            if result and len(result) > 0 and result[0].get("memory"):
                import json
                memory_str = result[0]["memory"]
                return json.loads(memory_str)
            return None

        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to query {module_name} Instance Memory: {e}")
            return None

    async def delete_instance_json_format_memory(
        self,
        module_name: str,
        instance_id: str
    ) -> bool:
        """
        Delete JSON Format Memory for the specified Instance

        Args:
            module_name: Module name
            instance_id: Instance ID

        Returns:
            bool: Whether deletion was successful
        """
        table_name = self._get_instance_json_format_table_name(module_name)

        # Check if table exists
        if not await self.if_instance_json_format_table_exists(module_name):
            return True  # Table does not exist, treat as successful deletion

        delete_sql = f"""
            DELETE FROM `{table_name}`
            WHERE `instance_id` = %s
        """

        try:
            await self.db.execute(delete_sql, params=(instance_id,), fetch=False)
            logger.debug(f"EventMemoryModule: deleted {module_name} Instance Memory successfully, instance_id={instance_id}")
            return True
        except Exception as e:
            logger.exception(f"EventMemoryModule: failed to delete {module_name} Instance Memory: {e}")
            return False

    def _get_instance_json_format_table_name(self, module_name: str) -> str:
        """
        Get Instance-based JSON Format Memory table name

        Naming convention: instance_json_format_memory_{module_name}
        Example: ChatModule -> instance_json_format_memory_chat
        """
        # Convert to lowercase, remove "Module" suffix (if present)
        name = module_name.lower()
        if name.endswith("module"):
            name = name[:-6]  # Remove "module"
        return f"instance_json_format_memory_{name}"

    # ================================================================================================
    # Batch Operations - for cascading deletion when a Narrative is deleted
    # ================================================================================================

    async def delete_all_memory_for_narrative(
        self,
        narrative_id: str,
        module_names: Optional[List[str]] = None
    ) -> bool:
        """
        Delete all Memory for the specified Narrative (cascading deletion)

        Called when a Narrative is deleted to clean up all related Memory.

        Args:
            narrative_id: Narrative ID
            module_names: List of Modules to clean up (optional, must be specified manually if not provided)

        Returns:
            bool: Whether all deletions were successful
        """
        success = True

        # Delete JSON Format Memory for each Module
        if module_names:
            for module_name in module_names:
                if not await self.delete_json_format_memory(module_name, narrative_id):
                    success = False

        # Delete Report Memory
        if not await self.delete_report_memory(narrative_id):
            success = False

        return success
