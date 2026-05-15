#!/usr/bin/env python3
"""
@file_name: check_id_field_coverage.py
@author: NetMind.AI
@date: 2026-05-08
@description: ID Rewrite Layer 3 — CI guard for STRUCTURED_ID_FIELDS coverage

Rule (PRD §8.11 Layer 3):
- Every column in schema_registry whose name ends in "_id" MUST either:
  (a) be registered in bundle.id_field_map.STRUCTURED_ID_FIELDS for its
      table, OR
  (b) be explicitly listed in IGNORE below with a one-line reason.

If a column is missing in both lists, this script exits non-zero — preventing
silent ID-rewrite bugs when someone adds a new table column without
registering it for bundle import rewrite.

Run from repo root:
    python scripts/check_id_field_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from xyz_agent_context.utils.schema_registry import TABLES  # noqa: E402
from xyz_agent_context.bundle.id_field_map import STRUCTURED_ID_FIELDS  # noqa: E402


# Columns we explicitly do NOT rewrite during bundle import.
# Each entry: (table_name, column_name) → reason string.
# These columns ARE called *_id but are NOT subject to ID rewrite —
# typically because they're surrogate PKs, references to global tables
# the bundle never imports, or pre-existing data we don't touch.
IGNORE: dict[tuple[str, str], str] = {
    # Surrogate auto-increment integer PKs (every table has one)
    ("agents", "id"): "surrogate auto-increment PK, not a logical ID",
    ("users", "id"): "surrogate PK",
    ("events", "id"): "surrogate PK",
    ("narratives", "id"): "surrogate PK",
    ("mcp_urls", "id"): "surrogate PK",
    ("mcp_urls", "mcp_id"): "user-scoped MCP URL config, not bundled (mcp_hints flow handles MCP separately)",
    ("inbox_table", "id"): "surrogate PK",
    ("inbox_table", "message_id"): "inbox messages are user-scoped notifications, not bundled",
    ("inbox_table", "event_id"): "inbox refers to events but inbox itself is not bundled",
    ("agent_messages", "id"): "surrogate PK",
    ("agent_messages", "source_id"): "channel/sender id from external system; not a NarraNexus ID",
    ("module_instances", "id"): "surrogate PK",
    ("instance_social_entities", "id"): "surrogate PK",
    ("instance_social_entities", "entity_id"): "polymorphic — sometimes an agent_id (rewritten case-by-case in importer), sometimes external (user/org); rewrite logic lives in importer.confirm() per-row",
    ("instance_jobs", "id"): "surrogate PK",
    ("instance_rag_store", "id"): "surrogate PK",
    ("instance_narrative_links", "id"): "surrogate PK",
    ("instance_awareness", "id"): "surrogate PK",
    ("instance_module_report_memory", "id"): "surrogate PK",
    ("instance_json_format_memory", "id"): "surrogate PK",
    ("instance_json_format_memory_chat", "id"): "surrogate PK",
    ("module_report_memory", "id"): "surrogate PK",
    ("cost_records", "id"): "surrogate PK; cost records are not bundled",
    ("cost_records", "agent_id"): "cost records are not bundled (per-instance billing data)",
    ("embeddings_store", "id"): "surrogate PK; embeddings are bundle-format-versioned separately",
    ("chat_message_embeddings", "id"): "surrogate PK; chat embeddings derived",
    ("bus_channels", "id"): "surrogate PK",
    ("bus_channel_members", "id"): "surrogate PK",
    ("bus_messages", "id"): "surrogate PK",
    ("bus_agent_registry", "id"): "surrogate PK",
    ("bus_message_failures", "id"): "surrogate PK",
    ("bus_message_failures", "message_id"): "FK to bus_messages.message_id, rewritten transitively when message_id is rewritten in bus_messages",
    ("bus_message_failures", "channel_id"): "FK to bus_channels.channel_id; bus_message_failures are diagnostic, not bundled",
    ("user_providers", "id"): "surrogate PK",
    ("user_providers", "provider_id"): "config-scoped (claude/openai/...), not a NarraNexus rewriteable ID",
    ("user_providers", "user_id"): "user_id is rewritten via global user_id replacement, not the per-kind ID rewrite",
    ("user_slots", "id"): "surrogate PK",
    ("user_slots", "user_id"): "global user_id replacement",
    ("bus_message_failures", "agent_id"): "diagnostic table, not bundled",
    ("user_quotas", "id"): "surrogate PK",
    ("user_quotas", "user_id"): "global user_id replacement",
    ("lark_credentials", "id"): "stripped from bundle (credentials)",
    ("lark_credentials", "agent_id"): "lark_credentials table is fully stripped on export",
    ("lark_seen_messages", "id"): "surrogate PK; not bundled",
    ("lark_seen_messages", "message_id"): "external Lark IM message id, not a NarraNexus ID",
    ("lark_seen_messages", "agent_id"): "Lark seen-message log not bundled",
    ("lark_trigger_audit", "id"): "surrogate PK",
    ("lark_trigger_audit", "message_id"): "external Lark IM message id",
    ("lark_trigger_audit", "agent_id"): "Lark trigger audit not bundled",
    ("lark_trigger_audit", "app_id"): "Lark app id (external)",
    ("lark_trigger_audit", "chat_id"): "Lark chat id (external)",
    ("lark_trigger_audit", "sender_id"): "Lark sender id (external)",
    ("teams", "id"): "surrogate PK",
    ("team_members", "id"): "surrogate PK",
    ("skill_archives", "id"): "surrogate PK",
    ("bundle_preflight_sessions", "id"): "surrogate PK; bundle_preflight_sessions itself is import-side scratch state, never inside a bundle",
    ("bundle_preflight_sessions", "user_id"): "import-side scratch state, never bundled",
    # User-scoped columns — handled by global user_id rewrite, not per-kind ID rewrite
    ("bus_agent_registry", "owner_user_id"): "global user_id replacement on import",
    ("events", "user_id"): "global user_id replacement on import",
    ("inbox_table", "user_id"): "inbox is per-user, never bundled",
    ("instance_jobs", "user_id"): "global user_id replacement on import",
    ("module_instances", "user_id"): "global user_id replacement on import",
    ("teams", "owner_user_id"): "global user_id replacement on import",
    ("skill_archives", "user_id"): "skill_archives is per-user state, not bundled (archive_path is bundled separately)",
    ("users", "user_id"): "users table is fully stripped on export",
    # Embeddings / cost / mcp_urls — not bundled (embeddings rebuilt per compat advice; cost is billing data; mcp_urls reviewed via mcp_hints flow)
    ("chat_message_embeddings", "instance_id"): "embeddings rebuilt on import-side per embedding-compat advice",
    ("chat_message_embeddings", "event_id"): "embeddings rebuilt on import-side",
    ("cost_records", "event_id"): "cost records are billing data, not bundled",
    ("embeddings_store", "entity_id"): "embeddings rebuilt on import-side",
    ("mcp_urls", "agent_id"): "mcp_urls bundled via mcp_hints.json flow — user reviews and adds manually",
    ("mcp_urls", "user_id"): "see mcp_urls.agent_id",
    ("user_slots", "provider_id"): "provider_id is a config string ('claude' / 'openai'), not a NarraNexus rewriteable ID",
    # Lark — fully stripped
    ("lark_credentials", "app_id"): "lark_credentials fully stripped on export",
    ("lark_credentials", "owner_open_id"): "lark_credentials fully stripped on export",
}


def main() -> int:
    missing: list[tuple[str, str]] = []

    for table_name, table_def in sorted(TABLES.items()):
        registered = STRUCTURED_ID_FIELDS.get(table_name, {})
        for col in table_def.columns:
            if not col.name.endswith("_id") and col.name != "id":
                continue
            key = (table_name, col.name)
            if col.name in registered:
                continue
            if key in IGNORE:
                continue
            missing.append(key)

    if missing:
        print("FAIL — the following *_id columns are not registered in either")
        print("       bundle.id_field_map.STRUCTURED_ID_FIELDS or scripts/")
        print("       check_id_field_coverage.IGNORE:")
        print()
        for table, col in missing:
            print(f"  - {table}.{col}")
        print()
        print("Action: either register the column in STRUCTURED_ID_FIELDS")
        print("        (with the correct kind from id_schema.ID_KINDS) so")
        print("        bundle import will rewrite it, OR add it to IGNORE")
        print("        with a one-line reason for why it's exempt.")
        return 1

    total = sum(len(t.columns) for t in TABLES.values())
    n_id_cols = sum(
        1 for t in TABLES.values() for c in t.columns
        if c.name.endswith("_id") or c.name == "id"
    )
    print(f"OK — checked {n_id_cols} ID-shaped columns across {len(TABLES)} tables ({total} columns total).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
