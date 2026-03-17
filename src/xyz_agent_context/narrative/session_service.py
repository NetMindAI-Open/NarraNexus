#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Session Manager - Manages user session state

Features:
1. Session creation, lookup, and update
2. Timeout detection and automatic cleanup
3. File-based persistent storage (JSON format)
4. In-memory cache + file persistence dual-layer storage

Design approach:
- Uses an in-memory dictionary as cache, key is (user_id, agent_id)
- Simultaneously persists Sessions to JSON files
- On lookup, checks memory first, then files
- Periodically cleans up expired Sessions (avoids memory leaks and disk usage)
- Thread-safe (uses asyncio.Lock to protect shared state)

File storage format:
- Directory: {project_root}/sessions/
- Filename: {agent_id}_{user_id}.json
- Content: JSON serialization of ConversationSession

Author: AI Assistant
Date: 2025-12-02
Phase: Phase 2 - Session management (enhanced - file persistence)
"""

from __future__ import annotations

import asyncio
import json
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from loguru import logger

from .models import ConversationSession
from .config import config


def _ensure_timezone_aware(dt: datetime) -> datetime:
    """
    Ensure a datetime is timezone-aware

    If it is a naive datetime, assumes UTC and adds timezone info
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class SessionService:
    """
    Session Manager - Manages the lifecycle of user sessions

    Main features:
    1. get_or_create_session() - Get or create a Session
    2. update_session() - Update Session state
    3. cleanup_expired_sessions() - Clean up expired Sessions
    4. get_session() - Get a Session by ID
    5. list_all_sessions() - List all Sessions
    6. delete_session() - Delete a Session

    Storage strategy:
    - Dual-layer storage: in-memory cache + file persistence
    - File format: JSON
    - Supports multi-Agent Runtime isolation (via file locks)

    Thread safety:
    - Uses asyncio.Lock to protect in-memory dictionaries
    - Uses fcntl.flock to protect file operations

    Example:
        >>> service = SessionService()
        >>> session = await manager.get_or_create_session("user_123", "agent_456")
        >>> print(f"Session ID: {session.session_id}")
        >>>
        >>> # Update Session
        >>> await manager.update_session(
        ...     session.session_id,
        ...     last_query="new query",
        ...     current_narrative_id="nar_abc123"
        ... )
        >>>
        >>> # Clean up expired Sessions (recommended to call periodically)
        >>> await manager.cleanup_expired_sessions()
    """

    def __init__(self, session_dir: Optional[str] = None):
        """
        Initialize SessionService

        Args:
            session_dir: Session file storage directory, defaults to {project_root}/sessions/

        Internal state:
        - _sessions: In-memory cache, key=(user_id, agent_id)
        - _session_by_id: Fast lookup by session_id
        - _session_dir: File storage directory
        - _lock: asyncio.Lock, protects concurrent access
        """
        # Set storage directory
        if session_dir is None:
            project_root = Path(__file__).resolve().parents[3]
            self._session_dir = project_root / "sessions"
        else:
            self._session_dir = Path(session_dir)

        # Ensure directory exists
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Primary storage: (user_id, agent_id) -> ConversationSession
        self._sessions: Dict[Tuple[str, str], ConversationSession] = {}

        # Secondary index: session_id -> ConversationSession (for fast lookup)
        self._session_by_id: Dict[str, ConversationSession] = {}

        # Concurrency control lock
        self._lock = asyncio.Lock()

        logger.info("SessionService initialized")
        logger.info(f"  Session storage directory: {self._session_dir}")

    def _get_session_file_path(self, agent_id: str, user_id: str) -> Path:
        """Get the Session file path"""
        safe_name = f"{agent_id}_{user_id}".replace("/", "_").replace("\\", "_")
        return self._session_dir / f"{safe_name}.json"

    async def _load_session_from_file(self, agent_id: str, user_id: str) -> Optional[ConversationSession]:
        """Load a Session from file"""
        session_file = self._get_session_file_path(agent_id, user_id)

        if not session_file.exists():
            return None

        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    # Parse datetime fields, ensure timezone-aware
                    data['created_at'] = _ensure_timezone_aware(
                        datetime.fromisoformat(data['created_at'])
                    )
                    data['last_query_time'] = _ensure_timezone_aware(
                        datetime.fromisoformat(data['last_query_time'])
                    )
                    return ConversationSession(**data)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to load Session file {session_file}: {e}")
            return None

    async def _save_session_to_file(self, session: ConversationSession) -> None:
        """Save a Session to file"""
        session_file = self._get_session_file_path(session.agent_id, session.user_id)

        # Prepare JSON data
        data = session.model_dump()
        data['created_at'] = session.created_at.isoformat()
        data['last_query_time'] = session.last_query_time.isoformat()

        # Write with file lock
        with open(session_file, 'w', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    async def _delete_session_file(self, agent_id: str, user_id: str) -> bool:
        """Delete a Session file"""
        session_file = self._get_session_file_path(agent_id, user_id)
        if session_file.exists():
            session_file.unlink()
            return True
        return False

    async def save_session(self, session: ConversationSession) -> None:
        """
        Save a Session to file (public method)

        Architecture Plan B: AgentRuntime is responsible for Session management.
        After NarrativeService.select() updates Session state,
        AgentRuntime calls this method to persist.

        Args:
            session: Session object to save

        Example:
            >>> session = await session_manager.get_or_create_session(user_id, agent_id)
            >>> # ... NarrativeService.select() updates session object ...
            >>> await session_manager.save_session(session)
        """
        async with self._lock:
            # Update in-memory index
            self._session_by_id[session.session_id] = session
            key = (session.user_id, session.agent_id)  # Note: consistent key order with _sessions
            self._sessions[key] = session

            # Persist to file
            await self._save_session_to_file(session)
            logger.debug(f"Saved Session: {session.session_id}")

    async def get_or_create_session(
        self,
        user_id: str,
        agent_id: str
    ) -> ConversationSession:
        """
        Get or create a Session (with smart timeout detection)

        Logic flow:
        1. Check if Session exists in memory cache
        2. If not in memory, check if file exists
        3. If exists:
           - Check if timed out (time since last_query_time > SESSION_TIMEOUT)
           - Not timed out: return existing Session
           - Timed out: delete old Session, create new Session
        4. If not exists: create new Session and persist

        Timeout detection:
        - Uses config.SESSION_TIMEOUT setting (default 600 seconds = 10 minutes)
        - After timeout, treated as new conversation, creates new Session

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            ConversationSession: Current Session (may be newly created or reused)
        """
        async with self._lock:
            key = (user_id, agent_id)
            session: Optional[ConversationSession] = None

            # Step 1: Check memory cache
            if key in self._sessions:
                session = self._sessions[key]
            else:
                # Step 2: Check file
                session = await self._load_session_from_file(agent_id, user_id)
                if session:
                    # Load into memory cache
                    self._sessions[key] = session
                    self._session_by_id[session.session_id] = session
                    logger.debug(f"Loaded Session from file: {session.session_id}")

            # Step 3: Check timeout
            if session:
                elapsed = (datetime.now(timezone.utc) - session.last_query_time).total_seconds()

                if elapsed <= config.SESSION_TIMEOUT:
                    # Not timed out, reuse Session
                    logger.debug(
                        f"Reusing existing Session: {session.session_id} "
                        f"(user={user_id}, agent={agent_id}, time since last query={elapsed:.1f}s)"
                    )
                    return session
                else:
                    # Timed out, delete old Session
                    logger.info(
                        f"Session timed out, creating new Session: {session.session_id} "
                        f"(time since last query={elapsed:.1f}s > {config.SESSION_TIMEOUT}s)"
                    )
                    await self._remove_session_with_file(session)

            # Step 4: Create new Session
            session = self._create_new_session(user_id, agent_id)

            # Store in memory
            self._sessions[key] = session
            self._session_by_id[session.session_id] = session

            # Persist to file
            await self._save_session_to_file(session)

            logger.info(
                f"Created new Session: {session.session_id} "
                f"(user={user_id}, agent={agent_id})"
            )

            return session

    async def _remove_session_with_file(self, session: ConversationSession) -> None:
        """Delete a Session (memory and file)"""
        key = (session.user_id, session.agent_id)
        self._sessions.pop(key, None)
        self._session_by_id.pop(session.session_id, None)
        await self._delete_session_file(session.agent_id, session.user_id)
        logger.debug(f"Deleted Session: {session.session_id}")

    async def update_session(
        self,
        session_id: str,
        **kwargs
    ) -> bool:
        """
        Update Session fields

        Features:
        - Finds Session by session_id
        - Updates specified fields
        - Automatically updates last_query_time to current time
        - Automatically increments query_count (if last_query is provided)
        - Automatically persists to file

        Updatable fields:
        - last_query: str - Last query
        - last_query_embedding: List[float] - Last query's embedding
        - current_narrative_id: str - Currently associated Narrative ID

        Args:
            session_id: Session unique ID
            **kwargs: Fields to update (supports Pydantic fields)

        Returns:
            bool: True if update succeeded, False if Session doesn't exist
        """
        async with self._lock:
            session = self._session_by_id.get(session_id)

            if not session:
                logger.warning(f"Session not found: {session_id}")
                return False

            # Update fields
            updated_fields = []
            for field, value in kwargs.items():
                if hasattr(session, field):
                    setattr(session, field, value)
                    updated_fields.append(field)
                else:
                    logger.warning(f"Session has no field: {field}")

            # Automatically update timestamp
            session.last_query_time = datetime.now(timezone.utc)

            # If last_query was updated, increment counter
            if 'last_query' in kwargs:
                session.query_count += 1

            # Persist to file
            await self._save_session_to_file(session)

            logger.debug(
                f"Updated Session: {session_id} "
                f"(fields={updated_fields}, query_count={session.query_count})"
            )

            return True

    async def get_session(self, session_id: str) -> Optional[ConversationSession]:
        """
        Get a Session by session_id

        Args:
            session_id: Session unique ID

        Returns:
            Optional[ConversationSession]:
                - Session object (if found)
                - None (if not found)

        Example:
            >>> session = await manager.get_session("sess_abc123")
            >>> if session:
            ...     print(f"User: {session.user_id}")
            ... else:
            ...     print("Session not found")
        """
        async with self._lock:
            return self._session_by_id.get(session_id)

    async def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired Sessions (free memory and files)

        Features:
        - Iterates through all Sessions (memory + files)
        - Deletes timed-out Sessions
        - Returns cleanup count

        Recommended call timing:
        - Periodic cleanup (e.g., once per hour)
        - When memory pressure is high
        - Background task on schedule

        Returns:
            int: Number of Sessions cleaned up
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            expired_sessions = []

            # First load all Sessions from files into memory
            await self._load_all_sessions_to_memory()

            # Find all expired Sessions
            for session in list(self._sessions.values()):
                elapsed = (now - session.last_query_time).total_seconds()
                if elapsed > config.SESSION_TIMEOUT:
                    expired_sessions.append(session)

            # Delete expired Sessions (memory + files)
            for session in expired_sessions:
                await self._remove_session_with_file(session)

            if expired_sessions:
                logger.info(f"Cleaned up {len(expired_sessions)} expired Sessions")

            return len(expired_sessions)

    async def _load_all_sessions_to_memory(self) -> None:
        """Load all Sessions from files into memory"""
        for session_file in self._session_dir.glob("*.json"):
            try:
                with open(session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data['created_at'] = _ensure_timezone_aware(
                        datetime.fromisoformat(data['created_at'])
                    )
                    data['last_query_time'] = _ensure_timezone_aware(
                        datetime.fromisoformat(data['last_query_time'])
                    )
                    session = ConversationSession(**data)

                    key = (session.user_id, session.agent_id)
                    if key not in self._sessions:
                        self._sessions[key] = session
                        self._session_by_id[session.session_id] = session
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(f"Failed to load Session file {session_file}: {e}")
                continue

    async def list_all_sessions(self) -> List[ConversationSession]:
        """
        List all Sessions (loaded from files)

        Returns:
            List[ConversationSession]: List of all Sessions
        """
        async with self._lock:
            await self._load_all_sessions_to_memory()
            return list(self._sessions.values())

    async def delete_session(self, agent_id: str, user_id: str) -> bool:
        """
        Delete a specified Session

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            bool: True if deletion succeeded
        """
        async with self._lock:
            key = (user_id, agent_id)
            session = self._sessions.get(key)

            if session:
                await self._remove_session_with_file(session)
                logger.info(f"Deleted Session: agent={agent_id}, user={user_id}")
                return True
            else:
                # May only exist in file
                deleted = await self._delete_session_file(agent_id, user_id)
                if deleted:
                    logger.info(f"Deleted Session file: agent={agent_id}, user={user_id}")
                return deleted

    def _create_new_session(self, user_id: str, agent_id: str) -> ConversationSession:
        """
        Create a new Session (internal helper method)

        Generation rules:
        - session_id format: sess_{16-character hex}
        - created_at and last_query_time are both set to current UTC time
        - query_count starts at 0

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            ConversationSession: Newly created Session
        """
        session_id = f"sess_{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        return ConversationSession(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            created_at=now,
            last_query_time=now,
            last_query="",
            last_query_embedding=None,
            current_narrative_id=None,
            query_count=0,
        )

    def _remove_session(self, session_id: str):
        """
        Delete a Session (memory only, internal helper method)

        Deletes from both dictionaries:
        - _sessions (primary storage)
        - _session_by_id (secondary index)

        Args:
            session_id: Session ID to delete
        """
        session = self._session_by_id.get(session_id)
        if session:
            key = (session.user_id, session.agent_id)
            self._sessions.pop(key, None)
            self._session_by_id.pop(session_id, None)
            logger.debug(f"Removed Session from memory: {session_id}")

    async def get_session_count(self) -> int:
        """
        Get the count of currently active Sessions (for monitoring)

        Returns:
            int: Active Session count (including those in files)
        """
        async with self._lock:
            # Count file count
            file_count = len(list(self._session_dir.glob("*.json")))
            return file_count

    async def get_session_by_agent_user(
        self,
        agent_id: str,
        user_id: str
    ) -> Optional[ConversationSession]:
        """
        Get a Session by agent_id and user_id (without timeout check)

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Optional[ConversationSession]: Session or None
        """
        async with self._lock:
            key = (user_id, agent_id)

            # Check memory first
            if key in self._sessions:
                return self._sessions[key]

            # Then check file
            session = await self._load_session_from_file(agent_id, user_id)
            if session:
                self._sessions[key] = session
                self._session_by_id[session.session_id] = session

            return session
