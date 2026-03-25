"""
@file_name: social_network_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: Social Network Repository - Data access layer for social network entities

Responsibilities:
- CRUD operations for social network entities
- Search entities by tags
- Relationship strength and interaction count updates
"""

import json
from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema import SocialNetworkEntity


class SocialNetworkRepository(BaseRepository[SocialNetworkEntity]):
    """
    Social Network Repository implementation

    Refactoring notes:
    - Uses the instance_social_entities table
    - Data isolation via instance_id

    Usage example:
        repo = SocialNetworkRepository(db_client)

        # Get a single entity
        entity = await repo.get_entity("user_123", "social_abc123")

        # Get all social network entities for an Instance
        entities = await repo.get_all_entities("social_abc123")

        # Add an entity
        await repo.add_entity(entity)

        # Search by tags
        results = await repo.search_by_tags("social_abc123", "expert:recommendation")
    """

    table_name = "instance_social_entities"
    id_field = "id"

    # JSON fields (2026-01-15 Feature 2.2.1: added related_job_ids; Persona: added extra_data; Feature 2.3: added embedding)
    _json_fields = {"identity_info", "contact_info", "tags", "expertise_domains", "related_job_ids", "extra_data", "embedding"}

    async def get_entity(
        self,
        entity_id: str,
        instance_id: str
    ) -> Optional[SocialNetworkEntity]:
        """
        Get a social network entity

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)

        Returns:
            SocialNetworkEntity or None
        """
        logger.debug(f"    → SocialNetworkRepository.get_entity({entity_id}, {instance_id})")
        return await self.find_one({
            "entity_id": entity_id,
            "instance_id": instance_id
        })

    async def get_all_entities(
        self,
        instance_id: str,
        entity_type: Optional[str] = None,
        limit: int = 100
    ) -> List[SocialNetworkEntity]:
        """
        Get all social network entities for an Instance

        Args:
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            entity_type: Entity type filter (optional)
            limit: Result count limit

        Returns:
            List of SocialNetworkEntity
        """
        logger.debug(f"    → SocialNetworkRepository.get_all_entities({instance_id})")

        filters = {"instance_id": instance_id}
        if entity_type:
            filters["entity_type"] = entity_type

        return await self.find(
            filters=filters,
            limit=limit,
            order_by="updated_at DESC"
        )

    async def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        instance_id: str,
        entity_name: Optional[str] = None,
        entity_description: Optional[str] = None,
        identity_info: Optional[Dict[str, Any]] = None,
        contact_info: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        expertise_domains: Optional[List[str]] = None
    ) -> int:
        """
        Add a social network entity

        Args:
            entity_id: Entity ID
            entity_type: Entity type (user | agent)
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            entity_name: Entity name
            entity_description: Entity description
            identity_info: Identity information dictionary
            contact_info: Contact information dictionary
            tags: Tag list
            expertise_domains: Expertise domain list

        Returns:
            Inserted record ID
        """
        logger.debug(f"    → SocialNetworkRepository.add_entity({entity_id})")

        entity = SocialNetworkEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            instance_id=instance_id,
            entity_name=entity_name,
            entity_description=entity_description,
            identity_info=identity_info or {},
            contact_info=contact_info or {},
            tags=tags or [],
            expertise_domains=expertise_domains or [],
            relationship_strength=0.0,
            interaction_count=0
        )

        return await self.insert(entity)

    async def update_entity_info(
        self,
        entity_id: str,
        instance_id: str,
        updates: Dict[str, Any]
    ) -> int:
        """
        Update social network entity information

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            updates: Dictionary of fields to update

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → SocialNetworkRepository.update_entity_info({entity_id})")

        # If updates is empty, return directly to avoid generating invalid SQL
        if not updates:
            logger.debug(f"    → No updates to apply for entity {entity_id}")
            return 0

        # Serialize JSON fields
        for field in self._json_fields:
            if field in updates and not isinstance(updates[field], str):
                updates[field] = json.dumps(updates[field], ensure_ascii=False)

        # Use raw SQL for update (because compound conditions are needed)
        conditions = []
        params = []
        for key, value in updates.items():
            conditions.append(f"`{key}` = %s")
            params.append(value)

        # Check again (all fields may have been filtered out after serialization)
        if not conditions:
            logger.debug(f"    → No valid update conditions for entity {entity_id}")
            return 0

        params.extend([entity_id, instance_id])

        query = f"""
            UPDATE {self.table_name}
            SET {', '.join(conditions)}
            WHERE entity_id = %s AND instance_id = %s
        """

        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    async def delete_entity(
        self,
        entity_id: str,
        instance_id: str
    ) -> int:
        """
        Delete a social network entity

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → SocialNetworkRepository.delete_entity({entity_id})")

        query = f"""
            DELETE FROM {self.table_name}
            WHERE entity_id = %s AND instance_id = %s
        """

        result = await self._db.execute(query, params=(entity_id, instance_id), fetch=False)
        return result if isinstance(result, int) else 0

    async def search_by_tags(
        self,
        instance_id: str,
        search_keyword: str,
        limit: int = 10
    ) -> List[SocialNetworkEntity]:
        """
        Search entities by tags

        Args:
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            search_keyword: Search keyword (can be any part of a tag)
            limit: Result count limit

        Returns:
            List of matching SocialNetworkEntity

        Examples:
            search_keyword="expert:recommendation" -> Find recommendation system experts
            search_keyword="domain:machine_learning" -> Find people related to machine learning
        """
        logger.debug(f"    → SocialNetworkRepository.search_by_tags({instance_id}, {search_keyword})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE instance_id = %s
            AND JSON_SEARCH(tags, 'one', %s) IS NOT NULL
            ORDER BY relationship_strength DESC
            LIMIT %s
        """

        results = await self._db.execute(
            query,
            params=(instance_id, f"%{search_keyword}%", limit),
            fetch=True
        )

        return [self._row_to_entity(row) for row in results]

    async def semantic_search(
        self,
        instance_id: str,
        query_embedding: List[float],
        limit: int = 10,
        min_similarity: float = 0.3
    ) -> List[tuple[SocialNetworkEntity, float]]:
        """
        Search entities by semantic vector (Feature 2.3)

        Calculates cosine similarity at the application layer for sorting (MySQL does not natively support vector operations).

        Args:
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            query_embedding: Embedding vector of the query text
            limit: Result count limit
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of (SocialNetworkEntity, similarity_score) tuples, sorted by similarity descending

        Examples:
            query_embedding = await get_embedding("Who recently showed purchase intent?")
            results = await repo.semantic_search(instance_id, query_embedding)
            for entity, score in results:
                print(f"{entity.entity_name}: {score:.3f}")
        """
        logger.debug(f"    → SocialNetworkRepository.semantic_search({instance_id})")

        # Get all entities for this instance
        query = f"""
            SELECT * FROM {self.table_name}
            WHERE instance_id = %s
        """

        results = await self._db.execute(
            query,
            params=(instance_id,),
            fetch=True
        )

        if not results:
            return []

        from xyz_agent_context.agent_framework.llm_api.embedding import cosine_similarity
        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import (
            use_embedding_store,
            get_stored_embeddings_batch,
        )

        entity_ids = [row.get("entity_id") for row in results if row.get("entity_id")]
        new_system = use_embedding_store()
        store_vectors: dict = {}
        if new_system:
            store_vectors = await get_stored_embeddings_batch("entity", entity_ids)

        entities_with_scores = []
        for row in results:
            entity = self._row_to_entity(row)
            if new_system:
                vector = store_vectors.get(entity.entity_id) if entity.entity_id else None
            else:
                vector = entity.embedding
            if vector:
                similarity = cosine_similarity(query_embedding, vector)
                if similarity >= min_similarity:
                    entities_with_scores.append((entity, similarity))

        # Sort by similarity descending and take the top limit results
        entities_with_scores.sort(key=lambda x: x[1], reverse=True)
        return entities_with_scores[:limit]

    async def keyword_search(
        self,
        instance_id: str,
        keyword: str,
        limit: int = 10
    ) -> List[SocialNetworkEntity]:
        """
        Search entities by keyword

        Searches for entities containing the keyword in entity_name, entity_description, and tags.

        Args:
            instance_id: SocialNetworkModule's instance_id
            keyword: Search keyword
            limit: Result count limit

        Returns:
            List of matching SocialNetworkEntity
        """
        logger.debug(f"    → SocialNetworkRepository.keyword_search({instance_id}, '{keyword}')")

        # Use LIKE for fuzzy matching
        search_pattern = f"%{keyword}%"

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE instance_id = %s
              AND (
                  entity_name LIKE %s
                  OR entity_description LIKE %s
                  OR tags LIKE %s
              )
            ORDER BY interaction_count DESC, updated_at DESC
            LIMIT %s
        """

        results = await self._db.execute(
            query,
            params=(instance_id, search_pattern, search_pattern, search_pattern, limit),
            fetch=True
        )

        if not results:
            return []

        return [self._row_to_entity(row) for row in results]

    async def increment_interaction(
        self,
        entity_id: str,
        instance_id: str
    ) -> int:
        """
        Increment interaction count and update last interaction time

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → SocialNetworkRepository.increment_interaction({entity_id})")

        query = f"""
            UPDATE {self.table_name}
            SET interaction_count = interaction_count + 1,
                last_interaction_time = NOW()
            WHERE entity_id = %s AND instance_id = %s
        """

        result = await self._db.execute(
            query,
            params=(entity_id, instance_id),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    async def append_related_job_ids(
        self,
        entity_id: str,
        instance_id: str,
        job_ids: List[str]
    ) -> int:
        """
        Append job_ids to Entity's related_job_ids array

        Feature 2.2.1 implementation: Entity-side append method for Job-Entity bidirectional index

        Uses JSON_ARRAY_APPEND for atomic appending, avoiding duplicates.

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            job_ids: Job IDs to append

        Returns:
            Number of affected rows
        """
        logger.debug(
            f"    → SocialNetworkRepository.append_related_job_ids({entity_id}, "
            f"job_ids={job_ids})"
        )

        if not job_ids:
            return 0

        # Build JSON_ARRAY_APPEND query
        # MySQL 8.0+ supports JSON_ARRAY_APPEND, but deduplication is needed
        # Here we first read existing IDs, then compute deduplicated IDs, then update

        # 1. Read existing related_job_ids
        entity = await self.get_entity(entity_id, instance_id)
        if not entity:
            logger.warning(f"Entity {entity_id} not found, skipping append")
            return 0

        # 2. Compute deduplicated IDs
        existing_ids = set(entity.related_job_ids)
        new_ids = existing_ids.union(set(job_ids))

        # 3. Update database
        query = f"""
            UPDATE {self.table_name}
            SET related_job_ids = %s,
                updated_at = NOW()
            WHERE entity_id = %s AND instance_id = %s
        """

        result = await self._db.execute(
            query,
            params=(json.dumps(list(new_ids), ensure_ascii=False), entity_id, instance_id),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    async def remove_related_job_ids(
        self,
        entity_id: str,
        instance_id: str,
        job_ids: List[str]
    ) -> int:
        """
        Remove job_ids from Entity's related_job_ids array

        Feature 2.2.1 implementation: Entity-side removal method for Job-Entity bidirectional index

        Args:
            entity_id: Entity ID
            instance_id: Instance ID (SocialNetworkModule's instance_id)
            job_ids: Job IDs to remove

        Returns:
            Number of affected rows
        """
        logger.debug(
            f"    → SocialNetworkRepository.remove_related_job_ids({entity_id}, "
            f"job_ids={job_ids})"
        )

        if not job_ids:
            return 0

        # 1. Read existing related_job_ids
        entity = await self.get_entity(entity_id, instance_id)
        if not entity:
            logger.warning(f"Entity {entity_id} not found, skipping remove")
            return 0

        # 2. Compute IDs after removal
        existing_ids = set(entity.related_job_ids)
        remaining_ids = existing_ids - set(job_ids)

        # 3. Update database
        query = f"""
            UPDATE {self.table_name}
            SET related_job_ids = %s,
                updated_at = NOW()
            WHERE entity_id = %s AND instance_id = %s
        """

        result = await self._db.execute(
            query,
            params=(json.dumps(list(remaining_ids), ensure_ascii=False), entity_id, instance_id),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    def _row_to_entity(self, row: Dict[str, Any]) -> SocialNetworkEntity:
        """
        Convert a database row to a SocialNetworkEntity object

        Refactoring notes (2026-01-15 Feature 2.2.1):
        - Added related_job_ids field parsing

        Refactoring notes (2026-01-16 Feature 2.3):
        - Added embedding field parsing
        """
        # Parse JSON fields
        identity_info = self._parse_json_field(row.get("identity_info"), {})
        contact_info = self._parse_json_field(row.get("contact_info"), {})
        tags = self._parse_json_field(row.get("tags"), [])
        expertise_domains = self._parse_json_field(row.get("expertise_domains"), [])
        related_job_ids = self._parse_json_field(row.get("related_job_ids"), [])  # Feature 2.2.1
        extra_data = self._parse_json_field(row.get("extra_data"), {})
        embedding = self._parse_json_field(row.get("embedding"), None)  # Feature 2.3

        return SocialNetworkEntity(
            id=row.get("id"),
            instance_id=row["instance_id"],
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            entity_name=row.get("entity_name"),
            entity_description=row.get("entity_description"),
            identity_info=identity_info,
            contact_info=contact_info,
            relationship_strength=row.get("relationship_strength", 0.0),
            interaction_count=row.get("interaction_count", 0),
            last_interaction_time=row.get("last_interaction_time"),
            tags=tags,
            expertise_domains=expertise_domains,
            related_job_ids=related_job_ids,  # Feature 2.2.1
            embedding=embedding,  # Feature 2.3
            persona=row.get("persona"),
            extra_data=extra_data,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: SocialNetworkEntity) -> Dict[str, Any]:
        """
        Convert a SocialNetworkEntity object to a database row

        Refactoring notes (2026-01-15 Feature 2.2.1):
        - Added related_job_ids field serialization

        Refactoring notes (2026-01-16 Feature 2.3):
        - Added embedding field serialization
        """
        return {
            "instance_id": entity.instance_id,
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "entity_name": entity.entity_name,
            "entity_description": entity.entity_description,
            "identity_info": json.dumps(entity.identity_info, ensure_ascii=False),
            "contact_info": json.dumps(entity.contact_info, ensure_ascii=False),
            "relationship_strength": entity.relationship_strength,
            "interaction_count": entity.interaction_count,
            "last_interaction_time": entity.last_interaction_time,
            "tags": json.dumps(entity.tags, ensure_ascii=False),
            "expertise_domains": json.dumps(entity.expertise_domains, ensure_ascii=False),
            "related_job_ids": json.dumps(entity.related_job_ids, ensure_ascii=False),  # Feature 2.2.1
            "embedding": json.dumps(entity.embedding, ensure_ascii=False) if entity.embedding else None,  # Feature 2.3
            "persona": entity.persona,
            "extra_data": json.dumps(entity.extra_data, ensure_ascii=False),
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """
        Parse a JSON field

        Handles double-encoding cases (JSON string encoded as JSON again).

        Args:
            value: Field value (may be a str or an already parsed object)
            default: Default value

        Returns:
            Parsed value
        """
        if value is None:
            return default

        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                # Handle double-encoding: if parsed result is still a string, try parsing again
                if isinstance(parsed, str):
                    try:
                        parsed = json.loads(parsed)
                    except json.JSONDecodeError:
                        pass
                return parsed
            except json.JSONDecodeError:
                return default

        return value
