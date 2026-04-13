"""
@file_name: test_schema_events_index.py
@description: T01 — verify events table has composite (agent_id, created_at) index.
"""
from xyz_agent_context.utils.schema_registry import TABLES


def test_events_has_composite_agent_created_index():
    events = TABLES["events"]
    names = {idx.name for idx in events.indexes}
    assert "idx_events_agent_created" in names
    idx = next(i for i in events.indexes if i.name == "idx_events_agent_created")
    assert idx.columns == ["agent_id", "created_at"]
    assert idx.unique is False
