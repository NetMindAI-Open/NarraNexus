"""
@file_name: test_unset_description_surfaces.py
@author: NarraNexus
@date: 2026-08-04
@description: An agent with no description says NOTHING about it — to peers and
to itself.

P1 section 02: the creation placeholder ("A new agent ready for configuration") was
never replaced and reached the model on three surfaces. Two of them are
rendered here, and both were actively harmful rather than merely useless:

  * the Known Agents list injected into every bus turn showed that line beside
    EVERY peer, so an agent told to "ask the teaching expert" read a roster in
    which no entry claimed to be anything;
  * BasicInfo injects the same field as the agent's OWN self-description, so
    the agent read that it was a new agent awaiting configuration — and said
    so when a peer asked whether it was set up (evt_feb1f6ae).

(The third surface, ``bus_agent_registry``, is covered by
tests/message_bus/test_agent_discovery_sync.py.)
"""
from __future__ import annotations

import inspect

import pytest

from narranexus.platform.schema import (
    LEGACY_AGENT_DESCRIPTION_PLACEHOLDER,
    ContextData,
)

PLACEHOLDER = LEGACY_AGENT_DESCRIPTION_PLACEHOLDER


# ---------------------------------------------------------------------------
# Known Agents (peer roster)
# ---------------------------------------------------------------------------


def _roster(known: list[dict]) -> str:
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    module = MessageBusModule(agent_id="agent_me", user_id="user_tc",
                              database_client=object())
    ctx = ContextData(agent_id="agent_me", input_content="hi")
    ctx.extra_data = {"bus_known_agents": known}
    return "\n".join(str(p) for p in module._volatile_context_parts(ctx))


@pytest.mark.parametrize("description", [PLACEHOLDER, "", None])
def test_a_peer_without_a_description_is_listed_by_name_only(description):
    out = _roster([
        {"agent_id": "agent_peer", "agent_name": "咕咕嘎嘎",
         "agent_description": description},
    ])

    assert "agent_peer" in out and "咕咕嘎嘎" in out
    assert "ready for configuration" not in out.lower()
    # No dangling separator either — the line must simply end after the name.
    assert "咕咕嘎嘎:" not in out


def test_a_real_peer_description_is_shown():
    out = _roster([
        {"agent_id": "agent_peer", "agent_name": "咕咕嘎嘎",
         "agent_description": "Reviews lesson plans."},
    ])

    assert "Reviews lesson plans." in out


def test_one_configured_peer_stays_distinguishable_among_blank_ones():
    """The point of the fix: the roster must let the asking agent tell peers
    apart. With the placeholder printed everywhere, every line looked alike."""
    out = _roster([
        {"agent_id": "agent_a", "agent_name": "A", "agent_description": PLACEHOLDER},
        {"agent_id": "agent_b", "agent_name": "B",
         "agent_description": "Teaching expert: curriculum and lesson review."},
        {"agent_id": "agent_c", "agent_name": "C", "agent_description": ""},
    ])

    assert out.count("Teaching expert") == 1
    assert "ready for configuration" not in out.lower()


# ---------------------------------------------------------------------------
# BasicInfo (the agent's own self-description)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [PLACEHOLDER, "", None])
async def test_the_agent_reads_an_instruction_not_a_false_status(stored, monkeypatch):
    from narranexus_plugins.basic_info_module import basic_info_module as mod

    ctx = await _gather_basic_info(mod, monkeypatch, stored)

    assert ctx.agent_description == mod.UNSET_AGENT_DESCRIPTION_NOTICE
    assert "ready for configuration" not in ctx.agent_description.lower()
    # It must point at the fix, so an agent that notices can act in-turn.
    assert "update_agent_profile" in ctx.agent_description


@pytest.mark.asyncio
async def test_a_real_self_description_is_passed_through_verbatim(monkeypatch):
    from narranexus_plugins.basic_info_module import basic_info_module as mod

    ctx = await _gather_basic_info(mod, monkeypatch, "Reviews lesson plans.")

    assert ctx.agent_description == "Reviews lesson plans."


async def _gather_basic_info(mod, monkeypatch, stored_description):
    """Run BasicInfoModule.gather against a stubbed agent row."""
    from types import SimpleNamespace

    class _FakeAgentRepo:
        def __init__(self, _db):
            pass

        async def get_agent(self, agent_id):
            return SimpleNamespace(
                agent_id=agent_id, agent_name="咕咕嘎嘎",
                agent_description=stored_description, created_by="user_tc",
                agent_create_time=None, is_public=False,
            )

    class _FakeUserRepo:
        def __init__(self, _db):
            pass

        async def get_display_name(self, user_id):
            return "TC"

    import narranexus.platform.repository as repo_pkg
    monkeypatch.setattr(repo_pkg, "AgentRepository", _FakeAgentRepo)
    monkeypatch.setattr(repo_pkg, "UserRepository", _FakeUserRepo)
    monkeypatch.setattr(mod, "AgentRepository", _FakeAgentRepo, raising=False)

    module = mod.BasicInfoModule(agent_id="agent_me", user_id="user_tc",
                                 database_client=object())
    ctx = ContextData(agent_id="agent_me", input_content="hi")
    return await module.gather(ctx)


# ---------------------------------------------------------------------------
# One writer for the discovery row (review 2026-08-05, issue 1)
# ---------------------------------------------------------------------------


def test_no_second_registry_writer_is_exposed_as_a_tool():
    """``bus_register_agent`` used to let an agent rewrite its own discovery
    row. It wrote ``owner_user_id=""`` while ``search_agents`` filters on that
    column, so one call made the agent vanish from its owner's search until the
    next turn re-synced it — the very failure this work removes, re-armed. It
    also reset ``registered_at`` and let the agent declare capabilities that
    the per-turn derivation would silently overwrite.

    Deleted rather than shimmed (iron rule #2): description belongs to
    ``update_agent_profile``, capabilities are derived from installed skills
    and active modules.
    """
    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools

    source = inspect.getsource(_message_bus_mcp_tools)
    assert "bus_register_agent" not in source


def test_the_instruction_no_longer_points_at_the_deleted_tool():
    """A prompt naming a tool that does not exist teaches the model to fail;
    this one used to send it there precisely when it wanted to fix its
    profile."""
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    text = "\n".join(
        MessageBusModule(
            agent_id="agent_x", user_id="user_tc", database_client=None
        )._static_instruction_parts()
    )
    assert "bus_register_agent" not in text
    assert "update_agent_profile" in text
