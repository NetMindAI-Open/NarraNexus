"""
@file_name: test_codex_sdk_v2_init.py
@date: 2026-06-04
@description: Import-contract tests for CodexSDKv2.

Verifies:
* Protocol conformance (CodexSDKv2 satisfies AgentLoopDriver via
  structural typing).
* Method signature compatibility with v1 CodexSDK so step_3's
  driver-agnostic dispatch sees identical surfaces.
* Registry: both ``codex_cli_v2`` and ``codex_official`` names
  resolve to a CodexSDKv2 instance.
* ``_build_codex_config_overrides`` produces the right TOML-literal
  strings for the canonical inputs (MCP urls, sandbox, reasoning
  summary, permissions, writable roots).

These tests do NOT exercise the SDK itself (no subprocess, no
network) — they just lock in the static contract of v2. Live SDK
behavior is verified by ``scripts/spike_codex_official_sdk.py``
Section A/B and by Task 10's manual smoke gate.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework.agent_loop_driver import (
    AgentLoopDriver,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
)
from xyz_agent_context.agent_framework.xyz_codex_cli_sdk import CodexSDK
from xyz_agent_context.agent_framework.xyz_codex_official_sdk import (
    CodexSDKv2,
    _build_codex_config_overrides,
)


# ---------------- Protocol conformance ----------------


def test_codex_sdk_v2_is_agent_loop_driver():
    """Structural typing check — CodexSDKv2 must conform to the
    AgentLoopDriver Protocol (defined in agent_loop_driver.py)."""
    instance = CodexSDKv2("./")
    assert isinstance(instance, AgentLoopDriver), (
        "CodexSDKv2 doesn't conform to AgentLoopDriver Protocol — "
        "check the agent_loop method signature matches the Protocol."
    )


def test_codex_sdk_v2_signature_matches_v1():
    """v1 and v2 must expose the SAME agent_loop signature so the
    dispatcher in step_3_agent_loop sees them as interchangeable."""
    v1_sig = inspect.signature(CodexSDK.agent_loop)
    v2_sig = inspect.signature(CodexSDKv2.agent_loop)
    # Same parameter names in same order. Forward-reference type
    # hints (string vs evaluated) may differ across files, so we
    # only assert on parameter names, not annotations.
    assert list(v1_sig.parameters.keys()) == list(v2_sig.parameters.keys()), (
        f"v1 params {list(v1_sig.parameters)} vs "
        f"v2 params {list(v2_sig.parameters)}"
    )


def test_codex_sdk_v2_init_takes_working_path():
    """Factory contract: ``CodexSDKv2(working_path='...')`` matches the
    factory shape ``get_agent_loop_driver`` forwards kwargs to."""
    instance = CodexSDKv2(working_path="/tmp/x")
    assert instance.working_path == "/tmp/x"


# ---------------- Registry registration ----------------


def test_v2_registered_under_codex_cli():
    """``codex_cli`` is the single canonical framework id. A/B aliases
    (``codex_cli_v2``, ``codex_official``, plain ``codex``) were
    deregistered after the cutover — keeping them around was just a
    backwards-compatibility shim per binding rule #2 (YOLO)."""
    assert "codex_cli" in available_agent_loop_frameworks()


def test_ab_period_aliases_no_longer_registered():
    """Regression guard: the A/B-period aliases must NOT be present.
    If they reappear, the cleanup commit got reverted."""
    available = set(available_agent_loop_frameworks())
    assert "codex_cli_v2" not in available
    assert "codex_official" not in available
    assert "codex" not in available
    assert "claude" not in available


def test_codex_cli_resolves_to_v2():
    """``codex_cli`` resolves to the official-SDK driver. The v1
    ``CodexSDK`` class still lives in ``xyz_codex_cli_sdk.py`` as a
    revival fallback but is intentionally NOT registered — pulling it
    back online requires a
    one-line ``register_agent_loop_driver`` edit in
    ``agent_framework/__init__.py``.
    """
    assert "codex_cli" in available_agent_loop_frameworks()
    d = get_agent_loop_driver(framework="codex_cli", working_path="./")
    assert isinstance(d, CodexSDKv2)
    # And the v1 class is importable but distinct — confirms the
    # source file is still in the repo (revival fallback intact).
    assert CodexSDK is not CodexSDKv2


# ---------------- _build_codex_config_overrides ----------------


def test_overrides_emits_required_baseline_keys():
    """Every config must carry instructions file + sandbox + reasoning
    summary — those are the three baseline keys v1 also always emits."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/agent/instructions.md"),
        mcp_servers={},
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'model_instructions_file="/tmp/agent/instructions.md"' in joined
    assert 'sandbox_mode="danger-full-access"' in joined
    assert 'model_reasoning_summary="detailed"' in joined


def test_overrides_emits_one_mcp_entry_per_server():
    """One ``mcp_servers.<name>.url=...`` line per URL passed in."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={
            "lark_module": {"url": "http://localhost:7831/mcp"},
            "slack_module": {"url": "http://localhost:7832/mcp"},
        },
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'mcp_servers.lark_module.url="http://localhost:7831/mcp"' in joined
    assert 'mcp_servers.slack_module.url="http://localhost:7832/mcp"' in joined


def test_overrides_rewrites_sse_url_to_streamable_http():
    """MCP URL rewriter (imported from v1) must apply: ``/sse`` → ``/mcp``."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={"x": {"url": "http://localhost:7801/sse"}},
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'mcp_servers.x.url="http://localhost:7801/mcp"' in joined


def test_overrides_quotes_glob_keys_in_permissions():
    """Permission rule keys with shell-meta chars MUST be TOML-quoted
    so codex's --config parser doesn't try to expand them as wildcards.
    This is the bug class spike Section A surfaced — guard with a test."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions={
            "commands": {
                "brew install *": "deny",
                "sudo *": "deny",
            },
            "filesystem": {
                "/etc/**": "deny",
            },
        },
    )
    joined = "\n".join(result)
    # Each glob-bearing key must be wrapped in double quotes on the LHS.
    assert 'permissions.commands."brew install *"="deny"' in joined
    assert 'permissions.commands."sudo *"="deny"' in joined
    assert 'permissions.filesystem."/etc/**"="deny"' in joined


def test_overrides_emits_writable_roots_array():
    """When writable_roots is passed, emit the
    ``sandbox_workspace_write.writable_roots`` array entry."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        writable_roots=[Path("/tmp/agent"), Path("/scratch")],
    )
    joined = "\n".join(result)
    assert 'sandbox_workspace_write.writable_roots=["/tmp/agent", "/scratch"]' in joined


def test_overrides_omits_model_when_none():
    """``model=None`` means "use codex's default" — don't emit the line."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        model=None,
    )
    assert not any(entry.startswith("model=") for entry in result)


def test_overrides_emits_model_when_provided():
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        model="gpt-5.5",
    )
    joined = "\n".join(result)
    assert 'model="gpt-5.5"' in joined


def test_overrides_returns_tuple_not_list():
    """``CodexConfig.config_overrides`` is typed ``tuple[str, ...]``; if
    we accidentally return a list, the dataclass init would still
    accept it but type checkers complain. Lock in tuple."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
    )
    assert isinstance(result, tuple)
    assert all(isinstance(s, str) for s in result)


# ---------------- API-key auth wiring (incident 2026-06-11) ----------


def test_overrides_declares_model_provider_for_api_key():
    """REGRESSION: an API-key agent slot needs a custom model_provider +
    env_key so codex authenticates with CODEX_API_KEY. v1's config.toml
    did this; the v2 cutover dropped it, so codex hit the Responses API
    with no auth header → '401 Missing bearer'. These overrides restore
    it."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        api_key="sk-test-123",
        base_url="https://api.openai.com/v1",
        auth_type="api_key",
    )
    joined = "\n".join(result)
    assert 'model_provider="narranexus"' in joined
    assert 'model_providers.narranexus.env_key="CODEX_API_KEY"' in joined
    assert 'model_providers.narranexus.base_url="https://api.openai.com/v1"' in joined
    assert 'model_providers.narranexus.wire_api="responses"' in joined
    # The key itself is NEVER inlined into config_overrides — it flows via
    # the CODEX_API_KEY env var that env_key references.
    assert "sk-test-123" not in joined


def test_overrides_defaults_base_url_for_api_key_without_one():
    """Empty base_url on an API-key slot → official OpenAI endpoint."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        api_key="sk-x",
        base_url="",
        auth_type="api_key",
    )
    joined = "\n".join(result)
    assert 'model_providers.narranexus.base_url="https://api.openai.com/v1"' in joined


def test_overrides_no_model_provider_for_oauth():
    """OAuth slots authenticate via the staged auth.json — they must NOT
    declare a custom provider (that path expects CODEX_API_KEY, which is
    blank for OAuth)."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers={},
        permissions=None,
        api_key="",
        auth_type="oauth",
    )
    joined = "\n".join(result)
    assert "model_provider" not in joined
    assert "model_providers" not in joined


def test_thread_start_accepts_kwargs_we_actually_pass():
    """SDK contract test — ``AsyncCodex.thread_start`` must keep
    accepting the kwargs we pass at runtime. The v2 ``agent_loop``
    crashed on 2026-06-08 because it passed
    ``skip_git_repo_check=True`` (a v1 CLI flag I mistakenly assumed
    the SDK exposed) — TypeError "unexpected keyword argument".

    Pin the kwarg set so an SDK rename / removal fails CI before
    hitting a user. If this test fails after an SDK upgrade:
      1. Inspect ``inspect.signature(AsyncCodex.thread_start)``
      2. Update ``xyz_codex_official_sdk.py``'s thread_start call to
         match the new shape
      3. Update this test to match what we actually pass now.
    """
    import inspect as _inspect

    from openai_codex import AsyncCodex

    sig = _inspect.signature(AsyncCodex.thread_start)
    params = set(sig.parameters.keys())

    # Kwargs the v2 agent_loop currently passes — every one of these
    # MUST be in the SDK signature or thread_start blows up at runtime.
    required = {"sandbox"}
    missing = required - params
    assert not missing, (
        f"openai_codex.AsyncCodex.thread_start lost kwarg(s) {missing}. "
        f"Update xyz_codex_official_sdk.py:agent_loop and this test. "
        f"Available kwargs: {sorted(params)}"
    )

    # Belt-and-suspenders: explicitly assert the kwarg we used to
    # pass and removed is still NOT here — if the SDK reintroduces
    # skip_git_repo_check we can re-enable it (and the CodexConfig
    # launch_args_override workaround becomes unnecessary).
    assert "skip_git_repo_check" not in params, (
        "SDK reintroduced skip_git_repo_check — consider re-enabling "
        "it at the thread_start call site for clearer intent."
    )


def test_response_processor_recognises_thinking_item():
    """Internal-contract test — the event type ``thinking_item``
    emitted by our v2 reasoning-delta translation MUST be a string
    response_processor's run_item_stream_event handler explicitly
    matches. Otherwise reasoning content silently lands in the
    catch-all "OTHER" branch (incident 2026-06-08: 296 reasoning
    deltas per turn → 0 chars visible in Thinking panel because
    we emitted ``thinking_delta`` which response_processor doesn't
    know).

    This test inspects the handler's source for a literal
    ``"thinking_item"`` comparison. If the handler is rewritten to
    use a different mechanism (registry, dispatch dict, etc.) this
    test will need updating — but at that point we WANT it to fail
    so we re-verify the contract.
    """
    import inspect as _inspect

    from xyz_agent_context.agent_runtime.response_processor import (
        ResponseProcessor,
    )

    handler_src = _inspect.getsource(
        ResponseProcessor._handle_run_item_stream_event
    )
    assert '"thinking_item"' in handler_src or "'thinking_item'" in handler_src, (
        "ResponseProcessor._handle_run_item_stream_event no longer "
        "branches on item.type == 'thinking_item'. The v2 translator "
        "emits this exact type for streamed reasoning deltas; if the "
        "handler renamed the type, update output_transfer.py's "
        "reasoning translation accordingly. Otherwise reasoning "
        "content vanishes into the OTHER catch-all."
    )


def test_v2_item_type_table_covers_known_sdk_types():
    """SDK contract test — ``_V2_ITEM_TYPE_TO_V1`` in output_transfer
    must cover every ThreadItem type the v1 helper expects to see.

    The v1 helper has 3 frozensets (TEXT, THINKING, TOOL); any SDK
    ThreadItem variant whose ``type`` Literal maps to one of those
    target snake_case names MUST be present in the normalizer table.
    Otherwise the v1 helper silently drops it (incident 2026-06-08:
    every agent_message item fell through 'unknown — drop' and the
    no_reply fallback fired every turn).
    """
    import openai_codex.generated.v2_all as v2

    from xyz_agent_context.agent_framework.output_transfer import (
        _CODEX_ITEM_TYPES_TEXT,
        _CODEX_ITEM_TYPES_THINKING,
        _CODEX_ITEM_TYPES_TOOL,
        _V2_ITEM_TYPE_TO_V1,
    )

    targets = _CODEX_ITEM_TYPES_TEXT | _CODEX_ITEM_TYPES_THINKING | _CODEX_ITEM_TYPES_TOOL

    # Pull every ThreadItem subclass's type Literal.
    sdk_item_types: set[str] = set()
    for name in dir(v2):
        cls = getattr(v2, name)
        if not (
            isinstance(cls, type)
            and hasattr(cls, "model_fields")
            and "type" in cls.model_fields
        ):
            continue
        ann = cls.model_fields["type"].annotation
        # Pydantic Literal annotations expose values via __args__
        for value in getattr(ann, "__args__", ()):
            if isinstance(value, str):
                sdk_item_types.add(value)

    # For each SDK item type, after normalization, does it land on
    # something v1 cares about? If not, that's OK (most ThreadItem
    # variants are forward-compat / unused). But if the SDK type's
    # NORMALIZED form matches a v1 target, the table must contain it.
    #
    # Equivalently: every v1 target must be reachable from some
    # camelCase key in the table. Verify by reverse lookup.
    reverse: dict[str, str] = {
        snake: camel for camel, snake in _V2_ITEM_TYPE_TO_V1.items()
    }
    missing_in_table: set[str] = set()
    for target in targets:
        if target not in reverse and target not in sdk_item_types:
            # v1 target that doesn't exist in SDK at all — also OK
            # (might be a legacy v1-only type). Skip.
            continue
        if target in reverse:
            continue  # already mapped (or matches snake_case identity)
        missing_in_table.add(target)

    assert not missing_in_table, (
        f"v1 helper expects item types {missing_in_table} but "
        f"_V2_ITEM_TYPE_TO_V1 doesn't map any SDK camelCase type to them. "
        f"Add the camelCase → snake_case entry."
    )


def test_method_constants_match_sdk_notification_registry():
    """SDK contract test — every ``_METHOD_*`` constant our translator
    listens for MUST appear in ``openai_codex.generated.notification_registry.NOTIFICATION_MODELS``.

    Initial v2 commit had every "item/*" notification mistakenly
    written as "turn/*" (2026-06-08, output_transfer.py). The SDK
    silently dropped all unmatched dispatches and the model's
    reasoning + tool calls leaked into the chat bubble as plain
    text. This test re-checks the constant set against the live
    SDK registry on every CI run; if a future SDK rename misaligns
    our constants the test fails BEFORE shipping.

    NOTE: ``_METHOD_TURN_FAILED`` was deliberately REMOVED — the SDK
    does not emit ``turn/failed``; failure surfaces via
    ``turn/completed`` with ``turn.status == "failed"``.
    """
    from openai_codex.generated.notification_registry import NOTIFICATION_MODELS

    from xyz_agent_context.agent_framework import output_transfer as ot

    method_constants = {
        name: getattr(ot, name)
        for name in dir(ot)
        if name.startswith("_METHOD_") and isinstance(getattr(ot, name), str)
    }
    assert method_constants, "no _METHOD_* constants found — file reorg?"

    sdk_methods = set(NOTIFICATION_MODELS.keys())
    misaligned = {
        name: value
        for name, value in method_constants.items()
        if value not in sdk_methods
    }
    assert not misaligned, (
        f"{len(misaligned)} translator method constant(s) are NOT in the "
        f"SDK's notification registry — silent drops will happen at runtime:\n"
        + "\n".join(f"  {n} = {v!r}" for n, v in misaligned.items())
        + f"\n\nActual SDK methods (sample): {sorted(sdk_methods)[:10]}..."
    )


def test_turn_is_coroutine_function():
    """SDK contract test — ``AsyncThread.turn`` must be a coroutine
    so ``await thread.turn(...)`` works. If the SDK ever flips it to
    sync we'd silently get back an unawaited coroutine object and
    everything downstream would crash with confusing errors."""
    import inspect as _inspect

    from openai_codex import AsyncThread

    assert _inspect.iscoroutinefunction(AsyncThread.turn), (
        "AsyncThread.turn is no longer a coroutine — agent_loop's "
        "``await thread.turn(...)`` will return the wrong object. "
        "Restore the defensive ``inspect.iscoroutine`` ladder or "
        "revisit the SDK contract."
    )


def test_stream_is_async_generator_function():
    """SDK contract test — ``AsyncTurnHandle.stream`` must be an
    async generator so ``async for ... in handle.stream()`` works.
    Initial v2 shipped with an ``asyncio.to_thread(next, stream)``
    wrapper assuming stream() was sync — would have silently misfired
    on every run if it weren't shadowed by an earlier crash."""
    import inspect as _inspect

    from openai_codex import AsyncTurnHandle

    assert _inspect.isasyncgenfunction(AsyncTurnHandle.stream), (
        "AsyncTurnHandle.stream is no longer an async generator — "
        "agent_loop's ``async for n in handle.stream()`` will not "
        "iterate. Either restore a bridge or switch back to next()."
    )


def test_interrupt_is_coroutine_function():
    """SDK contract test — ``AsyncTurnHandle.interrupt`` must be a
    coroutine so ``await handle.interrupt()`` cancels cleanly. If
    flipped to sync, our await would error; if wrapped in
    asyncio.to_thread, it would silently double-block the loop."""
    import inspect as _inspect

    from openai_codex import AsyncTurnHandle

    assert _inspect.iscoroutinefunction(AsyncTurnHandle.interrupt), (
        "AsyncTurnHandle.interrupt is no longer a coroutine — "
        "agent_loop's cancellation path needs adjustment."
    )


def test_sandbox_full_access_attribute_exists():
    """SDK contract test — the ``openai_codex.Sandbox`` enum must expose
    ``full_access``. The v2 ``agent_loop`` passes this to
    ``thread_start(sandbox=...)``. If the SDK ever renames the enum
    (the move from 0.1.0bN's ``danger_full_access`` to plain
    ``full_access`` already happened once and burned an integration
    smoke run on 2026-06-08), this test catches it before it ships.

    Note the two-layer naming convention preserved on purpose:
    * codex internal config / TOML / CLI flag:  ``danger-full-access``
    * openai_codex SDK ``Sandbox`` enum:        ``full_access``
    Both refer to the same sandbox mode."""
    from openai_codex import Sandbox

    assert hasattr(Sandbox, "full_access"), (
        "openai_codex.Sandbox.full_access missing — SDK upgrade likely "
        "renamed the enum. Update xyz_codex_official_sdk.py line 390 "
        "and this test together."
    )
    # Adjacent enum values we don't currently use but want to detect if
    # they ever disappear (would signal a much bigger SDK API rework).
    assert hasattr(Sandbox, "read_only")
    assert hasattr(Sandbox, "workspace_write")
