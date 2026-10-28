"""
@file_name: test_schema_artifact_events.py
@date: 2026-08-18
@description: Schema presence tests for the artifact pointer fingerprint
column (content_hash) and the cross-process artifact event outbox table
(instance_artifact_events). Both are additive registrations picked up by
auto_migrate; these tests pin the definitions so a refactor of
schema_registry cannot silently drop them.
"""

from narranexus.platform.utils.db.schema_registry import TABLES


def test_instance_artifacts_has_content_hash_column():
    table = TABLES["instance_artifacts"]
    cols = {c.name for c in table.columns}
    assert "content_hash" in cols


def test_content_hash_declares_both_dialects():
    table = TABLES["instance_artifacts"]
    col = next(c for c in table.columns if c.name == "content_hash")
    assert col.sqlite_type == "TEXT"
    assert col.mysql_type == "VARCHAR(64)"
    assert col.nullable is True  # legacy rows stay NULL; hashing is best-effort


def test_artifact_events_outbox_registered():
    table = TABLES["instance_artifact_events"]
    cols = {c.name for c in table.columns}
    assert {"id", "agent_id", "payload_json", "created_at", "consumed_at"} <= cols


def test_artifact_events_outbox_pending_index():
    table = TABLES["instance_artifact_events"]
    index_names = {i.name for i in table.indexes}
    assert "idx_artifact_events_agent_pending" in index_names
