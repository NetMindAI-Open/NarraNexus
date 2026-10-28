"""
@file_name: test_lifecycle.py
@author: Bin Liang
@date: 2026-08-20
@description: Lock lifecycle.is_bootstrap_active — the shared bootstrap-phase
judgment gating BOTH the step_1 greeting seed and context_runtime's Bootstrap
injection/auto-delete. Uses the REAL db_client so the event-count query executes
against the actual (sqlite) dialect, not a mock — the byte-identical query runs
on prod MySQL, and this exercises the sqlite translation.
"""
from __future__ import annotations

import pytest

from narranexus.platform.bootstrap import lifecycle
from narranexus.platform.bootstrap.profiles import META_AUTO_DELETE


async def _insert_events(db, agent_id: str, n: int) -> None:
    for i in range(n):
        await db.execute(
            "INSERT INTO events (event_id, trigger, trigger_source, agent_id) "
            "VALUES (%s, %s, %s, %s)",
            (f"evt_{agent_id}_{i}", "chat", "user", agent_id),
            fetch=False,
        )


@pytest.fixture
def _workspace_with_md(monkeypatch, tmp_path):
    """Bootstrap.md present at the resolved workspace."""
    (tmp_path / "Bootstrap.md").write_text("bootstrap")
    monkeypatch.setattr(lifecycle, "resolve_existing_workspace", lambda *a, **k: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_active_when_present_and_under_threshold(db_client, _workspace_with_md):
    await _insert_events(db_client, "a_under", 2)  # default threshold 3
    st = await lifecycle.is_bootstrap_active(db_client, "a_under", "u1", {})
    assert st.present is True
    assert st.active is True
    assert st.event_count == 2
    assert st.threshold == 3


@pytest.mark.asyncio
async def test_inactive_when_over_threshold(db_client, _workspace_with_md):
    await _insert_events(db_client, "a_over", 3)  # count 3 >= threshold 3
    st = await lifecycle.is_bootstrap_active(db_client, "a_over", "u1", {})
    assert st.present is True
    assert st.active is False
    assert st.event_count == 3


@pytest.mark.asyncio
async def test_inactive_when_md_absent(db_client, monkeypatch, tmp_path):
    # No Bootstrap.md written → present False, active False, no count query needed.
    monkeypatch.setattr(lifecycle, "resolve_existing_workspace", lambda *a, **k: tmp_path)
    st = await lifecycle.is_bootstrap_active(db_client, "a_absent", "u1", {})
    assert st.present is False
    assert st.active is False


@pytest.mark.asyncio
async def test_active_when_threshold_none(db_client, _workspace_with_md):
    """Semantic-only profile (threshold None) → active while the file exists,
    regardless of event count."""
    await _insert_events(db_client, "a_none", 9)
    st = await lifecycle.is_bootstrap_active(
        db_client, "a_none", "u1", {META_AUTO_DELETE: None}
    )
    assert st.present is True
    assert st.active is True
    assert st.threshold is None
