"""
@file_name: test_modeling.py
@author: Bin Liang
@date: 2026-07-29
@description: Modeling group: profile resolution, cache planning, chunk
translation (fake litellm stream), usage normalization, compaction.
"""

import json

import pytest

from xyz_agent_context.agent_framework.nexus_power.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    CachePlan,
    ModelParams,
    ModelRequest,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import ToolResult
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.compaction import (
    ToolResultPruner,
    estimate_message_tokens,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.model_client import (
    LiteLLMModelClient,
    _extract_usage,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.profiles import (
    output_budget,
    resolve_profile,
)
from xyz_agent_context.agent_framework.providers.model_catalog import (
    _KNOWN_MODELS,
    get_context_window,
    get_max_output_tokens,
    get_model_meta,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.prompt_cache import (
    plan_cache,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
    TurnLedger,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import ModelEvent


def test_profile_resolution():
    assert resolve_profile("claude-sonnet-4", "anthropic").name == "anthropic"
    assert resolve_profile("deepseek-chat", None).name == "deepseek"
    assert resolve_profile("claude-opus-x", None).name == "anthropic"
    assert resolve_profile("totally-unknown", None).name == "default"


def test_output_ceilings_come_from_the_catalog():
    """115_200 is the catalog's standing "90% of the model limit" margin
    on Claude's 128K, not a number this module chose.

    An under-set ceiling is not a safe default: it truncates tool
    arguments mid-JSON, which the model cannot see and cannot recover
    from (2026-07-30 incident, against an 8_192 ceiling)."""
    assert resolve_profile("claude-opus-4-8", "anthropic").max_output_tokens == 115_200
    assert resolve_profile("claude-sonnet-4-6", "anthropic").max_output_tokens == 115_200
    # The platform id NetMind actually dispatches on resolves too.
    assert resolve_profile("anthropic/claude-opus-4-8", "anthropic").max_output_tokens == 115_200


def test_haiku_keeps_its_own_lower_ceiling():
    """Haiku's real limit is 64K against 128K for the rest of the line,
    so it lands at 57_600 where they land at 115_200. Measured on the dev
    gateway 2026-07-31: haiku@64000 -> 200, haiku@128000 -> 400."""
    profile = resolve_profile("claude-haiku-4-5", "anthropic")
    assert profile.max_output_tokens == 57_600
    # Same dialect as the rest of the line — only the ceiling differs.
    assert profile.cache_style == "breakpoints"
    assert profile.supports_arg_delta is True


@pytest.mark.parametrize(
    "model",
    [
        "Qwen/Qwen2.5-7B-Instruct",
        "deepseek-ai/DeepSeek-V4-Pro",
        "XiaomiMiMo/MiMo-V2.5",
        "some-model-we-have-never-measured",
    ],
)
def test_the_anthropic_PROTOCOL_never_grants_a_vendor_ceiling(model):
    """``provider`` is the PROTOCOL ("anthropic"/"openai"), not the vendor
    — nexus_agent passes the resolved protocol straight through. The
    NetMind free-tier card speaks the anthropic protocol while serving
    Qwen, DeepSeek and MiMo, so keying the ceiling off the provider would
    hand a 7B model a 128K output request and 400 every call.

    Dialect still comes from the protocol (that part IS protocol-shaped);
    only the ceiling is a fact about the model."""
    profile = resolve_profile(model, "anthropic")
    assert profile.max_output_tokens == 8_192  # conservative default
    assert profile.cache_style == "breakpoints"  # protocol dialect intact


def test_ceiling_follows_the_model_even_with_no_provider():
    assert resolve_profile("anthropic/claude-opus-4-8", None).max_output_tokens == 115_200
    assert resolve_profile("claude-haiku-4-5", None).max_output_tokens == 57_600


def test_output_budget_leaves_room_for_the_input():
    """Anthropic rejects ``input + max_tokens > context_window``. Measured
    2026-07-31 on the dev gateway: opus-4-8 took 144_065 input alongside
    max_tokens=128_000 (so its real window is far past 200K), but Haiku's
    window really is 200K, and our own compaction only trips at 150K —
    leaving a band where an unclamped 64K request would exceed it."""
    haiku = resolve_profile("claude-haiku-4-5", "anthropic")
    assert output_budget(haiku, 0) == 57_600            # nothing to give back
    assert output_budget(haiku, 150_000) < 57_600       # the band that would 400
    assert output_budget(haiku, 150_000) + 150_000 <= haiku.vendor_context_window

    opus = resolve_profile("claude-opus-4-8", "anthropic")
    assert output_budget(opus, 144_065) == 115_200      # 1M window: never binds


def test_output_budget_never_returns_a_useless_or_negative_ceiling():
    haiku = resolve_profile("claude-haiku-4-5", "anthropic")
    assert output_budget(haiku, 10_000_000) > 0


@pytest.mark.parametrize(
    "model",
    ["deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen2.5-7B-Instruct", "unmeasured-model"],
)
def test_the_clamp_never_undercuts_the_window_we_manage(model):
    """A model with no measured wall must not be clamped below its own
    ceiling anywhere inside the window this module manages.

    The wall and the managed budget are separate numbers precisely so
    the clamp can use the real one — but an unmeasured wall must fall
    back to the managed budget, not to a dataclass literal. Left
    diverging, the free tier's own default agent model was clamped from
    8_192 down to 1_024 at 130K input, which is the very truncation this
    change exists to remove."""
    profile = resolve_profile(model, "anthropic")
    for estimate in (0, 100_000, 130_000, profile.context_window - 20_000):
        assert output_budget(profile, estimate) == profile.max_output_tokens


def test_measured_models_still_clamp_against_their_real_wall():
    """The fallback must not blunt the clamp where a wall IS known."""
    haiku = resolve_profile("claude-haiku-4-5", "anthropic")
    assert output_budget(haiku, 180_000) < haiku.max_output_tokens


@pytest.mark.parametrize(
    "model,expected",
    [
        # Catalog knows a ceiling but NOT a window. Raising here would
        # pair a model-measured ceiling with the anthropic dialect row's
        # 200_000 — a PROTOCOL number, not this model's window. Both of
        # the first two sit in the default NetMind dropdown.
        ("zai-org/GLM-5.1", 8_192),
        ("minimax/minimax-m2.7", 8_192),
        ("moonshotai/Kimi-K2.5", 8_192),
        ("google/gemini-3.1-pro-preview", 8_192),
        ("google/gemini-3.1-flash-lite-preview", 8_192),
        ("zai-org/GLM-5", 8_192),
        # Lowering needs no window — a smaller ceiling cannot overrun a
        # wall, and 7_200 is this model's real limit.
        ("deepseek-ai/DeepSeek-V3", 7_200),
    ],
)
def test_a_ceiling_is_only_raised_when_the_wall_is_known_too(model, expected):
    profile = resolve_profile(model, "anthropic")
    assert profile.max_output_tokens == expected
    assert profile.vendor_context_window is None
    # And the clamp must not size against the protocol row's window.
    assert output_budget(profile, 130_000) <= expected


def test_one_catalog_entry_supplies_both_numbers():
    """Two independent lookups could fall back differently and pair one
    row's ceiling with another row's window. The meta is resolved once."""
    meta = get_model_meta("anthropic/claude-opus-4-8")
    profile = resolve_profile("anthropic/claude-opus-4-8", "anthropic")
    assert profile.max_output_tokens == meta.max_output_tokens
    assert profile.vendor_context_window == meta.context_window


def test_prefix_normalization_lives_in_the_catalog_so_all_callers_get_it():
    """It was written inside nexus_power first, which left the other two
    consumers unable to resolve a platform id — the per-caller
    duplication this catalog exists to prevent.

    The ids here must be ones ONLY the fallback can resolve: an
    aggregator prefix over a bare registered name. An earlier version of
    this test used ``anthropic/claude-opus-4-8``, which is itself
    registered, so it hit the exact-match path and the fallback could be
    deleted outright with every test still green."""
    for model, ceiling, window in (
        ("yunwu/claude-opus-4-8", 115_200, 1_000_000),
        ("openrouter/claude-haiku-4-5", 57_600, 200_000),
    ):
        assert model not in _KNOWN_MODELS  # only reachable via the fallback
        assert get_max_output_tokens(model) == ceiling
        assert get_context_window(model) == window

    # Exact match still wins where the prefixed id IS registered.
    assert "anthropic/claude-opus-4-8" in _KNOWN_MODELS
    assert get_max_output_tokens("anthropic/claude-opus-4-8") == 115_200


def test_limits_come_from_the_platform_catalog_not_a_local_copy():
    """One question, one answer. A private table here would be a second
    source of truth for the same number — and the first draft of it
    promptly disagreed with the catalog (128_000 vs 115_200).

    This also means every framework that goes through our own client
    shares the numbers: adapters/openai_agents and llm/anthropic_helper
    already read the same catalog."""
    for model in ("claude-opus-4-8", "claude-haiku-4-5", "anthropic/claude-opus-4-8"):
        profile = resolve_profile(model, "anthropic")
        assert profile.max_output_tokens == get_max_output_tokens(model)
        assert profile.vendor_context_window == get_context_window(model)


def test_input_estimate_counts_tool_call_arguments():
    """A tool-only assistant step carries its payload in ``tool_calls``
    and sets ``content`` to None (turn_ledger._fold_step_message). Sizing
    off ``content`` alone estimated a 16KB write_file at ONE token —
    and this estimate is the clamp's only signal on a turn's first step,
    where the ledger has no measurement yet."""
    big = "X" * 16_000
    messages = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "g.html", "content": big})},
        }],
    }]
    estimate = estimate_message_tokens(messages)
    assert estimate > 3_000  # same order as the arguments it carries
    # Over-counting is the safe direction for a clamp; under-counting is
    # what lets a request sail past the wall.
    assert estimate >= len(big) // 4


def test_cache_plan_breakpoints_only_for_breakpoint_dialects():
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "hi"},
    ]
    anthropic = resolve_profile("claude", "anthropic")
    plan = plan_cache(messages, anthropic)
    assert plan.breakpoint_indices == (1, 2)
    assert plan_cache(messages, resolve_profile("deepseek", None)) == CachePlan()


class _FakeLitellm:
    """Replays canned chunk dicts like LitellmClient would."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.last_kwargs = None

    async def stream_chat(self, **kwargs):
        self.last_kwargs = kwargs
        for chunk in self._chunks:
            yield chunk


def _chunk(delta=None, finish=None, usage=None):
    choice = {"delta": delta or {}, "finish_reason": finish}
    return {"choices": [choice] if (delta or finish) else [], "usage": usage}


@pytest.mark.asyncio
async def test_stream_translation_text_tool_usage():
    chunks = [
        _chunk({"content": "think"}),
        _chunk({"reasoning_content": "deep"}),
        _chunk({"tool_calls": [{"index": 0, "id": "c1",
                                "function": {"name": "bash"}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": '{"command":'}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": '"ls"}'}}]}),
        _chunk(finish="tool_calls"),
        _chunk(usage={"prompt_tokens": 90, "completion_tokens": 12,
                      "cache_read_input_tokens": 40}),
    ]
    client = LiteLLMModelClient(
        resolve_profile("claude", "anthropic"), _FakeLitellm(chunks)
    )
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url="https://api.x.com"),
    )
    events = [e async for e in client.stream_step(request)]
    kinds = [e.kind for e in events]
    assert kinds == [
        "text_delta", "thinking_delta", "tool_use_start",
        "arg_delta", "arg_delta", "tool_use", "done",
    ]
    tool_use = events[kinds.index("tool_use")]
    assert tool_use.payload["args"] == {"command": "ls"}
    assert tool_use.payload["call_id"] == "c1"
    done = events[-1]
    assert done.payload["usage"] == Usage(
        input_tokens=90, output_tokens=12, cache_read_tokens=40
    )
    # Anthropic-protocol routing for custom endpoints.
    fake = client._client
    assert fake.last_kwargs["model"] == "anthropic/claude-x"


@pytest.mark.asyncio
async def test_cache_control_injected_at_breakpoints():
    fake = _FakeLitellm([_chunk(finish="stop")])
    client = LiteLLMModelClient(resolve_profile("claude", "anthropic"), fake)
    request = ModelRequest(
        messages=[{"role": "system", "content": "S"},
                  {"role": "user", "content": "U"}],
        tools=[],
        params=ModelParams(model="claude-x"),
        cache_plan=CachePlan(breakpoint_indices=(0,)),
    )
    _ = [e async for e in client.stream_step(request)]
    sent = fake.last_kwargs["messages"]
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[1]["content"] == "U"  # untouched


def test_usage_vocabulary_normalization():
    anthropic_style = _extract_usage(
        {"prompt_tokens": 100, "completion_tokens": 5,
         "cache_read_input_tokens": 60, "cache_creation_input_tokens": 10}
    )
    assert anthropic_style == Usage(100, 5, 60, 10)
    openai_style = _extract_usage(
        {"prompt_tokens": 100, "completion_tokens": 5,
         "prompt_tokens_details": {"cached_tokens": 60}}
    )
    assert openai_style == Usage(40, 5, 60, 0)  # inclusive → exclusive


@pytest.mark.asyncio
async def test_pruner_compacts_oldest_and_protects_tail():
    ledger = TurnLedger("t1")
    for i in range(8):
        call_id = f"c{i}"
        ledger.record_model_event(ModelEvent(
            kind="tool_use",
            payload={"call_id": call_id, "tool_name": "bash", "args": {}},
        ))
        ledger.record_model_event(ModelEvent(
            kind="done",
            payload={"stop_reason": "tool_use",
                     "usage": Usage(input_tokens=100_000)},
        ))
        ledger.record_tool_result(
            call_id, ToolResult(call_id=call_id, ok=True, content=f"{i}:" + "x" * 4000)
        )
    profile = resolve_profile("qwen", None)  # 32k window → far exceeded
    pruner = ToolResultPruner(keep_recent_results=2)
    assert pruner.should_compact(ledger, profile) is True
    entries = await pruner.compact(ledger, profile)
    assert entries  # something pruned
    ledger.apply_compaction(entries)
    tool_msgs = [m for m in ledger.provider_messages() if m["role"] == "tool"]
    # The newest two results stay verbatim.
    assert tool_msgs[-1]["content"].startswith("7:")
    assert tool_msgs[-2]["content"].startswith("6:")
    assert any(m["content"].startswith("[pruned]") for m in tool_msgs)


def test_litellm_route_follows_the_protocol_not_the_base_url():
    """A custom base_url alone must not imply the anthropic dialect.

    Model ids carry slashes (``deepseek-ai/DeepSeek-V3``) that litellm
    would read as a provider prefix, so the route has to be stated —
    but stated from the PROTOCOL the resolver decided. Before this,
    every custom endpoint was forced onto ``anthropic/`` and an
    openai-protocol card answered with ``AnthropicException``.
    """
    route = LiteLLMModelClient._litellm_model
    base = "https://api.netmind.ai/inference-api/openai/v1"
    assert route("deepseek-ai/DeepSeek-V3", base, "openai") == "openai/deepseek-ai/DeepSeek-V3"
    assert route("minimax/minimax-m2.5", "https://x/anthropic", "anthropic") == (
        "anthropic/minimax/minimax-m2.5"
    )
    # No base_url = litellm's own syntax, passed through untouched.
    assert route("gpt-5.4", "", "openai") == "gpt-5.4"


def test_litellm_route_prepends_even_when_id_carries_the_route_name():
    """Platform ids can EMBED the route name: NetMind's Claude models are
    literally called ``anthropic/claude-sonnet-5`` and its GPT models
    ``openai/gpt-5.4``. litellm consumes the first path segment as its
    routing prefix, so returning such ids unchanged sends the BARE name
    upstream — and NetMind has no bare aliases, it answers
    404 "unknown model: claude-sonnet-5" (dev incident 2026-07-30).
    With a custom base_url the route is therefore ALWAYS prepended:
    litellm eats the outer copy and the full platform id reaches the wire.
    """
    route = LiteLLMModelClient._litellm_model
    anthropic_base = "https://api.netmind.ai/inference-api/anthropic"
    openai_base = "https://api.netmind.ai/inference-api/openai/v1"
    assert route("anthropic/claude-sonnet-5", anthropic_base, "anthropic") == (
        "anthropic/anthropic/claude-sonnet-5"
    )
    assert route("openai/gpt-5.4", openai_base, "openai") == "openai/openai/gpt-5.4"


def test_tool_dialect_rewrite_is_anthropic_only():
    """OpenAI endpoints keep the OpenAI tool shape; only the anthropic
    route needs the native rewrite that dodges strict-gateway serde."""
    tools = [
        {
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {"type": "object"}},
        }
    ]
    dialect = LiteLLMModelClient._dialect_tools
    base = "https://gateway/v1"
    assert dialect(tools, base, "openai") == tools
    rewritten = dialect(tools, base, "anthropic")
    assert rewritten[0] == {
        "name": "t",
        "description": "d",
        "input_schema": {"type": "object"},
    }


def test_cache_breakpoints_also_mark_block_form_content():
    """A computed breakpoint must not be dropped on the floor.

    Only `str` content was marked, so any message already in block form
    (multimodal, or one the caller pre-built) silently bought no cache —
    the plan asked for a breakpoint and nothing carried it.
    """
    profile = resolve_profile("claude", "anthropic")
    client = LiteLLMModelClient(profile, client=None)
    request = ModelRequest(
        params=ModelParams(model="claude", provider="anthropic"),
        messages=[
            {"role": "system", "content": "plain"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ],
        tools=[],
        cache_plan=CachePlan(breakpoint_indices=(0, 1)),
    )
    marked = client._apply_cache_plan(request)

    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # Block form: the marker rides the LAST block, which is where
    # Anthropic reads it from.
    blocks = marked[1]["content"]
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    # The original request is untouched (marking copies).
    assert "cache_control" not in request.messages[1]["content"][-1]


@pytest.mark.asyncio
async def test_truncated_arguments_carry_parse_error_not_raw():
    """A stream cut mid-arguments (max_tokens) must NOT silently become
    ``{"_raw": ...}`` args — the tool_use event carries a parse_error so
    the loop can answer the call instead of executing it."""
    chunks = [
        _chunk({"tool_calls": [{"index": 0, "id": "c1",
                                "function": {"name": "write_file"}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": '{"path": "a.html", "content": "<htm'}}]}),
        _chunk(finish="length"),
    ]
    client = LiteLLMModelClient(
        resolve_profile("claude", "anthropic"), _FakeLitellm(chunks)
    )
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url="https://api.x.com"),
    )
    events = [e async for e in client.stream_step(request)]
    tool_use = next(e for e in events if e.kind == "tool_use")
    assert tool_use.payload["args"] == {}
    assert "_raw" not in tool_use.payload["args"]
    assert tool_use.payload["parse_error"]
    assert events[-1].payload["stop_reason"] == "length"


@pytest.mark.asyncio
async def test_well_formed_arguments_have_no_parse_error():
    chunks = [
        _chunk({"tool_calls": [{"index": 0, "id": "c1",
                                "function": {"name": "bash",
                                             "arguments": '{"command": "ls"}'}}]}),
        _chunk(finish="tool_calls"),
    ]
    client = LiteLLMModelClient(
        resolve_profile("claude", "anthropic"), _FakeLitellm(chunks)
    )
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url="https://api.x.com"),
    )
    events = [e async for e in client.stream_step(request)]
    tool_use = next(e for e in events if e.kind == "tool_use")
    assert tool_use.payload["args"] == {"command": "ls"}
    assert tool_use.payload["parse_error"] is None
    assert tool_use.payload["args_truncated"] is False


async def _tool_use_for(arguments: str, *, finish: str):
    chunks = [
        _chunk({"tool_calls": [{"index": 0, "id": "c1",
                                "function": {"name": "write_file"}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": arguments}}]}),
        _chunk(finish=finish),
    ]
    client = LiteLLMModelClient(
        resolve_profile("claude", "anthropic"), _FakeLitellm(chunks)
    )
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url="https://api.x.com"),
    )
    events = [e async for e in client.stream_step(request)]
    return next(e for e in events if e.kind == "tool_use")


async def _headers_for(base_url: str):
    chunks = [_chunk({"content": "hi"}), _chunk(finish="stop")]
    fake = _FakeLitellm(chunks)
    client = LiteLLMModelClient(resolve_profile("claude", "anthropic"), fake)
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url=base_url),
    )
    [e async for e in client.stream_step(request)]
    return ((fake.last_kwargs or {}).get("extra") or {}).get("extra_headers") or {}


@pytest.mark.asyncio
async def test_prefill_opt_out_header_only_goes_to_our_own_gateway():
    """It is a private agreement with our proxy. A direct vendor call
    gains nothing from it and should not carry our internal vocabulary
    off-site."""
    assert "x-nexus-prefill-retry" in await _headers_for("http://litellm:4000")
    assert "x-nexus-prefill-retry" not in await _headers_for("https://api.anthropic.com")
    assert "x-nexus-prefill-retry" not in await _headers_for("https://api.deepseek.com")


@pytest.mark.asyncio
async def test_truncation_is_read_off_the_json_not_off_stop_reason():
    """``stop_reason`` is the upstream's word and gateways get it wrong:
    the NetMind free-tier gateway reports ``tool_use`` for a call its own
    output cap severed (reproduced 2026-07-31 at max_tokens=2000, args
    cut to ``{"path": "game.html"``). Truncation is therefore decided by
    the shape of the JSON we actually received — a valid prefix that ran
    out — never by a field the provider can misreport."""
    tool_use = await _tool_use_for('{"path": "game.html"', finish="tool_calls")
    assert tool_use.payload["args_truncated"] is True
    # Unterminated string: the cut landed inside a value, not at the end.
    mid_string = await _tool_use_for('{"path": "a.html", "content": "<htm',
                                     finish="tool_calls")
    assert mid_string.payload["args_truncated"] is True


@pytest.mark.asyncio
async def test_genuinely_malformed_json_is_not_reported_as_truncation():
    """Bad escaping and stray delimiters fail in the MIDDLE of the buffer.
    Calling those "truncated" would send the model off to split a call
    that was never too long."""
    bad_escape = await _tool_use_for(r'{"path": "a\q.html"}', finish="tool_calls")
    assert bad_escape.payload["parse_error"]
    assert bad_escape.payload["args_truncated"] is False
    double_comma = await _tool_use_for('{"a": 1,, "b": 2}', finish="tool_calls")
    assert double_comma.payload["args_truncated"] is False
    # Not a prefix of any literal, and a full-length bad escape: damage.
    assert (await _tool_use_for('{"a": trX', finish="tool_calls")
            ).payload["args_truncated"] is False
    assert (await _tool_use_for(r'{"a": "\uZZZZ"}', finish="tool_calls")
            ).payload["args_truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    ['{"a": tru', '{"a": fals', '{"a": nul', r'{"a": "x\u00'],
)
async def test_a_cut_inside_a_literal_or_escape_is_still_truncation(arguments):
    """These fail STRICTLY INSIDE the buffer, so the end-of-buffer rule
    misses them — and they are exactly the shapes that would otherwise
    be answered with the escaping red herring."""
    tool_use = await _tool_use_for(arguments, finish="tool_calls")
    assert tool_use.payload["args_truncated"] is True
