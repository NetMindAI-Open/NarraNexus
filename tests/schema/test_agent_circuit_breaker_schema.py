"""
@file_name: test_agent_circuit_breaker_schema.py
@author:
@date: 2026-07-13
@description: Schema + table-registration tests for the Agent circuit-breaker.
"""

from datetime import datetime, timezone

from xyz_agent_context.schema import (
    AgentCircuitBreaker,
    CbStatus,
    ErrorCategory,
    PAUSING_CATEGORIES,
    PausedReason,
)
from xyz_agent_context.utils.schema_registry import TABLES


def test_enums_values():
    assert CbStatus.ACTIVE.value == "active"
    assert CbStatus.COOLING.value == "cooling"
    assert CbStatus.PAUSED.value == "paused"
    assert {r.value for r in PausedReason} == {"auth", "quota"}
    assert {c.value for c in ErrorCategory} == {"auth", "quota", "transient", "business"}
    # Only auth/quota escalate to a hard pause.
    assert PAUSING_CATEGORIES == frozenset({ErrorCategory.AUTH, ErrorCategory.QUOTA})


def test_model_defaults_and_roundtrip():
    cb = AgentCircuitBreaker(agent_id="agent_x")
    assert cb.cb_status == "active"           # use_enum_values → stored as str
    assert cb.consecutive_failure_count == 0
    assert cb.failure_category is None
    assert cb.paused_reason is None

    now = datetime.now(timezone.utc)
    full = AgentCircuitBreaker(
        agent_id="agent_y",
        cb_status=CbStatus.PAUSED,
        consecutive_failure_count=3,
        failure_category=ErrorCategory.AUTH,
        cooldown_until=now,
        paused_reason=PausedReason.AUTH,
        paused_at=now,
        last_error="401 Unauthorized",
    )
    dumped = full.model_dump()
    assert dumped["cb_status"] == "paused"
    assert dumped["failure_category"] == "auth"
    assert dumped["paused_reason"] == "auth"
    # Round-trips back from a row-like dict.
    assert AgentCircuitBreaker(**dumped).agent_id == "agent_y"


def test_table_registered_with_both_dialects():
    assert "instance_agent_circuit_breaker" in TABLES
    table = TABLES["instance_agent_circuit_breaker"]
    cols = {c.name: c for c in table.columns}
    for expected in (
        "agent_id", "cb_status", "consecutive_failure_count",
        "failure_category", "cooldown_until", "paused_reason",
        "paused_at", "last_error", "created_at", "updated_at",
    ):
        assert expected in cols, f"missing column {expected}"
    # Both dialects must be filled for every column (auto_migrate contract).
    for c in table.columns:
        assert c.sqlite_type and c.mysql_type
    # agent_id is the unique business key.
    assert cols["agent_id"].unique
