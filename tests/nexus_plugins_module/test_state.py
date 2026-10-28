"""
@file_name: test_state.py
@author: Bin Liang
@date: 2026-09-03
@description: Proposals expire after the approval timeout (fail-closed), decisions are single-shot, budgets and the audit timeline persist.
"""
from __future__ import annotations

import time

import pytest

from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl import state as st
from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.guards import APPROVAL_TIMEOUT_S


def test_proposal_lifecycle(env):
    store = st.ProposalStore()
    p = store.create(plugin_id="me.weather", agent_id="a1", user_id="u1", action="activate", scope="agent", summary="s", permissions={}, test_report={}, diff_hash="h")
    assert [x.id for x in store.list(agent_id="a1", pending_only=True)] == [p.id]
    assert store.decide(p.id, "approved", by="u1").decision == "approved"
    with pytest.raises(ValueError, match="already approved"):
        store.decide(p.id, "rejected", by="u1")
    with pytest.raises(KeyError):
        store.decide("prop_nope", "approved", by="u1")


def test_expired_proposal_is_rejected_fail_closed(env, monkeypatch):
    store = st.ProposalStore()
    p = store.create(plugin_id="me.weather", agent_id="a1", user_id="u1", action="activate", scope="agent", summary="s", permissions={}, test_report={}, diff_hash="h")
    monkeypatch.setattr(time, "time", lambda: p.created_at + APPROVAL_TIMEOUT_S + 1)
    assert store.list(pending_only=True) == []
    with pytest.raises(ValueError, match="expired"):
        store.decide(p.id, "approved", by="u1")


def test_budgets_and_audit_persist(env):
    budgets = st.BudgetStore()
    b = budgets.get("a1")
    b.spend()
    budgets.put("a1", b)
    assert len(budgets.get("a1").events) == 1
    audit = st.Audit()
    audit.record(agent_id="a1", user_id="u1", plugin_id="me.weather", action="edit", why="x", diff_hash="h")
    audit.record(agent_id="a2", user_id="u1", plugin_id="me.other", action="edit")
    assert [r["plugin_id"] for r in audit.timeline(agent_id="a1")] == ["me.weather"]
    assert st.summary_for_agent("a1").startswith("0 plugin(s)")
