"""
@file_name: channel_contact_utils.py
@author: Bin Liang
@date: 2026-03-10
@description: Read/write utility functions for contact_info.channels

contact_info structure:
{
    "channels": {
        "matrix": {
            "id": "@agent_xxx:matrix.narranexus.com",
            "server": "https://matrix.narranexus.com",
            "rooms": {
                "agent_research_002": "!roomid123:matrix.narranexus.com"
            }
        },
        "slack": {
            "id": "U12345678",
            "workspace": "narranexus.slack.com"
        }
    },
    "preferred_channel": "matrix"
}

Design principle:
- Social Network only does deep-merge JSON read/write; it never parses channels internals
- Adding a new channel just means adding a key under channels — zero Social Network changes
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


def get_channel_info(contact_info: Dict[str, Any], channel: str) -> Optional[Dict[str, Any]]:
    """
    Read a specific channel's info from contact_info.

    Args:
        contact_info: The entity's contact_info field
        channel: Channel name, e.g. "matrix", "slack"

    Returns:
        Channel info dict, or None if not present
    """
    return contact_info.get("channels", {}).get(channel)


def set_channel_info(
    contact_info: Dict[str, Any],
    channel: str,
    info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Write channel info into contact_info via deep merge.

    Does NOT overwrite unrelated fields. E.g. writing matrix.rooms won't touch matrix.id.

    Args:
        contact_info: The entity's contact_info field (modified in place)
        channel: Channel name
        info: Info dict to merge in

    Returns:
        Updated contact_info
    """
    if "channels" not in contact_info:
        contact_info["channels"] = {}

    existing = contact_info["channels"].get(channel, {})
    contact_info["channels"][channel] = _deep_merge(existing, info)
    return contact_info


def get_preferred_channel(contact_info: Dict[str, Any]) -> Optional[str]:
    """
    Get the preferred contact channel.

    Args:
        contact_info: The entity's contact_info field

    Returns:
        Preferred channel name, or None if not set
    """
    return contact_info.get("preferred_channel")


def get_room_id(
    contact_info: Dict[str, Any],
    channel: str,
    counterpart_id: str,
) -> Optional[str]:
    """
    Get the conversation ID with a specific counterpart on a specific channel.

    Args:
        contact_info: The entity's contact_info field
        channel: Channel name
        counterpart_id: The counterpart's identifier

    Returns:
        Conversation ID (e.g. Matrix room_id), or None
    """
    channel_info = get_channel_info(contact_info, channel)
    if not channel_info:
        return None
    return channel_info.get("rooms", {}).get(counterpart_id)


def set_room_id(
    contact_info: Dict[str, Any],
    channel: str,
    counterpart_id: str,
    room_id: str,
) -> Dict[str, Any]:
    """
    Write a conversation ID with a counterpart into contact_info.

    Args:
        contact_info: The entity's contact_info field (modified in place)
        channel: Channel name
        counterpart_id: The counterpart's identifier
        room_id: Conversation ID

    Returns:
        Updated contact_info
    """
    return set_channel_info(contact_info, channel, {
        "rooms": {counterpart_id: room_id}
    })


def merge_contact_info(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge incoming contact_info into existing, with normalization.

    Handles non-standard formats from Agent-generated input:
    - Top-level channel keys (e.g. {"matrix": "@xxx"}) → moved under channels
    - Flat key aliases (e.g. "user_id" → "id", "matrix_user_id" → "id")

    Always produces the canonical structure:
    {
        "channels": {"matrix": {"id": "...", ...}, ...},
        "preferred_channel": "..."
    }

    Args:
        existing: Current contact_info from DB
        incoming: New contact_info to merge in

    Returns:
        Merged and normalized contact_info
    """
    result = copy.deepcopy(existing)
    normalized = normalize_contact_info(incoming)
    return _deep_merge(result, normalized)


def normalize_contact_info(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize contact_info to canonical structure.

    Detects non-standard formats and converts them:
    - {"matrix": "@xxx:localhost"} → {"channels": {"matrix": {"id": "@xxx:localhost"}}}
    - {"matrix": {"user_id": "..."}} → {"channels": {"matrix": {"id": "..."}}}
    - {"channels": {"matrix": {"user_id": "..."}}} → {"channels": {"matrix": {"id": "..."}}}

    Args:
        raw: Raw contact_info dict (may be in any format)

    Returns:
        Normalized contact_info
    """
    if not raw:
        return {}

    result: Dict[str, Any] = {}
    known_channels = {"matrix", "slack", "discord", "telegram"}
    # Key aliases that should be normalized to "id"
    id_aliases = {"user_id", "matrix_user_id", "matrix_id", "slack_id", "channel_id"}

    # Preserve top-level metadata
    if "preferred_channel" in raw:
        result["preferred_channel"] = raw["preferred_channel"]

    # Preserve non-channel, non-metadata fields (e.g. "email": "alice@example.com")
    preserved_keys = {"channels", "preferred_channel"} | known_channels
    for key, val in raw.items():
        if key not in preserved_keys and not isinstance(val, dict):
            result[key] = val

    # Start with existing channels structure
    channels = copy.deepcopy(raw.get("channels", {}))

    # Normalize id aliases inside existing channels
    for ch_name, ch_data in channels.items():
        if isinstance(ch_data, dict):
            _normalize_id_field(ch_data, id_aliases)

    # Detect top-level channel keys (e.g. {"matrix": ...})
    for ch_name in known_channels:
        if ch_name not in raw:
            continue
        val = raw[ch_name]
        if isinstance(val, str):
            # {"matrix": "@xxx:localhost"} → {"channels": {"matrix": {"id": "@xxx"}}}
            if ch_name not in channels:
                channels[ch_name] = {}
            channels[ch_name].setdefault("id", val)
        elif isinstance(val, dict):
            # {"matrix": {"user_id": "...", "room_id": "..."}} → normalize and merge
            normalized_ch = copy.deepcopy(val)
            _normalize_id_field(normalized_ch, id_aliases)
            if ch_name in channels:
                channels[ch_name] = _deep_merge(channels[ch_name], normalized_ch)
            else:
                channels[ch_name] = normalized_ch

    if channels:
        result["channels"] = channels

    return result


def _normalize_id_field(ch_data: Dict[str, Any], id_aliases: set) -> None:
    """
    Normalize id aliases (user_id, matrix_user_id, etc.) to canonical "id" field in-place.

    If "id" already exists, aliases are removed. Otherwise the first alias found is promoted.
    """
    for alias in list(id_aliases):
        if alias in ch_data:
            if "id" not in ch_data:
                ch_data["id"] = ch_data.pop(alias)
            else:
                ch_data.pop(alias)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge two dicts. Values in override take precedence.

    Nested dicts are merged recursively; other types are overwritten.

    Args:
        base: Base dict
        override: Override dict

    Returns:
        New merged dict
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
