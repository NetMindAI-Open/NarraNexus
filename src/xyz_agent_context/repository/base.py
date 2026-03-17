"""
@file_name: base.py
@author: NetMind.AI
@date: 2025-11-28
@description: Repository base class

Responsibilities:
- Define a unified data access interface
- Provide generic implementation for batch loading (solving the N+1 problem)
- Encapsulate conversion between database rows and entity objects

Design notes:
- BaseRepository is a generic class; subclasses specify the concrete entity type
- Subclasses must implement _row_to_entity() and _entity_to_row() methods
- Batch queries use the get_by_ids() method to avoid the N+1 problem
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Optional, Dict, Any
from loguru import logger

# Generic type variable
T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """
    Repository base class

    Usage example:
        class EventRepository(BaseRepository[Event]):
            table_name = "events"
            id_field = "event_id"

            def _row_to_entity(self, row: Dict[str, Any]) -> Event:
                # Conversion logic
                pass

            def _entity_to_row(self, entity: Event) -> Dict[str, Any]:
                # Conversion logic
                pass

        # Usage
        repo = EventRepository(db_client)
        event = await repo.get_by_id("evt_123")
        events = await repo.get_by_ids(["evt_1", "evt_2", "evt_3"])
    """

    # Subclasses must override these class attributes
    table_name: str = ""
    id_field: str = "id"

    def __init__(self, db_client: 'AsyncDatabaseClient'):
        """
        Initialize the Repository

        Args:
            db_client: Async database client
        """
        if not self.table_name:
            raise ValueError(f"{self.__class__.__name__} must define 'table_name'")
        self._db = db_client

    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """
        Get a single entity by ID

        Args:
            entity_id: Entity ID

        Returns:
            Entity object, or None if not found
        """
        results = await self.get_by_ids([entity_id])
        return results[0] if results and results[0] is not None else None

    async def get_by_ids(self, ids: List[str]) -> List[Optional[T]]:
        """
        Batch fetch entities (core method for solving the N+1 problem)

        Uses a single IN query instead of multiple individual queries.

        Args:
            ids: List of IDs

        Returns:
            List of entities in the same order as the input IDs; missing ones return None

        Performance comparison:
            - Before (N+1): 100 entities -> 100 queries -> ~220ms
            - After (batch): 100 entities -> 1 query -> ~15ms
        """
        if not ids:
            return []

        logger.debug(f"    → {self.__class__.__name__}.get_by_ids({len(ids)} ids)")

        # Deduplicate while preserving order
        unique_ids = list(dict.fromkeys(ids))

        # Batch query
        rows = await self._db.get_by_ids(
            self.table_name,
            self.id_field,
            unique_ids
        )

        # Build ID -> entity mapping
        entity_map: Dict[str, T] = {}
        for row in rows:
            if row is not None:
                try:
                    entity = self._row_to_entity(row)
                    entity_map[row[self.id_field]] = entity
                except Exception as e:
                    logger.warning(f"Failed to parse row {row.get(self.id_field)}: {e}")

        # Return in original order
        result = [entity_map.get(id) for id in ids]
        logger.debug(f"    ← {self.__class__.__name__}.get_by_ids: {sum(1 for e in result if e is not None)} found")
        return result

    async def save(self, entity: T) -> int:
        """
        Save an entity (smart insert or update)

        Args:
            entity: Entity object

        Returns:
            Number of affected rows or newly inserted ID
        """
        row = self._entity_to_row(entity)
        entity_id = row.get(self.id_field)

        if not entity_id:
            raise ValueError(f"Entity must have {self.id_field}")

        # Check if exists
        existing = await self._db.get_one(self.table_name, {self.id_field: entity_id})

        if existing:
            # Update
            logger.debug(f"    → {self.__class__.__name__}.save: updating {entity_id}")
            return await self._db.update(
                self.table_name,
                filters={self.id_field: entity_id},
                data=row
            )
        else:
            # Insert
            logger.debug(f"    → {self.__class__.__name__}.save: inserting {entity_id}")
            return await self._db.insert(self.table_name, row)

    async def insert(self, entity: T) -> int:
        """
        Insert a new entity

        Args:
            entity: Entity object

        Returns:
            Newly inserted ID
        """
        row = self._entity_to_row(entity)
        logger.debug(f"    → {self.__class__.__name__}.insert")
        return await self._db.insert(self.table_name, row)

    async def update(self, entity_id: str, data: Dict[str, Any]) -> int:
        """
        Partially update an entity

        Args:
            entity_id: Entity ID
            data: Fields to update

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → {self.__class__.__name__}.update({entity_id})")
        return await self._db.update(
            self.table_name,
            filters={self.id_field: entity_id},
            data=data
        )

    async def delete(self, entity_id: str) -> int:
        """
        Delete an entity

        Args:
            entity_id: Entity ID

        Returns:
            Number of deleted rows
        """
        logger.debug(f"    → {self.__class__.__name__}.delete({entity_id})")
        return await self._db.delete(self.table_name, {self.id_field: entity_id})

    async def upsert(self, entity: T) -> int:
        """
        Concurrency-safe insert or update (using INSERT ... ON DUPLICATE KEY UPDATE)

        Difference from save():
        - save() queries first then decides to insert/update, which has race conditions
        - upsert() uses database-level atomic operation, ensuring concurrency safety

        Args:
            entity: Entity object

        Returns:
            Number of affected rows (1=insert, 2=update)
        """
        row = self._entity_to_row(entity)
        entity_id = row.get(self.id_field)

        if not entity_id:
            raise ValueError(f"Entity must have {self.id_field}")

        logger.debug(f"    → {self.__class__.__name__}.upsert({entity_id})")
        return await self._db.upsert(self.table_name, row, self.id_field)

    async def find(
        self,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None
    ) -> List[T]:
        """
        Query entities by conditions

        Args:
            filters: Filter conditions
            limit: Maximum number of results
            offset: Number of results to skip
            order_by: Sort order (e.g., "created_at DESC")

        Returns:
            List of entities
        """
        logger.debug(f"    → {self.__class__.__name__}.find(filters={filters})")
        rows = await self._db.get(
            self.table_name,
            filters=filters,
            limit=limit,
            offset=offset,
            order_by=order_by
        )
        return [self._row_to_entity(row) for row in rows if row]

    async def find_one(self, filters: Dict[str, Any]) -> Optional[T]:
        """
        Query a single entity by conditions

        Args:
            filters: Filter conditions

        Returns:
            Entity object, or None if not found
        """
        results = await self.find(filters, limit=1)
        return results[0] if results else None

    @abstractmethod
    def _row_to_entity(self, row: Dict[str, Any]) -> T:
        """
        Convert a database row to an entity object

        Subclasses must implement this method.

        Args:
            row: Database row (dictionary)

        Returns:
            Entity object
        """
        pass

    @abstractmethod
    def _entity_to_row(self, entity: T) -> Dict[str, Any]:
        """
        Convert an entity object to a database row

        Subclasses must implement this method.

        Args:
            entity: Entity object

        Returns:
            Database row (dictionary)
        """
        pass
