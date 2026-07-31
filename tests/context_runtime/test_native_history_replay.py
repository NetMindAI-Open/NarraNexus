"""
@file_name: test_native_history_replay.py
@author: Bin Liang
@date: 2026-07-29
@description: Native turn replay in build_input_for_framework.

Contract (Owner decisions 2026-07-29):
- ONLY when the agent's framework consumes structured history
  (NATIVE_REPLAY_FRAMEWORKS) do current-narrative assistant rows expand
  into the event_log-rebuilt assistant/tool sequence;
- the user row of a replayed turn keeps its flattened form (timeline
  tag anchoring); cross-narrative rows and rows without a foldable log
  keep their flattened form too;
- every failure path (identity lookup, event fetch, per-event fold)
  degrades to the flattened row — replay is enrichment, never fatal.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import xyz_agent_context.agent_framework.providers.model_identity as model_identity
from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.schema import ContextData

AGENT_ID = "agent_nhr"
USER_ID = "u_nhr"


def _event_log_json() -> str:
    return json.dumps(
        [
            {
                "timestamp": "2026-07-29T10:00:00Z",
                "type": "thinking",
                "content": {
                    "type": "thinking",
                    "content": "coalesced",
                    "monologue": "I should check the file. ",
                },
            },
            {
                "timestamp": "2026-07-29T10:00:01Z",
                "type": "tool_call",
                "content": {
                    "type": "tool_call",
                    "tool_name": "read_file",
                    "tool_call_id": "c1",
                    "arguments": {"path": "a.txt"},
                },
            },
            {
                "timestamp": "2026-07-29T10:00:02Z",
                "type": "tool_output",
                "content": {
                    "type": "tool_output",
                    "output": "file contents",
                    "tool_call_id": "c1",
                },
            },
        ]
    )


class _FakeDB:
    """Just enough surface for _load_native_turn_replays."""

    def __init__(self, rows: dict[str, dict] | Exception = ()):  # type: ignore[assignment]
        self._rows = rows

    async def get_by_ids(self, table: str, id_field: str, ids: list[str]):
        assert table == "events" and id_field == "event_id"
        if isinstance(self._rows, Exception):
            raise self._rows
        return [self._rows.get(i) for i in ids]


def _row(role: str, content: str, event_id: str, *, short_term: bool = False) -> dict:
    meta: dict = {
        "event_id": event_id,
        "timestamp": f"2026-07-29T10:0{0 if role == 'user' else 1}:00",
        "working_source": "chat",
    }
    if short_term:
        meta["memory_type"] = "short_term"
        meta["narrative_id"] = "nar_other"
    return {"role": role, "content": content, "meta_data": meta}


def _ctx(chat_history: list[dict]) -> ContextData:
    ctx = ContextData(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        input_content="next question",
        working_source="chat",
    )
    ctx.chat_history = chat_history
    return ctx


def _identity(framework: str):
    async def _resolve(agent_id: str, db):  # noqa: ANN001
        return SimpleNamespace(framework=framework)

    return _resolve


async def _build(runtime: ContextRuntime, ctx: ContextData) -> list[dict]:
    final_messages, _mcp, _dis, _expr = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="SYSTEM",
        active_instances=[],
        ctx_data=ctx,
    )
    return final_messages


@pytest.mark.asyncio
async def test_nexus_framework_expands_current_narrative_turn(monkeypatch):
    monkeypatch.setattr(
        model_identity, "resolve_agent_model_identity", _identity("nexus_power")
    )
    db = _FakeDB({"evt_1": {"event_id": "evt_1", "event_log": _event_log_json()}})
    runtime = ContextRuntime(AGENT_ID, USER_ID, database_client=db)
    ctx = _ctx(
        [
            _row("user", "please check the file", "evt_1"),
            _row("assistant", "flattened reply", "evt_1"),
        ]
    )
    messages = await _build(runtime, ctx)

    roles = [m["role"] for m in messages]
    # system, flattened user, native assistant, native tool, current user
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assistant = messages[2]
    assert assistant["content"] == "I should check the file. "
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "file contents",
    }
    # The flattened assistant text must NOT appear anywhere (no duplicate).
    assert all("flattened reply" not in str(m.get("content")) for m in messages)
    # The user row keeps its timeline anchoring (flattened, prefixed).
    assert "please check the file" in messages[1]["content"]


@pytest.mark.asyncio
async def test_cli_framework_keeps_flattened_history(monkeypatch):
    monkeypatch.setattr(
        model_identity, "resolve_agent_model_identity", _identity("claude_code")
    )
    db = _FakeDB({"evt_1": {"event_id": "evt_1", "event_log": _event_log_json()}})
    runtime = ContextRuntime(AGENT_ID, USER_ID, database_client=db)
    ctx = _ctx(
        [
            _row("user", "please check the file", "evt_1"),
            _row("assistant", "flattened reply", "evt_1"),
        ]
    )
    messages = await _build(runtime, ctx)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "flattened reply" in messages[2]["content"]


@pytest.mark.asyncio
async def test_cross_narrative_rows_never_expand(monkeypatch):
    monkeypatch.setattr(
        model_identity, "resolve_agent_model_identity", _identity("nexus_power")
    )
    db = _FakeDB({"evt_x": {"event_id": "evt_x", "event_log": _event_log_json()}})
    runtime = ContextRuntime(AGENT_ID, USER_ID, database_client=db)
    ctx = _ctx([_row("assistant", "other thread reply", "evt_x", short_term=True)])
    messages = await _build(runtime, ctx)
    assert [m["role"] for m in messages] == ["system", "assistant", "user"]
    assert "other thread reply" in messages[1]["content"]


@pytest.mark.asyncio
async def test_turn_without_foldable_log_stays_flat(monkeypatch):
    monkeypatch.setattr(
        model_identity, "resolve_agent_model_identity", _identity("nexus_power")
    )
    # evt_1 missing entirely; evt_2 has an empty log.
    db = _FakeDB({"evt_2": {"event_id": "evt_2", "event_log": "[]"}})
    runtime = ContextRuntime(AGENT_ID, USER_ID, database_client=db)
    ctx = _ctx(
        [
            _row("assistant", "old turn", "evt_1"),
            _row("assistant", "empty log turn", "evt_2"),
        ]
    )
    messages = await _build(runtime, ctx)
    assert [m["role"] for m in messages] == ["system", "assistant", "assistant", "user"]


@pytest.mark.asyncio
async def test_db_failure_degrades_to_flattened(monkeypatch):
    monkeypatch.setattr(
        model_identity, "resolve_agent_model_identity", _identity("nexus_power")
    )
    db = _FakeDB(RuntimeError("db down"))
    runtime = ContextRuntime(AGENT_ID, USER_ID, database_client=db)
    ctx = _ctx([_row("assistant", "flattened reply", "evt_1")])
    messages = await _build(runtime, ctx)
    assert [m["role"] for m in messages] == ["system", "assistant", "user"]
    assert "flattened reply" in messages[1]["content"]
