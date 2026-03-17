"""
@file_name: contact_card.py
@author: Bin Liang
@date: 2026-03-10
@description: Flexible YAML contact card for Agents

Each Agent has a contact_card.yaml in its workspace. The card has no fixed schema —
any Module can freely read, add fields, and write back.

Uses file locking (fcntl) for multi-process safety.

Key classes:
- ContactCard: Read/write a single Agent's contact card
- ContactCardScanner: Scan sibling Agents' contact cards
"""

from __future__ import annotations

import copy
import fcntl
import os
import time
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger


class ContactCard:
    """
    Flexible contact card reader/writer.

    No fixed schema — any program can freely add fields.
    Uses file locking to ensure multi-process safety.

    Args:
        workspace_path: Path to the Agent's workspace directory
    """

    FILENAME = "contact_card.yaml"

    def __init__(self, workspace_path: str):
        self.path = os.path.join(workspace_path, self.FILENAME)

    def read(self) -> Dict[str, Any]:
        """
        Read the entire contact card.

        Returns:
            Card data as dict. Returns empty dict if file doesn't exist.
        """
        if not os.path.exists(self.path):
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = yaml.safe_load(f)
                    return data if isinstance(data, dict) else {}
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"Failed to read contact card at {self.path}: {e}")
            return {}

    def write(self, data: Dict[str, Any]) -> None:
        """
        Overwrite the entire contact card.

        Args:
            data: Complete card data dict
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Failed to write contact card at {self.path}: {e}")

    def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep-merge update. Does not overwrite untouched fields.

        E.g. updating matrix.user_id won't affect the capabilities field.

        Args:
            updates: Fields to merge in

        Returns:
            Full card data after update
        """
        current = self.read()
        merged = _deep_merge(current, updates)
        self.write(merged)
        return merged

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Read a value using dot-separated path.

        Example: card.get("matrix.user_id")

        Args:
            key_path: Dot-separated key path
            default: Default value if key not found

        Returns:
            Value at the path, or default
        """
        data = self.read()
        keys = key_path.split(".")
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return default
            if data is None:
                return default
        return data

    def exists(self) -> bool:
        """Check if the contact card file exists."""
        return os.path.exists(self.path)


class ContactCardScanner:
    """
    Scan sibling Agents' contact cards.

    Scans all Agent workspaces under the same user's base directory,
    reads their contact_card.yaml files, and returns raw dicts (no schema validation).

    Results are cached briefly (configurable TTL) to avoid reading the filesystem
    on every conversation turn.
    """

    # Simple in-memory cache: {base_path: (timestamp, results)}
    _cache: Dict[str, tuple] = {}
    _cache_ttl: float = 300.0  # 5 minutes

    @classmethod
    async def scan_sibling_agents(
        cls,
        current_agent_id: str,
        base_workspace_path: str,
    ) -> List[Dict[str, Any]]:
        """
        Scan and return contact cards for sibling Agents (excluding self).

        Args:
            current_agent_id: This Agent's ID (to exclude from results)
            base_workspace_path: Parent directory containing all Agent workspaces

        Returns:
            List of contact card dicts from other Agents
        """
        # Check cache
        cache_key = base_workspace_path
        if cache_key in cls._cache:
            cached_time, cached_results = cls._cache[cache_key]
            if time.time() - cached_time < cls._cache_ttl:
                # Filter out self from cached results
                return [c for c in cached_results if c.get("agent_id") != current_agent_id]

        results = []
        if not os.path.isdir(base_workspace_path):
            return results

        try:
            for entry in os.listdir(base_workspace_path):
                agent_dir = os.path.join(base_workspace_path, entry)
                if not os.path.isdir(agent_dir):
                    continue

                card = ContactCard(agent_dir)
                if card.exists():
                    data = card.read()
                    if data:
                        results.append(data)
        except Exception as e:
            logger.warning(f"Failed to scan sibling agent cards: {e}")

        # Update cache
        cls._cache[cache_key] = (time.time(), results)

        # Filter out self
        return [c for c in results if c.get("agent_id") != current_agent_id]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the scan result cache."""
        cls._cache.clear()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge two dicts. Override values take precedence.

    Nested dicts are merged recursively; other types are overwritten.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
