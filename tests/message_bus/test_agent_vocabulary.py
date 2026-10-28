"""
@file_name: test_agent_vocabulary.py
@author:
@date: 2026-08-18
@description: Words the agent must never be shown, checked across every surface
              that reaches it.

Spec §10.4 asked for this test and §8 states the acceptance criterion it checks:
the subsystem's name appears in NO agent-visible text. It was not written, and
the cost was measurable — a review found eleven live strings naming a retired
owner tool, five `"MessageBus not available"` errors returned straight to the
model, a `find_agent` tool description advertising "the MessageBus registry",
and a `### Your Channels` heading printing raw channel ids into every turn. Each
one was individually obvious; what was missing was anything that looked at all
of them at once.

Why a vocabulary test rather than more per-string assertions: the redesign's
whole claim is that the agent's world has exactly two kinds of conversation in
it. That claim is falsified by ANY leaked third concept — a transport, a channel
id, an internal prefix, a tool that no longer exists — and which one leaks next
is unpredictable, whereas the vocabulary is a closed list. Per-string tests
guard the strings someone already thought of.

The surfaces collected here are the ones the model actually reads: the module's
byte-stable instruction block, its per-turn span, every MCP tool description
(the docstring IS the description), and the trigger prompts. If a new surface
appears, add it — a banned word is only banned where someone looks.
"""
from __future__ import annotations

import inspect

import pytest

from narranexus_plugins.message_bus_module.message_bus_module import (
    MessageBusModule,
)

#: Each entry is (fragment, why it must not reach the agent). Matched
#: case-insensitively, because the failure is the CONCEPT reaching the model, and
#: "MessageBus" vs "messagebus" is not a distinction the reader makes.
BANNED = [
    ("messagebus", "names a subsystem the agent has no model of (spec §8)"),
    ("bus_send_message", "retired tool — the agent would call a name that is gone"),
    ("bus_send_to_agent", "retired tool"),
    ("bus_create_channel", "retired tool"),
    ("bus_share_to_team", "retired tool → team_share_file"),
    ("send_message_to_user_directly", "retired tool, split into reply/notify_owner"),
    # The rest of the same rename batch — a guard that lists only some of the
    # retired names reads green while the omitted ones can be written back
    # unnoticed (verified retired: no live def in src/).
    ("bus_get_messages", "retired tool"),
    ("bus_search_agents", "retired tool → find_agent"),
    ("bus_get_unread", "retired tool"),
    ("bus_pin_team_rule", "retired tool"),
    ("bus_unpin_team_rule", "retired tool"),
    ("bus_list_team_files", "retired tool → team_list_files"),
    ("bus_get_channel_members", "retired tool"),
    ("bus_leave_channel", "retired tool"),
    ("bus_kick_member", "retired tool"),
    ("bus_get_agent_profile", "retired tool — deleted outright"),
    ("work_add_item", "retired tool → team_work_add"),
    ("work_complete_item", "retired tool"),
    ("work_claim_item", "retired tool"),
    ("work_list_items", "retired tool"),
    # NOTE: the old `work_update_status` is deliberately NOT here — its live
    # replacement `team_work_update_status` CONTAINS it as a substring, and the
    # match below is substring-based, so banning it would flag the current tool.
    # The other four retired work_* names have no live superstring.
    ("### your channels", "prints raw channel ids; the agent has no channels"),
    ("usr_", "an internal sender prefix; the agent is shown `User`"),
]

#: Words that are legitimate in a HISTORICAL note ("the old `bus_send_message`
#: could…") but never in text the model reads. The distinction is the surface,
#: not the word, so nothing is exempted here — instead the collectors below take
#: only agent-facing text, never module comments.
def _module() -> MessageBusModule:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    return module


def _static_block() -> str:
    return "\n".join(_module()._static_instruction_parts())


def _tool_descriptions() -> str:
    """Every MCP tool's docstring — that string IS what the model is shown.

    Registered against a stub server so the real docstrings are collected rather
    than re-typed here, which is the only version that cannot drift.
    """
    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools

    collected: list[str] = []

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                collected.append(inspect.getdoc(fn) or "")
                return fn

            return _wrap

    async def _no_bus():
        return None

    _message_bus_mcp_tools.register_message_bus_mcp_tools(_Stub(), _no_bus)
    assert collected, "no tool descriptions were collected — the stub missed them"
    _tool_descriptions.count = len(collected)
    return "\n".join(collected)


def _volatile_span() -> str:
    """The per-turn span, with a populated ctx — an empty one renders nothing."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(extra_data={
        "bus_known_agents": [
            {"agent_id": "agent_peer", "agent_name": "Peer",
             "agent_description": "helper"},
        ],
        "bus_unread_messages": [
            {"from_agent": "usr_owner", "channel_id": "ch_1", "content": "hi"},
            {"from_agent": "agent_peer", "channel_id": "ch_room", "content": "in room"},
        ],
        "bus_unread_total": 2,
        "bus_room_labels": {"ch_room": {"name": "Ops", "team_id": "team_ops"}},
    })
    return "\n".join(_module()._volatile_context_parts(ctx))


def _team_prompt() -> str:
    """The trigger's team-room prompt — the largest agent-visible surface here.

    Built from a minimal batch rather than skipped: this is the surface most
    likely to leak `usr_`, a raw `team_<id>` marker or a retired tool name,
    because it is assembled per turn from live rows rather than written once.
    """
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
    from narranexus.platform.message_bus.schemas import BusMessage

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msgs = [
        BusMessage(
            message_id="m1", channel_id="ch_room", from_agent="usr_owner",
            content="@Ana who takes the index?", mentions=["agent_ana"],
        )
    ]
    return trigger._build_team_prompt(
        agent_id="agent_ana",
        history=msgs,
        roster=[{"agent_id": "agent_ana", "name": "Ana"},
                {"agent_id": "agent_bo", "name": "Bo"}],
        team_id="t_1",
        trigger_messages=msgs,
        lead_agent_id="agent_ana",
        bulletin=None,
    )


def _agent_facing_texts() -> list[tuple[str, str]]:
    """Every surface the model actually reads.

    The docstring above used to name four and this function returned two — the
    per-turn span and the trigger prompt were absent, i.e. the biggest surface in
    the system was outside the guard that exists to close this class, while the
    test's green read as coverage.
    """
    out = [
        ("the byte-stable instruction block", _static_block()),
        ("the MCP tool descriptions", _tool_descriptions()),
        ("the per-turn span", _volatile_span()),
    ]
    try:
        out.append(("the team-room trigger prompt", _team_prompt()))
    except Exception as e:  # noqa: BLE001
        # Loud, not skipped: a prompt builder this test cannot construct is a
        # surface nobody is checking, which is the state that let the leaks in.
        raise AssertionError(
            f"could not build the team prompt, so it is unguarded: "
            f"{type(e).__name__}: {e}"
        ) from e

    # More surfaces the model reads that the first version of this guard did NOT
    # collect — currently clean, so this is future-proofing: the guard's green
    # was read as "covered" while these were outside it. Prompt modules are
    # collected as their raw UPPER_CASE string constants (the reviewer's note:
    # `.format()` on a template with placeholders would raise, and the concept
    # leaking is a property of the literal, not the rendered form).
    for label, dotted in (
        ("channel_prompts", "narranexus.platform.channel.channel_prompts"),
        ("job_module prompts", "narranexus_plugins.job_module.prompts"),
        ("basic_info_module prompts", "narranexus_plugins.basic_info_module.prompts"),
    ):
        out.append((f"{label} constants", _module_prompt_constants(dotted)))
    return out


def _module_prompt_constants(dotted: str) -> str:
    """Every UPPER_CASE string constant of a prompt module — the templates the
    model is shown. Constants only (not source), so module comments — which MAY
    carry a retired name in a historical note — are not what is checked."""
    import importlib

    mod = importlib.import_module(dotted)
    parts = [
        v for name, v in vars(mod).items()
        if name.isupper() and isinstance(v, str)
    ]
    return "\n".join(parts)


@pytest.mark.parametrize("fragment,why", BANNED)
def test_no_agent_visible_surface_uses_the_banned_word(fragment, why):
    for surface, text in _agent_facing_texts():
        assert fragment not in text.lower(), (
            f"{surface} contains {fragment!r}: {why}"
        )


def test_the_error_strings_returned_to_the_agent_name_no_subsystem():
    """Tool RETURN values are agent-visible too, and they were the biggest leak.

    Five sites returned `{"error": "MessageBus not available"}` straight to the
    model. Read off the source rather than by calling each tool: reaching the
    failure branch means faking a downed backend per tool, and the property under
    test is about the string, not the path to it.
    """
    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools

    src = inspect.getsource(_message_bus_mcp_tools)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or '"error"' not in stripped:
            continue
        assert "MessageBus" not in stripped, (
            f"an error returned to the agent names the subsystem: {stripped}"
        )


def test_the_banned_list_would_actually_fail():
    """A guard against the guard: if the collectors returned empty strings, every
    assertion above would pass and mean nothing.

    Checks that the collected text contains something the agent IS supposed to
    see, so a broken collector fails here instead of silently certifying.
    """
    static = _static_block().lower()
    tools = _tool_descriptions().lower()

    # A tool's own name is not in its own docstring, so the collector is checked
    # by count and by a phrase only a real description would carry.
    assert _tool_descriptions.count >= 5, (
        f"only {_tool_descriptions.count} tool descriptions collected — the stub "
        f"is no longer seeing the registrations"
    )
    assert "send a private message to another agent" in tools, (
        "the tool-description collector came back without real docstrings"
    )
    assert len(static) > 500, f"the static block collapsed to {len(static)} chars"

    # The two surfaces added on 2026-08-18 need the same protection: a collector
    # that silently returns "" makes every banned-word assertion over it vacuous,
    # and the whole point of adding them was that they were not being checked.
    surfaces = dict(_agent_facing_texts())
    assert "### Known Agents" in surfaces["the per-turn span"], (
        "the per-turn span collector came back without its live lists"
    )
    team = surfaces["the team-room trigger prompt"]
    assert len(team) > 500 and "Ops" not in team, (
        f"the team prompt collector returned {len(team)} chars"
    )
    # And it must render the human sender as `User`, which is also WHY the
    # `usr_` assertion over this surface passes rather than being unexercised.
    assert "User" in team, "the team prompt did not render its human sender"
