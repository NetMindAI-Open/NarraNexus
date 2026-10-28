"""
@file_name: ctx_merger.py
@author: NetMind.AI
@date: 2025-12-22
@description: ContextData merger

Responsibility: Merge multiple modules' ctx_data modifications into the original ctx_data
Assumption: Modules do not write to the same fields (each module is responsible for its own data domain)

Usage scenarios:
- After parallel execution of gather, merge each module's modifications
- Each module receives a copy of the original ctx_data and modifies it independently
- Finally merge all modifications into the final result
"""

from typing import List, Dict, Any, Set, TYPE_CHECKING
from copy import deepcopy
from loguru import logger

if TYPE_CHECKING:
    from narranexus.platform.schema import ContextData


class ContextDataMerger:
    """
    ContextData Merger

    Merge multiple modules' modifications into the original ctx_data.

    Merge strategy:
    - IMMUTABLE_FIELDS: Fields that cannot be modified (e.g., agent_id, user_id)
    - LIST_FIELDS: Merge using extend (e.g., chat_history)
    - DICT_FIELDS: Merge using deep merge (e.g., extra_data)
    - Other fields: Non-None values override
    """

    # Fields not allowed to be modified by modules
    IMMUTABLE_FIELDS: Set[str] = {"agent_id", "user_id", "input_content"}

    # List type fields, merged using extend
    LIST_FIELDS: Set[str] = {"chat_history"}

    # Dict type fields, using deep merge
    DICT_FIELDS: Set[str] = {"user_profile", "extra_data"}

    @classmethod
    def merge(
        cls,
        original: 'ContextData',
        updates: List['ContextData'],
    ) -> 'ContextData':
        """
        Merge multiple modules' modifications

        Args:
            original: Original ctx_data
            updates: List of ctx_data returned by each module

        Returns:
            Merged ctx_data

        Example:
            >>> # Module A added chat_history
            >>> # Module B added user_profile
            >>> merged = ContextDataMerger.merge(original, [ctx_a, ctx_b])
            >>> # merged contains all modifications from both modules
        """
        if not updates:
            return original

        # Start from original data
        result = original.model_copy(deep=True)
        result_dict = result.model_dump()
        original_dict = original.model_dump()

        for update in updates:
            if update is None:
                continue

            update_dict = update.model_dump()

            for field, value in update_dict.items():
                # Skip immutable fields
                if field in cls.IMMUTABLE_FIELDS:
                    continue

                # Skip unmodified fields (same as original value)
                if field in original_dict and original_dict[field] == value:
                    continue

                # Merge based on field type
                if field in cls.LIST_FIELDS:
                    cls._merge_list(result_dict, field, value, original_dict.get(field))
                elif field in cls.DICT_FIELDS:
                    cls._merge_dict(result_dict, field, value, original_dict.get(field))
                else:
                    # Simple field: non-None value overrides
                    if value is not None:
                        result_dict[field] = value

        # Rebuild ContextData
        return type(original)(**result_dict)

    @classmethod
    def _merge_list(
        cls,
        result: Dict,
        field: str,
        new_value: Any,
        original_value: Any,
    ) -> None:
        """
        Merge List field

        Only adds elements newly added by the module, avoiding duplicates.

        Args:
            result: Result dictionary (will be modified)
            field: Field name
            new_value: New value returned by the module
            original_value: Original value
        """
        if new_value is None:
            return

        original_list = original_value or []
        new_list = new_value or []

        # Only add elements newly added by the module
        added = [item for item in new_list if item not in original_list]

        if result.get(field) is None:
            result[field] = list(original_list)

        result[field].extend(added)

        if added:
            logger.debug(f"          Merged {len(added)} items to {field}")

    @classmethod
    def _merge_dict(
        cls,
        result: Dict,
        field: str,
        new_value: Any,
        original_value: Any,
    ) -> None:
        """
        Deep merge Dict field

        Recursively merge nested dictionaries.

        Args:
            result: Result dictionary (will be modified)
            field: Field name
            new_value: New value returned by the module
            original_value: Original value
        """
        if new_value is None:
            return

        original_dict = original_value or {}
        new_dict = new_value or {}

        if result.get(field) is None:
            result[field] = dict(original_dict)

        # Deep merge
        cls._deep_merge(result[field], new_dict, original_dict)

    @classmethod
    def _deep_merge(cls, target: Dict, source: Dict, original: Dict) -> None:
        """
        Recursive deep merge

        Args:
            target: Target dictionary (will be modified)
            source: Source dictionary
            original: Original dictionary (used to detect modifications)
        """
        for key, value in source.items():
            # Skip unmodified values
            if key in original and original[key] == value:
                continue

            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                cls._deep_merge(target[key], value, original.get(key, {}))
            else:
                target[key] = deepcopy(value)
