"""
Narrative CRUD operations implementation

@file_name: crud.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative creation, read, update, delete operations
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from ..config import config
from ..models import (
    Narrative,
    NarrativeActor,
    NarrativeActorType,
    NarrativeInfo,
    NarrativeType,
    PROVISIONAL_SUMMARY_PREFIXES,
)
from narranexus.platform.utils.text import truncate_text

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.repository import NarrativeRepository


class NarrativeCRUD:
    """
    Narrative CRUD operations

    Responsibilities:
    - Create Narrative
    - Load Narrative from database
    - Save Narrative to database
    - Query Narrative by conditions
    """

    def __init__(self, agent_id: str):
        """
        Initialize CRUD operations

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._database_client: Optional["AsyncDatabaseClient"] = None
        self._narrative_repository: Optional["NarrativeRepository"] = None

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._database_client = db_client

    async def _get_db_client(self) -> "AsyncDatabaseClient":
        """Get the database client (lazy loaded)"""
        if self._database_client is None:
            from narranexus.platform.utils.db.db_factory import get_db_client
            self._database_client = await get_db_client()
        return self._database_client

    async def _get_repository(self) -> "NarrativeRepository":
        """Get the Repository (lazy loaded)"""
        if self._narrative_repository is None:
            from narranexus.platform.repository import NarrativeRepository
            db = await self._get_db_client()
            self._narrative_repository = NarrativeRepository(db)
        return self._narrative_repository

    async def load_by_id(self, narrative_id: str) -> Optional[Narrative]:
        """
        Load a Narrative from the database

        Args:
            narrative_id: Narrative ID

        Returns:
            Narrative object, or None if not found
        """
        repo = await self._get_repository()
        return await repo.get_by_id(narrative_id)

    async def load_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Narrative]:
        """
        Load Narratives by Agent and User

        Args:
            agent_id: Agent ID
            user_id: User ID
            limit: Maximum number of results

        Returns:
            List of Narratives (sorted by updated_at descending)
        """
        repo = await self._get_repository()
        return await repo.get_by_agent_user(agent_id, user_id, limit)

    async def save(self, narrative: Narrative) -> int:
        """
        Save a Narrative to the database

        Args:
            narrative: Narrative object

        Returns:
            Number of affected rows
        """
        repo = await self._get_repository()
        result = await repo.save(narrative)
        await self._index_narrative(narrative)
        return result

    async def _index_narrative(self, narrative: Narrative) -> None:
        """Project this narrative's searchable surface (name + current_summary +
        topic_keywords) into the unified search index (memory_narrative), with a
        source_ref pointer back to the narrative. ONE-WAY, idempotent: every
        persist (save / upsert / create→save) flows through here, so this is the
        single write point — narratives stay searchable via `remember` and never
        go stale (design §6, projection kind). Best-effort: an index failure
        must never break narrative persistence."""
        try:
            from narranexus.platform.memory import MemoryEngine
            kws = list(narrative.topic_keywords or [])
            # Same searchable surface as narrative ROUTING — literally the same
            # function now (`Narrative.searchable_text`), not a second copy that
            # happens to list the same fields. It WAS a second copy, joining with
            # "\n" where routing joined with " "; equivalent only because the
            # tokenizer splits on whitespace.
            text = narrative.searchable_text()
            db = await self._get_db_client()
            await MemoryEngine(db, narrative.agent_id).index(
                "narrative", narrative.id, text, scope_type="agent", tags=kws,
            )
        except Exception as e:  # noqa: BLE001 — projection is best-effort enrichment
            logger.warning(f"narrative index failed (non-fatal): {e}")

    async def upsert(self, narrative: Narrative) -> int:
        """
        Concurrency-safe insert or update Narrative (using database-level atomic operation)

        Difference from save():
        - save() queries first then decides insert/update, which has race conditions
        - upsert() uses INSERT ... ON DUPLICATE KEY UPDATE, ensuring concurrency safety

        Args:
            narrative: Narrative object

        Returns:
            Number of affected rows (1=new insert, 2=updated existing record)
        """
        repo = await self._get_repository()
        result = await repo.upsert(narrative)
        await self._index_narrative(narrative)
        return result

    async def create(
        self,
        agent_id: str,
        user_id: str,
        narrative_type: NarrativeType = NarrativeType.CHAT,
        title: str = "New Narrative",
        description: str = "",
        actors: Optional[List[NarrativeActor]] = None,
        save_to_db: bool = True,
    ) -> Narrative:
        """
        Create a new Narrative

        Automatically creates main_chat instance and saves it to the module_instances table.

        Args:
            agent_id: Agent ID
            user_id: User ID
            narrative_type: Narrative type
            title: Title
            description: Description
            actors: List of actors
            save_to_db: Whether to save to database

        Returns:
            Newly created Narrative
        """
        from narranexus.platform.schema.module_schema import ModuleInstance, InstanceStatus
        from narranexus.platform.module_system import InstanceFactory

        # Create default actors
        if actors is None:
            actors = [
                NarrativeActor(id=user_id, type=NarrativeActorType.USER),
                NarrativeActor(id=agent_id, type=NarrativeActorType.AGENT),
            ]

        # Create Narrative Info.
        #
        # `description` is CLAMPED here rather than at any one call site: this
        # is the single funnel every writer passes through — routing's
        # `create_from_query`, the LLM's `create_narrative` signal in
        # `step_4_persist_results` (text straight from tool arguments), and the
        # HTTP route. Prod carries descriptions up to 198,398 characters, and
        # `description` is in the BM25 index while the updater never rewrites
        # it, so an unbounded write here is a permanent fossil. Its two
        # siblings at the routing call site were already truncated; this one
        # was not.
        #
        # `current_summary` is a PLACEHOLDER, not a record — see
        # `models.PROVISIONAL_SUMMARY_PREFIXES`, which the retirement rule
        # reads so a thread whose updater never succeeds keeps its description.
        narrative_info = NarrativeInfo(
            name=title,
            description=truncate_text(description, config.DESCRIPTION_MAX_LENGTH),
            current_summary=f"{PROVISIONAL_SUMMARY_PREFIXES[0]}{title}",
            actors=actors,
        )

        # Generate unique ID
        narrative_id = f"nar_{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        # Get database client and InstanceFactory
        db_client = await self._get_db_client()
        instance_factory = InstanceFactory(db_client)

        # Ensure agent-level Instances exist (awareness, social_network)
        await instance_factory.ensure_agent_instances_exist(agent_id)

        # Create ChatModule instance and save to database
        # 2026-01-21 P1-1: No longer stores main_chat_instance_id; associations managed via link table
        # create_chat_instance automatically links the instance to the narrative
        chat_instance_record = await instance_factory.create_chat_instance(
            agent_id=agent_id,
            user_id=user_id,
            narrative_id=narrative_id,
            description=f"Chat instance for user {user_id}"
        )

        # For compatibility, also create in-memory ModuleInstance object
        chat_instance = ModuleInstance(
            instance_id=chat_instance_record.instance_id,
            module_class=chat_instance_record.module_class,
            description=f"Chat instance for user {user_id}",
            status=InstanceStatus.ACTIVE,
            agent_id=agent_id,
            dependencies=[],
            created_at=now,
            last_used_at=now
        )

        # Create Narrative (no longer uses main_chat_instance_id)
        narrative = Narrative(
            id=narrative_id,
            type=narrative_type,
            agent_id=agent_id,
            narrative_info=narrative_info,
            main_chat_instance_id=None,  # 2026-01-21 P1-1: Deprecated
            active_instances=[chat_instance],
            event_ids=[],
            dynamic_summary=[],
            env_variables={},
            related_narrative_ids=[],
            created_at=now,
            updated_at=now,
            topic_keywords=[],
            topic_hint="",
        )

        logger.info(f"Created Narrative: {narrative_id} with chat_instance: {chat_instance_record.instance_id}")

        if save_to_db:
            await self.save(narrative)

        return narrative
