"""
@file_name: db_backend.py
@author: NexusAgent
@date: 2026-04-02
@description: Abstract base class defining the interface all database backends must implement

This module provides the DatabaseBackend ABC that enables pluggable database backends
(e.g., SQLite for local desktop via Tauri, MySQL for cloud deployment). All backends
must implement this interface to ensure consistent behavior across environments.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DatabaseBackend(ABC):
    """
    Abstract base class for database backends.

    All database backends (SQLite, MySQL, etc.) must implement this interface.
    This enables the application to switch between backends without changing
    the business logic layer.
    """

    # ===== Properties =====

    @property
    @abstractmethod
    def placeholder(self) -> str:
        """
        Return the SQL parameter placeholder for this backend.

        Returns:
            '?' for SQLite, '%s' for MySQL, etc.
        """
        ...

    @property
    @abstractmethod
    def dialect(self) -> str:
        """
        Return the SQL dialect name for this backend.

        Returns:
            'sqlite', 'mysql', etc.
        """
        ...

    # ===== Lifecycle =====

    @abstractmethod
    async def initialize(self) -> None:
        """
        Set up connections, pools, and any required configuration.

        Must be called before any other operations.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """
        Clean up connections and release resources.

        After calling close(), the backend should not be used.
        """
        ...

    # ===== Raw SQL Execution =====

    @abstractmethod
    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query and return result rows as dicts.

        Args:
            query: SQL query string with parameter placeholders.
            params: Tuple of parameter values.

        Returns:
            List of row dicts for SELECT queries, empty list for write queries.
        """
        ...

    @abstractmethod
    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """
        Execute a write SQL statement and return the number of affected rows.

        Args:
            query: SQL write statement (INSERT, UPDATE, DELETE).
            params: Tuple of parameter values.

        Returns:
            Number of affected rows.
        """
        ...

    # ===== CRUD Operations =====

    @abstractmethod
    async def get(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query rows from a table with optional filtering, pagination, and sorting.

        Args:
            table: Table name.
            filters: Column-value pairs for WHERE clause (AND logic).
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.
            order_by: Order expression, e.g. 'created_at DESC'.
            fields: List of column names to select. None means SELECT *.

        Returns:
            List of row dicts.
        """
        ...

    @abstractmethod
    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Query a single row matching the given filters.

        Args:
            table: Table name.
            filters: Column-value pairs for WHERE clause.

        Returns:
            Row dict if found, None otherwise.
        """
        ...

    @abstractmethod
    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[str],
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Batch-fetch rows by a list of IDs, preserving the input order.

        Solves the N+1 query problem by using a single IN query.

        Args:
            table: Table name.
            id_field: Column name used as the ID.
            ids: List of ID values to fetch.

        Returns:
            List of row dicts (or None for missing IDs), in the same order as `ids`.
        """
        ...

    @abstractmethod
    async def insert(
        self,
        table: str,
        data: Dict[str, Any],
    ) -> int:
        """
        Insert a single row into a table.

        Args:
            table: Table name.
            data: Column-value pairs to insert.

        Returns:
            The lastrowid (auto-increment ID) of the inserted row.
        """
        ...

    @abstractmethod
    async def update(
        self,
        table: str,
        filters: Dict[str, Any],
        data: Dict[str, Any],
    ) -> int:
        """
        Update rows matching the given filters.

        Args:
            table: Table name.
            filters: Column-value pairs for WHERE clause.
            data: Column-value pairs to update.

        Returns:
            Number of rows updated.
        """
        ...

    @abstractmethod
    async def delete(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> int:
        """
        Delete rows matching the given filters.

        Args:
            table: Table name.
            filters: Column-value pairs for WHERE clause.

        Returns:
            Number of rows deleted.
        """
        ...

    @abstractmethod
    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str,
    ) -> int:
        """
        Insert a row, or update it if the id_field conflicts.

        Uses database-level atomic operations (e.g., INSERT ... ON CONFLICT)
        to avoid race conditions.

        Args:
            table: Table name.
            data: Column-value pairs to insert/update.
            id_field: The unique/primary key column for conflict detection.

        Returns:
            Number of affected rows.
        """
        ...

    # ===== Transaction Support =====

    @abstractmethod
    async def begin_transaction(self) -> None:
        """Begin a database transaction."""
        ...

    @abstractmethod
    async def commit(self) -> None:
        """Commit the current transaction."""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the current transaction."""
        ...
