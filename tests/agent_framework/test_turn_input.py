"""
@file_name: test_turn_input.py
@date: 2026-07-27
@description: Tests for TurnInput — the explicit materialized-layer bundle
step_3 hands to a driver.

Pins the exact kwargs shape step_3 historically passed piecemeal
(messages / mcp_servers / extra_env-or-None / disallowed_tools-or-None),
so packing them into one object is provably zero-behavior. The ``refs``
field is the reserved reference-layer half of the future TurnContext
(design §8.2) and must stay None until a driver actually consumes it.
"""
from __future__ import annotations

import dataclasses

import pytest

from xyz_agent_context.agent_framework.loop.turn_input import TurnInput


def _mk(**overrides):
    base = dict(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        mcp_servers={"chat_module": {"url": "http://x:7804/sse"}},
    )
    base.update(overrides)
    return TurnInput(**base)


def test_driver_kwargs_matches_legacy_call_shape():
    ti = _mk(
        disallowed_tools=("WebSearch",),
        extra_env={"TAVILY_API_KEY": "k"},
        agent_id="agent_x",
        expressive_tools=("mcp__chat_module__send_message_to_user_directly",),
    )
    kwargs = ti.driver_kwargs()
    assert kwargs == {
        "messages": ti.messages,
        "mcp_servers": ti.mcp_servers,
        "extra_env": {"TAVILY_API_KEY": "k"},
        "disallowed_tools": ["WebSearch"],
        "agent_id": "agent_x",
        "expressive_tools": ["mcp__chat_module__send_message_to_user_directly"],
    }
    # messages / mcp_servers ride through by reference — no copies that
    # would break the mutate-then-call pattern in step_3.3.
    assert kwargs["messages"] is ti.messages
    assert kwargs["mcp_servers"] is ti.mcp_servers


def test_empty_collections_normalize_to_none():
    """step_3 passed ``skill_env_vars or None`` and
    ``extra_disallowed_tools or None`` — empties become None so driver
    defaults engage identically."""
    kwargs = _mk().driver_kwargs()
    assert kwargs["extra_env"] is None
    assert kwargs["disallowed_tools"] is None


def test_expressive_tools_key_absent_when_empty_agent_id_always_present():
    """A mute turn emits no expressive_tools key (driver defaults engage);
    agent_id always rides along — NexusPower stamps it into ToolContext
    and every driver accepts it via **kwargs."""
    kwargs = _mk().driver_kwargs()
    assert "expressive_tools" not in kwargs
    assert kwargs["agent_id"] == "agent"


def test_refs_reserved_and_default_none():
    """Schema honesty: refs is declared for the §8.2 reference layer but
    no driver consumes it yet — it must default to None and TurnInput
    must not invent content for it."""
    assert _mk().refs is None


def test_turn_input_is_frozen():
    ti = _mk()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ti.messages = []  # type: ignore[misc]


def test_driver_kwargs_carries_no_resume_key():
    """The field is gone (2026-07-29): the claude adapter authors its own
    transcript, so no session id crosses this bundle. Asserted rather than
    simply dropped — re-introducing the key would silently start spamming
    CodexSDKv2's ignored-kwargs WARNING once per turn again."""
    assert "resume_session_id" not in _mk().driver_kwargs()


def test_driver_kwargs_excludes_cancellation_and_streaming():
    """cancellation stays a separately-passed argument (it is per-run
    control flow, not turn content); streaming keeps its driver default."""
    kwargs = _mk().driver_kwargs()
    assert "cancellation" not in kwargs
    assert "streaming" not in kwargs
