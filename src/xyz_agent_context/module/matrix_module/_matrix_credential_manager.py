"""
@file_name: _matrix_credential_manager.py
@author: Bin Liang
@date: 2026-03-10
@description: Matrix credential management

Manages Agent credentials for NexusMatrix Server:
- Store/retrieve credentials from the matrix_credentials table
- Auto-register agents on NexusMatrix when they're created
- Track polling state (sync_token, next_poll_time) for MatrixTrigger

This is a private implementation — external code should access it via MatrixModule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.utils import DatabaseClient


@dataclass
class MatrixCredential:
    """
    A single Agent's Matrix credentials and polling state.

    Stored in the matrix_credentials table. Used by MatrixTrigger
    for authentication and adaptive polling.
    """
    agent_id: str
    nexus_agent_id: str = ""
    api_key: str = ""
    matrix_user_id: str = ""
    server_url: str = ""
    sync_token: str = ""
    next_poll_time: Optional[datetime] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MatrixCredentialManager:
    """
    CRUD operations for Matrix credentials.

    Works with the matrix_credentials table in the NarraNexus database.
    """

    TABLE = "matrix_credentials"

    def __init__(self, db: DatabaseClient):
        self.db = db

    async def get_credential(self, agent_id: str) -> Optional[MatrixCredential]:
        """
        Get a single Agent's Matrix credential.

        Args:
            agent_id: Agent ID

        Returns:
            MatrixCredential, or None if not found
        """
        row = await self.db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_credential(row)

    async def get_all_active(self) -> List[MatrixCredential]:
        """
        Get all active credentials (for MatrixTrigger polling).

        Returns:
            List of active MatrixCredential instances
        """
        rows = await self.db.get_many(self.TABLE, {"is_active": True})
        return [self._row_to_credential(r) for r in rows]

    async def get_due_credentials(self, now: Optional[datetime] = None) -> List[MatrixCredential]:
        """
        Get credentials that are due for polling (next_poll_time <= now).

        Args:
            now: Current time (defaults to utcnow)

        Returns:
            List of due MatrixCredential instances
        """
        if now is None:
            now = datetime.now(timezone.utc)

        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE is_active = TRUE
              AND (next_poll_time IS NULL OR next_poll_time <= %s)
            ORDER BY next_poll_time ASC
        """
        rows = await self.db.execute(query, (now,))
        return [self._row_to_credential(r) for r in rows]

    async def save_credential(self, cred: MatrixCredential) -> None:
        """
        Insert or update a credential (upsert).

        Args:
            cred: MatrixCredential to save
        """
        now = datetime.now(timezone.utc)
        data = {
            "agent_id": cred.agent_id,
            "nexus_agent_id": cred.nexus_agent_id,
            "api_key": cred.api_key,
            "matrix_user_id": cred.matrix_user_id,
            "server_url": cred.server_url,
            "sync_token": cred.sync_token,
            "next_poll_time": cred.next_poll_time,
            "is_active": cred.is_active,
            "updated_at": now,
        }

        existing = await self.db.get_one(self.TABLE, {"agent_id": cred.agent_id})
        if existing:
            await self.db.update(self.TABLE, {"agent_id": cred.agent_id}, data)
        else:
            data["created_at"] = now
            await self.db.insert(self.TABLE, data)

        logger.debug(f"Saved Matrix credential for agent {cred.agent_id}")

    async def update_sync_token(self, agent_id: str, sync_token: str) -> None:
        """
        Update the sync token after a successful sync.

        Args:
            agent_id: Agent ID
            sync_token: New sync token from NexusMatrix
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"sync_token": sync_token, "updated_at": datetime.now(timezone.utc)},
        )

    async def update_next_poll_time(
        self, agent_id: str, next_time: datetime
    ) -> None:
        """
        Update the next polling time (adaptive scheduling).

        Args:
            agent_id: Agent ID
            next_time: Next polling datetime
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"next_poll_time": next_time, "updated_at": datetime.now(timezone.utc)},
        )

    async def deactivate(self, agent_id: str) -> None:
        """
        Mark a credential as inactive (stop polling).

        Args:
            agent_id: Agent ID
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"is_active": False, "updated_at": datetime.now(timezone.utc)},
        )

    async def delete(self, agent_id: str) -> None:
        """
        Delete a credential entirely.

        Args:
            agent_id: Agent ID
        """
        await self.db.delete(self.TABLE, {"agent_id": agent_id})

    @staticmethod
    def _row_to_credential(row: Dict[str, Any]) -> MatrixCredential:
        """Convert a database row to a MatrixCredential dataclass."""
        return MatrixCredential(
            agent_id=row.get("agent_id", ""),
            nexus_agent_id=row.get("nexus_agent_id", ""),
            api_key=row.get("api_key", ""),
            matrix_user_id=row.get("matrix_user_id", ""),
            server_url=row.get("server_url", ""),
            sync_token=row.get("sync_token", ""),
            next_poll_time=row.get("next_poll_time"),
            is_active=bool(row.get("is_active", True)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


async def ensure_agent_registered(
    db: "DatabaseClient",
    agent_id: str,
    server_url: str = "",
    force: bool = False,
) -> Optional[MatrixCredential]:
    """
    Ensure an agent is registered on NexusMatrix and return its credential.

    Unified entry point for all auto-registration scenarios:
    - MCP tool first-use auto-registration
    - Explicit matrix_register tool call
    - Channel sender registration

    Flow:
    1. Check if credential already exists (return immediately if not forced)
    2. Look up agent info from database
    3. Call register_agent_on_matrix to register
    4. Return the new credential

    Args:
        db: Database client
        agent_id: Agent ID (business key, not auto-increment PK)
        server_url: NexusMatrix server URL (default from module constant)
        force: If True, delete existing credential and re-register

    Returns:
        MatrixCredential on success, None on failure
    """
    import os
    from .contact_card import ContactCard

    cred_mgr = MatrixCredentialManager(db)

    # Look up agent info (needed for both existing and new registration)
    from xyz_agent_context.repository import AgentRepository
    agent_repo = AgentRepository(db)
    agent = await agent_repo.get_agent(agent_id)
    if not agent:
        logger.warning(f"Agent {agent_id} not found in database, cannot register on Matrix")
        return None

    agent_name = agent.agent_name or agent_id

    # Compute workspace_path from settings (Agent model doesn't store it)
    workspace_path = ""
    try:
        from xyz_agent_context.settings import settings
        workspace_path = os.path.join(
            settings.base_working_path,
            f"{agent_id}_{agent.created_by}",
        )
    except Exception:
        logger.debug(f"Could not compute workspace_path for {agent_id}, skipping contact card")

    # Check existing credential
    existing = await cred_mgr.get_credential(agent_id)
    if existing and not force:
        # Ensure contact card exists and has latest agent name
        _sync_contact_card(workspace_path, agent_id, agent_name, existing)
        return existing

    # Force mode: delete existing first
    if existing and force:
        await cred_mgr.delete(agent_id)
        logger.info(f"Deleted existing Matrix credential for {agent_id} (force re-register)")

    # Register
    ok = await register_agent_on_matrix(
        db=db,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_description=agent.agent_description or "",
        workspace_path=workspace_path,
        server_url=server_url,
    )

    if ok:
        cred = await cred_mgr.get_credential(agent_id)
        logger.info(f"Registered {agent_id} on NexusMatrix: {cred.matrix_user_id if cred else '?'}")
        return cred

    logger.warning(f"Registration failed for {agent_id}, NexusMatrix may be unavailable")
    return None


def _sync_contact_card(
    workspace_path: str,
    agent_id: str,
    agent_name: str,
    cred: MatrixCredential,
) -> None:
    """
    Ensure contact_card.yaml exists and has the latest agent name.

    Called on every ensure_agent_registered to keep contact cards
    in sync with current agent names (fixes stale "New Agent" names).
    """
    if not workspace_path:
        return

    from .contact_card import ContactCard

    try:
        card = ContactCard(workspace_path)
        card.update({
            "agent_id": agent_id,
            "name": agent_name,
            "matrix": {
                "user_id": cred.matrix_user_id,
                "server_url": cred.server_url,
            },
        })
    except Exception as e:
        logger.debug(f"Failed to sync contact card for {agent_id}: {e}")


async def register_agent_on_matrix(
    db: "DatabaseClient",
    agent_id: str,
    agent_name: str,
    agent_description: str,
    workspace_path: str,
    server_url: str = "",
) -> bool:
    """
    Register an agent on NexusMatrix Server and persist credentials + contact card.

    Called during agent creation. Handles the full flow:
    1. Call NexusMatrix /registry/register API
    2. Save credentials to matrix_credentials table
    3. Write contact_card.yaml to workspace

    Args:
        db: Database client
        agent_id: Agent ID
        agent_name: Agent display name
        agent_description: Agent description
        workspace_path: Agent workspace directory path
        server_url: NexusMatrix server URL (default from module constant)

    Returns:
        True if registration succeeded, False otherwise
    """
    from .matrix_client import NexusMatrixClient
    from .matrix_module import DEFAULT_SERVER_URL
    from .contact_card import ContactCard

    url = server_url or DEFAULT_SERVER_URL

    # Skip if already registered
    cred_mgr = MatrixCredentialManager(db)
    existing = await cred_mgr.get_credential(agent_id)
    if existing:
        logger.debug(f"Agent {agent_id} already registered on Matrix, skipping")
        return True

    client = NexusMatrixClient(server_url=url)
    try:
        # 1. Register on NexusMatrix Server (use agent_id as Matrix username)
        result = await client.register_agent(
            agent_name=agent_name,
            description=agent_description,
            capabilities=["chat", "messaging"],
            owner=agent_id,
            preferred_username=agent_id,
        )
        if not result:
            logger.warning(f"Matrix registration failed for agent {agent_id}")
            return False

        api_key = result.get("api_key", "")
        matrix_user_id = result.get("matrix_user_id", "")
        nexus_agent_id = result.get("agent_id", "")

        if not api_key or not matrix_user_id:
            logger.warning(f"Matrix registration returned incomplete data for {agent_id}: {result}")
            return False

        # 2. Save credentials to database
        cred = MatrixCredential(
            agent_id=agent_id,
            nexus_agent_id=nexus_agent_id,
            api_key=api_key,
            matrix_user_id=matrix_user_id,
            server_url=url,
        )
        await cred_mgr.save_credential(cred)

        # 3. Write contact card to workspace
        if workspace_path:
            card = ContactCard(workspace_path)
            card.update({
                "agent_id": agent_id,
                "name": agent_name,
                "matrix": {
                    "user_id": matrix_user_id,
                    "server_url": url,
                },
            })

        logger.info(
            f"Agent {agent_id} registered on Matrix: {matrix_user_id}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to register agent {agent_id} on Matrix: {e}")
        return False
    finally:
        await client.close()
