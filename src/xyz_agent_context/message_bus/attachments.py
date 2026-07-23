"""
@file_name: attachments.py
@author: NarraNexus
@date: 2026-07-22
@description: Public entry point for bus-attachment staging and resolution.

Pure re-export facade over ``_bus_attachment_impl``. Consumers outside the
``message_bus`` package (backend routes, module MCP tools) must import from
HERE, never from the underscore-private implementation module — the project
layering (api → service protocol → private impl) keeps private modules free
to reorganize without touching cross-package callers.
"""

from xyz_agent_context.message_bus._bus_attachment_impl import (
    build_bus_markers,
    load_bus_attachment_meta,
    resolve_and_stage_refs,
    resolve_shared_file_by_id,
    resolve_shared_file_for_user,
    stage_path_into_team,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)

__all__ = [
    "build_bus_markers",
    "load_bus_attachment_meta",
    "resolve_and_stage_refs",
    "resolve_shared_file_by_id",
    "resolve_shared_file_for_user",
    "stage_path_into_team",
    "store_bus_attachment_meta",
    "store_bytes_into_bus",
]
