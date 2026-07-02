"""
@file_name: test_output_transfer_codex_official.py
@date: 2026-06-04
@description: Tests for the ``codex_official`` transfer_type branch
in output_transfer.py.

Each test takes one official-SDK Notification dict (in the shape
``{"method": "...", "payload": {...}}``) and asserts the
post-translation event(s) match the OpenAI-Agents-SDK shape that
``response_processor.ResponseProcessor.process`` consumes.

The CodexSDKv2 path emits richer events than the v1 ``codex_cli``
branch — most notably ``ReasoningSummaryTextDeltaNotification`` streams
the Thinking-panel text. The flagship test here is
``test_reasoning_summary_delta_emits_thinking_delta``.

Method names below are the **canonical** strings the SDK emits, pinned
from ``openai_codex.generated.notification_registry.NOTIFICATION_MODELS``
(SDK 0.1.0b3). The initial v2 commit had every "item/*" method
mistakenly written as "turn/*" — silently dropping every notification
in the wild. The contract test
``test_method_constants_match_sdk_notification_registry`` (in
test_codex_sdk_v2_init.py) cross-checks our constants against the
live SDK registry on every CI run.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.output_transfer import output_transfer


def _t(message: dict) -> list[dict]:
    return output_transfer(message, transfer_type="codex_official")


# ---------------- Info-only / drop ---------------------------------


def test_thread_started_dropped():
    assert _t({"method": "thread/started", "payload": {"thread_id": "abc"}}) == []


def test_turn_started_dropped():
    assert _t({"method": "turn/started", "payload": {"turn_id": "t1"}}) == []


def test_unknown_method_dropped():
    """Forward-compat: novel notifications from future SDK releases
    should not raise, just be dropped quietly with a DEBUG log."""
    assert _t({"method": "future/unknown_thing", "payload": {"x": 1}}) == []


def test_non_dict_input_returns_empty():
    """Defensive: a stringified line that parses as a non-dict
    (e.g. ``"hello"`` or ``[]``) should not crash."""
    assert output_transfer("a-string", transfer_type="codex_official") == []
    assert output_transfer([], transfer_type="codex_official") == []


# ---------------- Streaming deltas (the v2 UX win) ------------------


def test_agent_message_delta_emits_text_delta():
    """Text token streaming — fills the assistant bubble char-by-char."""
    evs = _t({
        "method": "item/agentMessage/delta",
        "payload": {"delta": "hello world"},
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hello world"


def test_empty_agent_message_delta_dropped():
    """Empty delta = no-op event — don't pollute the stream."""
    evs = _t({
        "method": "item/agentMessage/delta",
        "payload": {"delta": ""},
    })
    assert evs == []


def test_reasoning_summary_delta_emits_thinking_item():
    """*** FLAGSHIP TEST ***

    This is the UX win v2 brings over v1: reasoning summary streams
    progressively into the Thinking panel rather than landing as one
    big block.

    Each delta becomes a ``thinking_item`` (NOT ``thinking_delta`` —
    the latter is a type response_processor does not recognise;
    initial v2 commit emitted it and the panel stayed empty even with
    296 reasoning deltas per turn). The ``_ThinkingBatcher`` inside
    response_processor coalesces consecutive thinking_item chunks
    into ~100 ms WebSocket frames, so emitting one per delta IS the
    streaming model.
    """
    evs = _t({
        "method": "item/reasoning/summaryTextDelta",
        "payload": {
            "delta": "The user wants me to ",
            "item_id": "rs_1",
            "summary_index": 0,
        },
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "run_item_stream_event"
    assert evs[0]["item"]["type"] == "thinking_item"
    assert evs[0]["item"]["content"] == "The user wants me to "


def test_reasoning_text_delta_emits_thinking_item():
    """Reasoning text delta (the model's "raw" reasoning channel,
    when the SDK exposes it). Same thinking_item shape as summary
    deltas — both feed the same batcher."""
    evs = _t({
        "method": "item/reasoning/textDelta",
        "payload": {"delta": "step 1: ", "item_id": "r_1"},
    })
    assert len(evs) == 1
    assert evs[0]["item"]["type"] == "thinking_item"
    assert evs[0]["item"]["content"] == "step 1: "


def test_reasoning_summary_part_added_emits_thinking_item_with_newlines():
    """A new ``part`` (section header) added to the reasoning summary
    is rendered as a thinking_item with surrounding newlines so the
    batcher's coalesced output keeps section boundaries visible."""
    evs = _t({
        "method": "item/reasoning/summaryPartAdded",
        "payload": {"text": "Calculating step by step", "item_id": "rs_1"},
    })
    assert len(evs) == 1
    assert evs[0]["item"]["type"] == "thinking_item"
    # Wrap in newlines so the UI naturally separates sections.
    assert "\nCalculating step by step\n" in evs[0]["item"]["content"]


# ---------------- Turn lifecycle ------------------------------------


def test_turn_completed_emits_response_done_with_usage():
    """Healthy completion — turn.status="completed", emit response.done
    with token usage from turn.usage (preferred) or turn.token_usage
    (older field name, still tolerated)."""
    evs = _t({
        "method": "turn/completed",
        "payload": {
            "turn": {
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_input_tokens": 25,
                },
            },
        },
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.done"
    usage = evs[0]["data"]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["cached_input_tokens"] == 25


def test_turn_completed_with_token_usage_alias():
    """Tolerate ``token_usage`` field name (Turn payload has both
    ``usage`` and ``token_usage`` shape in older SDK builds — we
    accept either)."""
    evs = _t({
        "method": "turn/completed",
        "payload": {
            "turn": {
                "status": "completed",
                "token_usage": {"input_tokens": 7, "output_tokens": 3},
            },
        },
    })
    usage = evs[0]["data"]["usage"]
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3


def test_turn_completed_missing_usage_defaults_to_zero():
    evs = _t({
        "method": "turn/completed",
        "payload": {"turn": {"status": "completed"}},
    })
    assert evs[0]["data"]["usage"]["input_tokens"] == 0
    assert evs[0]["data"]["usage"]["output_tokens"] == 0


def test_turn_completed_with_failed_status_emits_response_error():
    """Failed turns surface via turn/completed with status=='failed' —
    there is NO separate turn/failed notification. Initial draft of
    this translator listened for 'turn/failed', which the SDK never
    emits; failures would have silently looked like clean
    completions."""
    evs = _t({
        "method": "turn/completed",
        "payload": {
            "turn": {
                "status": "failed",
                "error": {"message": "rate_limited", "type": "rate_limit_error"},
            },
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_message"] == "rate_limited"
    assert evs[0]["data"]["error_type"] == "rate_limit_error"


def test_top_level_error_emits_response_error():
    """ErrorNotification.error is a TurnError with .message — NOT a
    flat ``payload.message`` (which the initial translator wrongly
    read, ending in 'unknown error' for every real error)."""
    evs = _t({
        "method": "error",
        "payload": {
            "error": {
                "message": "connection refused",
                "codex_error_info": {"type": "transport_closed"},
            },
            "thread_id": "t",
            "turn_id": "tu",
            "will_retry": False,
        },
    })
    assert evs[0]["data"]["type"] == "response.error"
    assert "connection refused" in evs[0]["data"]["error_message"]
    assert evs[0]["data"]["error_type"] == "transport_closed"


def test_top_level_error_with_string_codex_error_info_does_not_crash():
    """REGRESSION (incident 2026-06-11): the real codex ``error``
    notification carries ``codex_error_info`` as a **string**, not a
    dict. The translator did ``info.get("type")`` after a bare
    ``or {}`` guard (which only catches None/falsy), so a non-empty
    string raised ``AttributeError: 'str' object has no attribute
    'get'``. That exception propagated out of CodexSDKv2.agent_loop,
    killed the codex loop ([AGENT-LOOP-FATAL]), and forced the
    helper-LLM no_reply fallback on EVERY codex turn — also leaving the
    Thinking panel empty because reasoning was never translated. Guard
    by type-checking ``info`` before calling ``.get``."""
    evs = _t({
        "method": "error",
        "payload": {
            "error": {
                "message": "stream disconnected before completion",
                "codex_error_info": "stream_error",  # <-- STRING, not dict
            },
            "thread_id": "t",
            "turn_id": "tu",
            "will_retry": False,
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert "stream disconnected" in evs[0]["data"]["error_message"]
    # The bare-string codex_error_info IS the category — preserve it as
    # error_type rather than discarding it to a generic "error".
    assert evs[0]["data"]["error_type"] == "stream_error"


def test_top_level_error_with_string_error_object_does_not_crash():
    """Defensive: if ``payload.error`` itself is a bare string (some
    transport-level failures), use it as the message and don't crash
    on the missing dict shape."""
    evs = _t({
        "method": "error",
        "payload": {"error": "raw transport failure"},
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_message"] == "raw transport failure"
    assert evs[0]["data"]["error_type"] == "error"


def test_top_level_error_unauthorized_surfaces_category_as_error_type():
    """REAL incident payload (2026-06-11): an expired/used codex OAuth
    token produces ``codex_error_info: "unauthorized"`` (a string). The
    category must surface as error_type so the runtime can prompt the
    user to re-login instead of silently falling back to the helper."""
    evs = _t({
        "method": "error",
        "payload": {
            "error": {
                "additional_details": None,
                "codex_error_info": "unauthorized",
                "message": (
                    "Your access token could not be refreshed because your "
                    "refresh token was already used. Please log out and sign "
                    "in again."
                ),
            },
            "thread_id": "t",
            "turn_id": "tu",
            "will_retry": False,
        },
    })
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_type"] == "unauthorized"
    assert "sign in again" in evs[0]["data"]["error_message"]


def test_transient_retrying_error_is_dropped():
    """codex emits ``error`` notifications with ``will_retry: True`` while
    it reconnects a dropped stream (message like "Reconnecting... 2/5").
    These are internal retry chatter, NOT a final outcome — surfacing them
    spammed the chat with bogus error bubbles (incident 2026-06-11). Drop
    them; the real result still arrives via a non-retrying error or
    turn/completed(status=failed)."""
    evs = _t({
        "method": "error",
        "payload": {
            "error": {
                "additional_details": "stream disconnected: tls handshake eof",
                "codex_error_info": {"response_stream_disconnected": {"http_status_code": None}},
                "message": "Reconnecting... 3/5",
            },
            "thread_id": "t",
            "turn_id": "tu",
            "will_retry": True,
        },
    })
    assert evs == []


def test_non_retrying_error_still_surfaces():
    """A non-retrying error (will_retry False/absent) is the final outcome
    — it must still surface."""
    evs = _t({
        "method": "error",
        "payload": {
            "error": {"message": "boom", "codex_error_info": "internal_error"},
            "will_retry": False,
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_type"] == "internal_error"


def test_turn_completed_failed_unauthorized_surfaces_category():
    """The same unauthorized error also arrives via turn/completed with
    status=='failed' (notification #5 in the incident). Its error_type
    must likewise be the 'unauthorized' category, not 'turn.failed'."""
    evs = _t({
        "method": "turn/completed",
        "payload": {
            "turn": {
                "status": "failed",
                "error": {
                    "additional_details": None,
                    "codex_error_info": "unauthorized",
                    "message": "Please log out and sign in again.",
                },
            },
        },
    })
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_type"] == "unauthorized"


# ---------------- Item lifecycle (delegates to v1 helpers) ----------


def test_item_completed_agent_message_delegates_to_v1_helper():
    """``item.completed`` notifications wrap a ThreadItem; v2's
    translator reshapes into the v1 codex_cli event shape and
    delegates so we don't duplicate item-type branching."""
    evs = _t({
        "method": "item/completed",
        "payload": {
            "item": {
                "type": "agent_message",
                "id": "i1",
                "text": "hello",
            },
        },
    })
    # v1's translator emits a raw_response_event for agent_message.
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hello"


def test_item_completed_camelcase_agent_message_normalized_to_text_delta():
    """SDK v2 emits item.type as ``agentMessage`` (camelCase); the
    boundary normalizer rewrites it to ``agent_message`` so the v1
    helper's frozenset lookup hits. Initial v2 commit shipped without
    this normalizer — every agent_message item silently dropped and
    the no_reply fallback fired every turn. THIS test locks in the
    fix."""
    evs = _t({
        "method": "item/completed",
        "payload": {
            "item": {
                "type": "agentMessage",  # <-- camelCase from SDK
                "id": "i1",
                "text": "hi from the agent",
            },
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hi from the agent"


def test_item_started_websearch_hoists_query_from_action():
    """SDK 0.1.0b3 emits ``WebSearchThreadItem`` at item/started with
    ``query=""`` — the actual search string lives inside
    ``action.root.query`` (the Responses-API action object). Without
    hoisting, the rendered tool_call_item shows ``{"query": ""}`` for
    every WebSearch call. Confirmed incident 2026-06-08."""
    evs = _t({
        "method": "item/started",
        "payload": {
            "item": {
                "type": "webSearch",
                "id": "ws_1",
                "query": "",  # SDK leaves this empty at start
                "action": {
                    "root": {
                        "type": "search",
                        "query": "latest news Asia",
                        "queries": None,
                    },
                },
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_item"
    assert item["tool_name"] == "WebSearch"
    assert item["arguments"]["query"] == "latest news Asia"


def test_item_started_websearch_hoists_queries_list():
    """If ``action.root.query`` is null but ``queries`` is a list,
    join into a single comma-separated string so the UI still has
    something readable. (Some Responses-API variants emit
    ``queries: ["a", "b"]`` for multi-query searches.)"""
    evs = _t({
        "method": "item/started",
        "payload": {
            "item": {
                "type": "webSearch",
                "id": "ws_2",
                "query": "",
                "action": {
                    "root": {
                        "type": "search",
                        "query": None,
                        "queries": ["china hot news", "asia headlines"],
                    },
                },
            },
        },
    })
    assert evs[0]["item"]["arguments"]["query"] == "china hot news, asia headlines"


def test_item_completed_camelcase_mcp_tool_call_normalized():
    """Same normalization for ``mcpToolCall`` — needs to surface as a
    tool_call_output_item so the no_reply detector matches
    send_message_to_user_directly."""
    evs = _t({
        "method": "item/completed",
        "payload": {
            "item": {
                "type": "mcpToolCall",  # <-- camelCase from SDK
                "id": "m1",
                "server": "chat_module",
                "tool": "send_message_to_user_directly",
                "result": {"success": True},
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_output_item"
    assert item["tool_name"] == "mcp__chat_module__send_message_to_user_directly"


def test_item_started_mcp_tool_call_emits_tool_call_item():
    evs = _t({
        "method": "item/started",
        "payload": {
            "item": {
                "type": "mcp_tool_call",
                "id": "m1",
                "server": "lark_module",
                "tool": "lark_status",
                "arguments": {"agent_id": "x"},
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_item"
    assert item["tool_name"] == "mcp__lark_module__lark_status"
    assert item["arguments"] == {"agent_id": "x"}


def test_item_completed_mcp_tool_call_emits_tool_output():
    evs = _t({
        "method": "item/completed",
        "payload": {
            "item": {
                "type": "mcp_tool_call",
                "id": "m1",
                "server": "lark_module",
                "tool": "lark_status",
                "result": {"success": True, "bound": False},
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_output_item"
    assert item["tool_name"] == "mcp__lark_module__lark_status"
    assert "success" in item["output"]


def test_item_completed_unwraps_root_model():
    """ThreadItem is a pydantic RootModel — sometimes serializes with
    a ``"root"`` wrapper. The translator must unwrap it transparently."""
    evs = _t({
        "method": "item/completed",
        "payload": {
            "item": {
                "root": {
                    "type": "agent_message",
                    "id": "i1",
                    "text": "hi",
                },
            },
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["delta"] == "hi"


# ---------------- Drops that should stay drops (until UI catches up)


def test_command_execution_output_delta_dropped():
    """Bash output streaming notifications are dropped intentionally
    — the frontend doesn't render mid-command output yet; the final
    aggregated output still lands on item.completed."""
    evs = _t({
        "method": "item/commandExecution/outputDelta",
        "payload": {"delta": "some output"},
    })
    assert evs == []


def test_mcp_tool_progress_dropped():
    """Same reason as command output — drop until frontend supports it."""
    evs = _t({
        "method": "item/mcpToolCall/progress",
        "payload": {"progress": 0.5},
    })
    assert evs == []
