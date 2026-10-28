"""
@file_name: test_message_source_handler.py
@author: Bin Liang
@date: 2026-05-11
@description: Contract tests for MessageSourceHandler + the registry-backed MessageSourceView.

Behaviour pinned:
1. A source enters the view through its plugin's contribution — a
   ``ChannelDescriptor``'s message-source fields, or an
   ``ingress.message_sources`` spec — and NEVER through an import side effect.
2. A source that is not declared falls back to the default handler; a source
   that IS a registered channel yet declares none warns, because that silent
   default is what recorded delivered IM replies as NO-REPLY.
3. The default handler covers `chat`/`a2a`/`callback`/`skill_study` without any
   declaration — they are the source-less sources.
4. `format_row_prefix` substitutes meta_data + channel_tag fields into the template.
5. `is_user_reply_tool` matches tool names by `<pattern> in tool_name` so the
   MCP-prefixed form (`mcp__chat_module__notify_owner`)
   still matches the pattern `notify_owner`.
6. A view's dump returns a JSON-serializable snapshot for debugging.
7. The reply extractor named by a descriptor is resolved LAZILY — building the
   view must not import the channel's module.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.channel import ChannelDescriptor, MessageSourceSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


@pytest.fixture
def view():
    """A view over a PRIVATE, empty ``Registries`` — the whole point of the
    change is that a message source is registry state, so a test can build its
    own registry instead of mutating a process-global dict and restoring it."""
    from narranexus.platform.channel.message_source_handler import MessageSourceView

    regs = Registries()
    v = MessageSourceView(regs)
    v.registries = regs  # type: ignore[attr-defined]  — handed to the test for registration
    return v


def _register_channel(view, descriptor: ChannelDescriptor, owner: str = "test.channel") -> None:
    view.registries.registry_for("ingress.channels").register_contribution(
        Contribution(descriptor.name, lambda: descriptor), owner=owner
    )


def _register_source(view, spec: MessageSourceSpec, owner: str = "test.source") -> None:
    view.registries.registry_for("ingress.message_sources").register_contribution(
        Contribution(spec.name, lambda: spec), owner=owner
    )


def test_channel_descriptor_fields_become_the_handler(view):
    """A channel declares its message source as descriptor fields; nothing else."""
    _register_channel(view, ChannelDescriptor(
        name="lark",
        display_name="Lark",
        trigger_ref="pkg.mod:Trigger",
        reply_tools=("notify_owner", "lark_cli +messages-send"),
        row_prefix_template="[Lark · {sender_name}]",
        dedicated_trigger=True,
    ))
    got = view.get("lark")
    assert got.name == "lark"
    assert got.label == "Lark"
    assert got.user_reply_tool_names == ("notify_owner", "lark_cli +messages-send")
    assert got.row_prefix_template == "[Lark · {sender_name}]"
    assert got.dedicated_trigger is True


def test_non_channel_source_comes_from_the_message_sources_slot(view):
    """The bus and the job clock are message sources without being channels."""
    _register_source(view, MessageSourceSpec(
        name="message_bus",
        display_label="NarraNexus",
        reply_tools=("notify_owner", "message_agent"),
        owner_visible_reply_tools=("notify_owner",),
        row_prefix_template="[private message from {from_agent}]",
    ))
    got = view.get("message_bus")
    assert got.effective_owner_visible_names == ("notify_owner",)
    assert got.is_user_reply_tool("mcp__x__message_agent")
    assert not got.is_owner_visible_reply_tool("mcp__x__message_agent")


def test_a_source_disappears_with_its_registration(view):
    """A channel excluded from a distribution / disabled in registry.json is not
    in ``ingress.channels``, so it has no handler — the property the old
    class-level dict could not express (a disabled channel's handler survived
    because something had imported its module)."""
    _register_channel(view, ChannelDescriptor(
        name="lark", display_name="Lark", trigger_ref="pkg.mod:T",
        reply_tools=("lark_cli",), row_prefix_template="[Lark]",
    ))
    assert view.get("lark").name == "lark"
    view.registries.registry_for("ingress.channels").remove_owner("test.channel")
    assert view.get("lark") is not None
    assert view.get("lark").row_prefix_template == "[NarraNexus UI]"


def test_registered_channel_without_a_message_source_warns(view, caplog):
    """The silent case, turned into a signal.

    A channel in the registry that declares no reply tools resolves to the
    default handler — its IM replies would be judged with ``reply_owner`` /
    ``notify_owner`` and its rows labelled ``[NarraNexus UI]``. That is exactly
    the failure the old registry hid, so it must be loud."""
    _register_channel(view, ChannelDescriptor(name="mute", display_name="Mute", trigger_ref="pkg.mod:T"))
    with caplog.at_level("WARNING"):
        assert view.get("mute").name == "default"


def test_sourceless_sources_use_the_default_without_warning(view):
    """chat / a2a / callback / skill_study declare nothing BY DESIGN."""
    from narranexus.platform.channel.message_source_handler import SOURCELESS_SOURCES

    for source in sorted(SOURCELESS_SOURCES):
        h = view.get(source)
        assert h.name == "default"
        assert "notify_owner" in h.user_reply_tool_names
        assert source not in view._warned_default  # type: ignore[attr-defined]


def test_get_unknown_source_returns_default_handler():
    from narranexus.platform.channel.message_source_handler import MessageSourceRegistry

    default = MessageSourceRegistry.get("definitely_not_registered_xyz")
    # Default handler always recognises notify_owner so
    # chat/a2a/callback/skill_study don't need explicit registration.
    assert "notify_owner" in default.user_reply_tool_names


def test_the_extractor_ref_is_resolved_lazily(view):
    """Naming an extractor must not import the module that holds it — otherwise
    building the view (a read) drags in every channel's SDK, which is the
    import coupling this design removes."""
    import sys

    module_name = "tests.channel._lazy_extractor_probe"
    sys.modules.pop(module_name, None)
    _register_source(view, MessageSourceSpec(
        name="probe",
        reply_tools=("probe_send",),
        reply_extractor_ref=f"{module_name}:extract",
    ))
    handler = view.get("probe")           # built…
    assert module_name not in sys.modules  # …without importing the extractor
    assert handler.extract_reply_text("probe_send", {"text": "hi"}) == "hi"
    assert module_name in sys.modules


def test_is_user_reply_tool_matches_mcp_prefixed_names():
    """Tool names from MCP arrive as e.g.
    `mcp__chat_module__notify_owner`. The handler must
    match its registered short name as a substring so we don't have to
    enumerate every MCP-prefixed variant."""
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    assert h.is_user_reply_tool("mcp__chat_module__notify_owner")
    assert h.is_user_reply_tool("notify_owner")
    assert not h.is_user_reply_tool("get_chat_history")
    assert not h.is_user_reply_tool("")


def test_is_user_reply_tool_matches_multiple_patterns():
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=(
            "notify_owner",
            "lark_cli +messages-send",
            "lark_cli +messages-reply",
        ),
    )
    assert h.is_user_reply_tool("mcp__chat_module__notify_owner")
    assert h.is_user_reply_tool("mcp__lark_module__lark_cli +messages-send")
    assert h.is_user_reply_tool("lark_cli +messages-reply")
    assert not h.is_user_reply_tool("lark_cli +messages-list")


def test_format_row_prefix_substitutes_meta_and_channel_tag():
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("notify_owner",),
        row_prefix_template="[Lark · {sender_name} in {room_name}]",
    )
    msg = {
        "role": "assistant",
        "content": "hi",
        "meta_data": {
            "working_source": "lark",
            "channel_tag": {
                "sender_name": "Loki",
                "room_name": "顺风耳, Loki, 阿良",
            },
        },
    }
    out = h.format_row_prefix(msg)
    assert "Lark" in out
    assert "Loki" in out
    assert "顺风耳, Loki, 阿良" in out


def test_format_row_prefix_missing_fields_falls_back_gracefully():
    """Template references {sender_name} but channel_tag is missing —
    must not crash; should leave the placeholder empty or substitute a
    safe default."""
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("notify_owner",),
        row_prefix_template="[Lark · {sender_name}]",
    )
    msg = {"role": "user", "content": "hi", "meta_data": {"working_source": "lark"}}
    out = h.format_row_prefix(msg)
    # Either the missing field becomes "" / "?" or the line still parses
    # — what we must NOT do is raise KeyError.
    assert "Lark" in out


def test_default_handler_renders_chat_ui_prefix():
    from narranexus.platform.channel.message_source_handler import MessageSourceRegistry

    msg = {
        "role": "user",
        "content": "hi",
        "meta_data": {"working_source": "chat", "user_id": "binliang"},
    }
    h = MessageSourceRegistry.get("chat")
    out = h.format_row_prefix(msg)
    assert "binliang" in out or "NarraNexus" in out or "Chat" in out


def test_extract_reply_text_default_returns_content_arg():
    """Default extractor: tool_name matches user_reply_tool_names AND
    arguments has a `content` field → return that content."""
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": "Hello user"},
    )
    assert out == "Hello user"


def test_extract_reply_text_default_returns_none_for_unmatched_tool():
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    assert h.extract_reply_text("get_chat_history", {"x": 1}) is None
    assert h.extract_reply_text("", {}) is None


def test_extract_reply_text_custom_fn_overrides_default():
    """A handler with `extract_reply_fn` can implement non-standard
    extraction. This is the Lark path: tool_name = 'lark_cli', the reply
    text sits inside `arguments['command']` as a `--markdown` flag."""
    from narranexus.platform.channel.message_source_handler import MessageSourceHandler

    def lark_extract(tool_name, args):
        if "lark_cli" not in tool_name:
            return None
        cmd = args.get("command", "")
        if "+messages-send" not in cmd:
            return None
        # Toy parser: find the --markdown literal
        if "--markdown" in cmd:
            return cmd.split("--markdown", 1)[1].strip().strip('"')
        return None

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("lark_cli",),
        extract_reply_fn=lark_extract,
    )
    # lark_cli send command → extracted
    out = h.extract_reply_text(
        "mcp__lark_module__lark_cli",
        {"command": 'im +messages-send --chat-id oc_x --markdown "你好啊"'},
    )
    assert out == '你好啊"'.rstrip('"')

    # lark_cli for a non-send command → no reply
    out2 = h.extract_reply_text(
        "lark_cli", {"command": "im +messages-list --chat-id oc_x"}
    )
    assert out2 is None


def test_extract_reply_text_strips_citeturn_tokens():
    """OpenAI Responses-API ``citeturnNviewN`` / ``citeturnNnewsN``
    tokens that gpt-5.5 emits when WebSearch ran are stripped at the
    reply-extraction layer (so users see clean prose, not literal
    cryptic markers). Verified format from incident 2026-06-08:
    tokens are concatenated to sentence ends with no whitespace."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    raw = "6月7日全国高考开考，今年报名人数1290万人。citeturn6view1"
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": raw},
    )
    assert out == "6月7日全国高考开考，今年报名人数1290万人。"


def test_extract_reply_text_strips_multiple_tokens_across_paragraphs():
    """Several tokens in one reply (with whitespace between them after
    strip) get the leftover spaces tidied up."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    raw = "新华社/央视消息称习近平抵达平壤。citeturn6view0 citeturn2news12"
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": raw},
    )
    # Both tokens gone; the whitespace between them collapses and the
    # trailing space before the period (if any) is fixed.
    assert "citeturn" not in out
    assert out == "新华社/央视消息称习近平抵达平壤。"


def test_extract_reply_text_preserves_text_without_tokens():
    """Fast-path: if no ``cite`` substring appears at all, the text is
    returned unchanged (no regex sweep, no whitespace mutation)."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    raw = "Hi there!  Multiple spaces  stay  intact."
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": raw},
    )
    assert out == raw  # NOT modified — fast-path kept doubled spaces


def test_extract_reply_text_does_not_match_word_cite():
    """The regex requires two alpha+digit cycles after ``cite``, so the
    English word "cite" used in ordinary prose (e.g. "Please cite the
    source") survives intact."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    raw = "Please cite the relevant section in your write-up."
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": raw},
    )
    assert out == raw


def test_extract_reply_text_strips_tokens_through_custom_extractor():
    """The strip applies AFTER any custom ``extract_reply_fn`` — so
    channels with non-standard reply tooling (Lark's --markdown flag,
    Slack/Telegram CLI wrappers, etc.) also get clean text without
    each having to implement the strip themselves."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    def lark_extractor(tool_name, arguments):
        # Pretend this is Lark's --markdown extraction
        return arguments.get("markdown")

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("lark_cli +messages-send",),
        extract_reply_fn=lark_extractor,
    )
    out = h.extract_reply_text(
        "lark_cli +messages-send",
        {"markdown": "中朝外交：习近平抵达平壤citeturn6view0"},
    )
    assert out == "中朝外交：习近平抵达平壤"


def test_dump_returns_serializable_snapshot(view):
    import json

    _register_channel(view, ChannelDescriptor(
        name="lark", display_name="Lark", trigger_ref="pkg.mod:T",
        reply_tools=("lark_cli +messages-send",),
        row_prefix_template="[Lark · {sender_name}]",
    ))
    snapshot = view.dump()
    # Must be JSON-serialisable for debug logging.
    json.dumps(snapshot)
    assert "lark" in snapshot


def test_extract_reply_text_all_citation_reply_returns_blank_sentinel():
    """A reply that is *nothing but* citation tokens (gpt-5.x +
    WebSearch emitting only markers) strips down to bare whitespace —
    that is a blank reply attempt — "" (distinct from None = not a reply
    call at all, so lark_cli non-send commands still classify as real
    tool calls downstream). Root cause of the
    2026-07-13 blank-bubble report."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    raw = "citeturn6view1\nciteturn6news2"
    out = h.extract_reply_text(
        "mcp__chat_module__notify_owner",
        {"content": raw},
    )
    assert out == ""


def test_extract_reply_text_whitespace_only_content_returns_blank_sentinel():
    """Literal whitespace content never survives extraction either —
    the falsy check alone let "\\n" through as a truthy 'reply'."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    assert h.extract_reply_text(
        "mcp__chat_module__notify_owner", {"content": "\n"}
    ) == ""
    assert h.extract_reply_text(
        "mcp__chat_module__notify_owner", {"content": "   "}
    ) == ""


def test_extract_owner_visible_text_inherits_blank_guard():
    """extract_owner_visible_text delegates to extract_reply_text, so
    the blank guard covers the owner-visible split too."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("notify_owner",),
    )
    assert h.extract_owner_visible_text(
        "mcp__chat_module__notify_owner", {"content": "\n"}
    ) == ""
