"""
@file_name: test_agent_peer_signal.py
@author:
@date: 2026-08-26
@description: "Is the far side another agent?" — one definition, filled
everywhere, reaching the model.

The DM Communication Protocol's "Breaking a Loop" section leans on knowing
whether the other party is a machine, and until now the platform had no
way to say so: the model could only guess from how the messages read.

This pins three things, because the signal is worthless if any one of them
is missing:
  1. the seam answers (per channel, one definition)
  2. every ChannelTag construction site fills it
  3. the tag actually renders it, so the model sees it

Point 2 is the fragile one. ``build_trigger_extra_data`` was once
hand-rolled at four sites and a new key was added to exactly one of them —
the same shape as the tag sites here.

Editing history worth knowing: this file was once edited by truncating it
at a marker string, which silently deleted seven tests — including the one
linking the prompt literal to ``AGENT_PEER_MARKER``. The suite stayed green
because the survivors still passed. **Green is not "complete"**; when
editing this file, diff the set of test names before and after.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.module_system.channel_trigger_map import CHANNEL_TRIGGER_MAP
from narranexus.platform.schema.channel_tag import AGENT_PEER_MARKER, ChannelTag
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage


def _msg(sender_id: str) -> ParsedMessage:
    return ParsedMessage(
        message_id="m1",
        chat_id="!room",
        sender_id=sender_id,
        sender_name="somebody",
        content="hi",
        chat_type=ChatType.PRIVATE,
    )


# ── 1. the seam ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cls", sorted(CHANNEL_TRIGGER_MAP.values(), key=lambda c: c.__name__)
)
def test_every_channel_answers_the_question(cls):
    """Feed each channel a message and check what comes back.

    ``callable(getattr(cls, "is_agent_peer"))`` — the first version — could
    never fail: the base class defines it, so every subclass inherits
    something callable. Actually calling it catches an override that
    returns None, raises, or hands back a truthy non-bool (which would sail
    through ``if is_agent_peer:`` and then serialise as junk on the tag).
    """
    # Unbound on purpose: the seam's documented contract is "depend on
    # ``message`` only". Calling it this way is how that contract is
    # enforced rather than merely stated.
    got = cls.is_agent_peer(None, _msg("U123"))  # type: ignore[arg-type]
    assert isinstance(got, bool), f"{cls.__name__} returned {got!r}"


def test_the_default_is_human():
    """Guessing "human" is the safe direction: it changes nothing about how
    the turn is handled, it only withholds one hint from the model."""
    assert ChannelTriggerBase.is_agent_peer(None, _msg("U123")) is False  # type: ignore[arg-type]


def test_narramessenger_reads_it_off_the_mxid():
    """The platform mints agent identities as ``@agent-<id>:<homeserver>``,
    so on this channel it is a fact rather than a guess — and this is the
    one channel where two agents routinely hold a 1:1 conversation with
    nobody else in the room."""
    matrix = CHANNEL_TRIGGER_MAP["narramessenger"]
    assert matrix.is_agent_peer(None, _msg("@agent-e7726996:matrix.netmind.chat")) is True  # type: ignore[arg-type]
    assert matrix.is_agent_peer(None, _msg("@liam:matrix.netmind.chat")) is False  # type: ignore[arg-type]


# ── 2. every construction site fills it ───────────────────────────────

def _code(obj) -> str:
    """Source with comments AND docstrings removed.

    Both would otherwise feed the counting assertions below: a module
    docstring showing ``is_agent_peer=True`` in an example counts as a fill,
    and a comment naming a call counts as a call.
    """
    import ast

    src = inspect.getsource(obj)
    lines = src.splitlines()
    # Blank out docstrings by LINE RANGE, not by string replace: what
    # ``ast.get_docstring`` hands back is the parsed value, while the source
    # holds the pre-escape form — any docstring with an escape or implicit
    # concatenation would silently fail to match and stay in the text.
    drop: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            drop.update(range(first.lineno - 1, (first.end_lineno or first.lineno)))
    return "\n".join(
        ln
        for i, ln in enumerate(lines)
        if i not in drop and not ln.strip().startswith("#")
    )


def _modules_that_build_receive_path_tags():
    """Derived from the trigger registry, not hardcoded.

    The first version listed three modules by hand and therefore could not
    see the fourth construction site (``backend/routes/manyfold/sync.py``)
    — the one whose tag is what the model actually reads. A guard whose
    coverage is a literal list gives the false comfort of "CI will catch
    me" while a new channel walks straight past it.
    """
    import importlib
    import inspect as _inspect

    import narranexus.platform.channel.channel_trigger_base as base

    mods = {base}
    for cls in CHANNEL_TRIGGER_MAP.values():
        mod = _inspect.getmodule(cls)
        if mod is not None:
            mods.add(mod)
    # The managed/platform-forwarded path lives in backend, and its tag is
    # the one the model reads on that surface.
    mods.add(importlib.import_module("backend.routes.manyfold.sync"))
    return sorted(mods, key=lambda m: m.__name__)


def test_the_guard_sees_every_registered_channel():
    """CHANNEL_TRIGGER_MAP is defensively imported (a channel whose optional
    dependency is missing is skipped silently). If that happens in CI, the
    guard below would quietly stop checking that channel."""
    from narranexus.platform.module_system.channel_trigger_map import (
        registered_trigger_class_names,
    )

    assert len(CHANNEL_TRIGGER_MAP) == len(registered_trigger_class_names()), (
        "a channel failed to import — the fill guard is running blind on it"
    )


def _trigger_modules():
    import inspect as _inspect

    import narranexus.platform.channel.channel_trigger_base as base

    mods = {base}
    for cls in CHANNEL_TRIGGER_MAP.values():
        mod = _inspect.getmodule(cls)
        if mod is not None:
            mods.add(mod)
    return mods


# Argument names that mean "the entire serialised tag", as opposed to one
# field of it. Kept explicit so adding a rehydrator is a deliberate edit
# here rather than an accident of how many parameters it happens to take.
#
# The trade is deliberate: a rehydrator whose argument is NOT on this list
# (``from_json(s)``, ``of(blob)``) gets treated as a builder and this test
# demands it fill the flag — a loud, explained failure. The previous
# criterion failed the other way, silently letting a future single-FIELD
# factory out of the guard. So: adding a rehydrator means either naming
# its argument from this list, or adding the new name to it.
_WHOLE_PAYLOAD_ARG_NAMES = {"data", "raw", "payload", "tag_str", "serialised"}


def _tag_builders() -> set[str]:
    """Factory names that build a tag FROM FIELDS.

    Derived from the signatures, not listed: ``from_dict`` / ``parse``
    rehydrate an already-serialised tag, so the flag is whatever was
    serialised and there is nothing for a caller to fill. Telling them
    apart by "takes one argument" keeps a new factory in scope
    automatically — a literal list is how the ``build_trigger_extra_data``
    lesson happened in the first place.
    """
    out = set()
    for name, value in vars(ChannelTag).items():
        if not isinstance(value, (staticmethod, classmethod)):
            continue
        params = list(inspect.signature(getattr(ChannelTag, name)).parameters)
        # A rehydrator takes the serialised tag as one whole-payload
        # argument. Testing the NAME of that argument rather than just
        # counting parameters keeps a future single-FIELD factory
        # (``ChannelTag.slack(sender_id)``) inside the guard — counting
        # alone would quietly drop it, and a dropped site fails by
        # reporting "human", never by erroring.
        if len(params) == 1 and params[0] in _WHOLE_PAYLOAD_ARG_NAMES:
            continue
        out.add(name)
    return out


def _channel_tag_calls(src: str) -> list[ast.Call]:
    """Every construction of a ChannelTag from fields."""
    builders = _tag_builders()
    out: list[ast.Call] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "ChannelTag":
            out.append(node)
        elif (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "ChannelTag"
            and f.attr in builders
        ):
            out.append(node)
    return out


def _fills_from_seam(call: ast.Call) -> bool:
    """``is_agent_peer=self.is_agent_peer(...)`` and nothing else.

    A bare literal would satisfy "the keyword is present" while reporting
    every A2A DM as human — no error, exactly the rot this guards.
    """
    for kw in call.keywords:
        if kw.arg != "is_agent_peer":
            continue
        v = kw.value
        return (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Attribute)
            and v.func.attr == "is_agent_peer"
            and isinstance(v.func.value, ast.Name)
            and v.func.value.id == "self"
        )
    return False


def test_every_channel_tag_built_on_the_receive_path_fills_the_flag():
    """A site that forgets it does not fail — it silently reports "human",
    which is exactly how a signal like this rots. ``build_trigger_extra_data``
    already taught this lesson: hand-rolled at four sites, new key added to
    one.

    Two predicates, by module KIND (derived, never a literal list — that is
    what the first version got wrong):

    - a **trigger** module must pass the seam's return value. Accepting a
      bare ``is_agent_peer=...`` would let the next person satisfy CI with
      a hardcoded ``False``, and a hardcoded False is precisely the failure
      this guard exists to prevent: no error, every A2A DM quietly reported
      as human.
    - the **managed** construction site legitimately builds ``False`` (the
      trigger has not run yet at that point), so there the contract is
      "False now, re-rendered later" — assert the re-render exists in the
      same module, otherwise that False is not legitimate any more.
    """
    triggers = _trigger_modules()
    missing = []
    for mod in _modules_that_build_receive_path_tags():
        src = _code(mod)
        # Per CONSTRUCTION, via the AST — not by counting the keyword
        # across the module. The seam has consumers other than tag
        # building (the ingress breaker asks the same question), so a
        # module-wide count says "3 passes for 2 tags" and fails on code
        # that is entirely correct. Worse, it would also PASS a module that
        # filled one tag twice and another not at all.
        tags = _channel_tag_calls(src)
        built = len(tags)
        if not built:
            continue
        if mod in triggers:
            filled = sum(1 for call in tags if _fills_from_seam(call))
            if built != filled:
                missing.append(
                    f"{mod.__name__}: {built} built, {filled} pass the seam"
                )
        else:
            filled = sum(
                1 for call in tags
                if any(kw.arg == "is_agent_peer" for kw in call.keywords)
            )
            if built != filled:
                missing.append(f"{mod.__name__}: {built} built, {filled} filled")
            elif "def retag_managed_input(" not in src:
                missing.append(
                    f"{mod.__name__}: builds a tag without the re-render that "
                    f"makes its False legitimate"
                )
    assert not missing, (
        f"ChannelTag sites that do not fill is_agent_peer: {missing}. A "
        f"missing fill reads as 'human' — silently."
    )


async def test_the_managed_path_puts_the_marker_in_what_the_model_reads(
    monkeypatch,
):
    """The end of the chain, on the surface where it matters most — driven
    through the REAL ``before_run``.

    Managed turns render their tag inside ``build_inbound_run_context``,
    but ``is_agent_peer`` is only known once the channel's trigger has seen
    the turn, which happens later in ``before_run``. So the stamp lands in
    the tag DICT while the string already handed to the model is the
    pre-stamp one; ``retag_managed_input`` closes that gap.

    The first version of this test hand-copied the three stamping lines out
    of ``before_run`` instead of calling it — which meant deleting the
    stamping from production left the whole suite green, because the test
    was writing the flag itself. A test that reimplements the thing it
    checks guards nothing.

    NarraMessenger's ``managed_before_run`` is a fail-CLOSED authorization
    gate, so it is replaced here: authorization is a different concern and
    letting it run would deny the turn (and then want a db for the audit).
    Replacing it is the point — not an excuse to go back to copying.
    """
    from backend.routes.manyfold.sync import (
        build_inbound_run_context,
        retag_managed_input,
    )
    from narranexus.platform.module_system.managed_channel_ingress import (
        ManagedChannelIngress,
    )
    from narranexus.platform.schema.hook_schema import WorkingSource

    _, run_input, extra = build_inbound_run_context(
        channel_provider="narramessenger",
        channel_context={
            "sender_id": "@agent-e7726996:matrix.netmind.chat",
            "sender_name": "Liam",
            "room_id": "!room:h",
            "chat_type": "private",
        },
        user_input="ping",
        session_id="s1",
    )
    assert AGENT_PEER_MARKER not in run_input, "nothing can know it this early"

    ingress = ManagedChannelIngress()
    trigger = ingress._trigger("narramessenger")
    if trigger is None:  # pragma: no cover — optional dependency missing
        pytest.skip("narramessenger trigger not importable")

    async def _allow(self, **kwargs):
        return True, ""

    monkeypatch.setattr(type(trigger), "managed_before_run", _allow)

    allowed, _receipt = await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="ping",
        trigger_extra_data=extra,
        db=None,
    )
    assert allowed

    assert AGENT_PEER_MARKER in retag_managed_input(extra, "ping"), (
        "the model reads this string — the stamped dict is not enough"
    )


async def test_the_managed_path_leaves_a_human_turn_untouched(monkeypatch):
    """Same path, human sender: the rebuilt string must be byte-identical
    to the one build_inbound_run_context produced."""
    from backend.routes.manyfold.sync import (
        build_inbound_run_context,
        retag_managed_input,
    )
    from narranexus.platform.module_system.managed_channel_ingress import (
        ManagedChannelIngress,
    )
    from narranexus.platform.schema.hook_schema import WorkingSource

    _, run_input, extra = build_inbound_run_context(
        channel_provider="narramessenger",
        channel_context={
            "sender_id": "@liam:matrix.netmind.chat",
            "sender_name": "Liam",
            "room_id": "!room:h",
            "chat_type": "private",
        },
        user_input="ping",
        session_id="s1",
    )
    ingress = ManagedChannelIngress()
    trigger = ingress._trigger("narramessenger")
    if trigger is None:  # pragma: no cover
        pytest.skip("narramessenger trigger not importable")

    async def _allow(self, **kwargs):
        return True, ""

    monkeypatch.setattr(type(trigger), "managed_before_run", _allow)
    await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="ping",
        trigger_extra_data=extra,
        db=None,
    )

    assert "is_agent_peer" not in extra["channel_tag"], (
        "falsy fields do not appear — same rule as ChannelTag.to_dict"
    )
    assert retag_managed_input(extra, "ping") == run_input


def test_the_render_round_trip_is_lossless():
    """``retag_managed_input`` runs on EVERY managed IM turn, and its floor
    is ``from_dict(t.to_dict()).format() == t.format()``. Nothing was
    watching that invariant; breaking it would corrupt the tag on all
    managed turns, not just the agent ones, and the red test would land far
    from the change."""
    for tag in (
        ChannelTag(channel="lark", sender_name="Alice", sender_id="ou_1"),
        ChannelTag(channel="lark", sender_name="A", sender_id="", room_id="oc_1"),
        ChannelTag(
            channel="narramessenger", sender_name="Liam", sender_id="@agent-x:h",
            room_id="!r", room_name="Room", is_agent_peer=True,
        ),
        ChannelTag(
            channel="wechat", sender_name="Bot", sender_id="wx1",
            is_agent_peer=True,
        ),
    ):
        assert ChannelTag.from_dict(tag.to_dict()).format() == tag.format()


def test_a_newline_in_a_display_name_cannot_break_the_tag():
    """``sender_name`` is a platform-supplied display name. The tag is a
    single-line protocol, so internal whitespace is collapsed at the door —
    otherwise a two-line tag would silently skip the re-render (and split
    the line in chat history)."""
    from backend.routes.manyfold.sync import build_inbound_run_context

    _, run_input, extra = build_inbound_run_context(
        channel_provider="narramessenger",
        channel_context={
            "sender_id": "@agent-x:h",
            "sender_name": "Li\nam",
            "room_id": "!r",
        },
        user_input="ping",
        session_id="s1",
    )
    assert run_input.count("\n") == 1
    assert extra["channel_tag"]["sender_name"] == "Li am"


def test_retag_produces_exactly_one_tag_line():
    """One line, never two.

    A stale tag is bad; two tag lines are worse — the second would be read
    as message content. (This guard was lost once, silently, when this file
    was edited by truncation; see the module docstring.)
    """
    from backend.routes.manyfold.sync import retag_managed_input

    extra = {
        "channel_tag": {
            "channel": "narramessenger", "sender_name": "Liam",
            "sender_id": "@agent-x:h", "room_id": "!r", "is_agent_peer": True,
        }
    }
    out = retag_managed_input(extra, "ping")
    assert out.count("\n") == 1
    assert out.endswith("\nping")
    assert AGENT_PEER_MARKER in out


def test_retag_leaves_a_plain_manyfold_turn_alone():
    """The pure-Manyfold branch returns early with no channel_tag at all."""
    from backend.routes.manyfold.sync import retag_managed_input

    assert retag_managed_input({"trigger_id": "s1"}, "just a prompt") == "just a prompt"
# ── 3. the model actually sees it ─────────────────────────────────────

def test_an_agent_sender_is_marked_in_the_rendered_tag():
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam",
        sender_id="@agent-x:h", room_id="!room", is_agent_peer=True,
    )
    assert AGENT_PEER_MARKER in tag.format()


def test_a_human_tag_is_byte_identical_to_before():
    """These strings land in chat history; changing the shape for every
    turn would make old and new turns disagree."""
    tag = ChannelTag(
        channel="lark", sender_name="Alice", sender_id="ou_1", room_id="oc_1",
    )
    assert tag.format() == "[Lark · Alice · ou_1 · oc_1]"


def test_the_marker_survives_a_round_trip():
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam",
        sender_id="@agent-x:h", room_id="!room", is_agent_peer=True,
    )
    back = ChannelTag.parse(tag.format())
    assert back is not None
    assert back.is_agent_peer is True
    assert back.room_id == "!room"


def test_a_room_less_agent_tag_does_not_parse_the_marker_as_a_room():
    tag = ChannelTag(
        channel="wechat", sender_name="Bot", sender_id="wx1", is_agent_peer=True,
    )
    back = ChannelTag.parse(tag.format())
    assert back is not None
    assert back.room_id == "", "the marker must not be read as a room id"
    assert back.is_agent_peer is True


def test_the_flag_is_dropped_from_the_wire_when_false():
    """``to_dict`` strips falsy fields, so existing serialised tags are
    unchanged by this PR."""
    assert "is_agent_peer" not in ChannelTag(
        channel="lark", sender_name="A", sender_id="ou_1"
    ).to_dict()
    assert ChannelTag(
        channel="lark", sender_name="A", sender_id="ou_1", is_agent_peer=True
    ).to_dict()["is_agent_peer"] is True


def test_the_prompt_clause_names_the_marker():
    """The protocol text and the marker must agree — a clause naming a
    marker the tag never renders is a branch the model cannot take."""
    from narranexus.platform.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
    )

    assert AGENT_PEER_MARKER in COMMUNICATION_PROTOCOL_DIRECT
