"""
@file_name: test_mcp_caller_identity.py
@author: NarraNexus
@date: 2026-08-01
@description: Server-side caller identity for module MCP tools (P1
「Agent 消极回复"我做不了"」/ evt_0dcee899).

The failure being pinned: module MCP servers are ONE shared process per
module, so a tool's ``agent_id`` was whatever the MODEL typed. A model that
filled ``agent_id="agent_current"`` got

    Error: No SocialNetworkModule instance found for agent_id=agent_current

and answered the user "I couldn't complete this". Iron rule #15 forbids
blaming the user's model choice, so identity must come from the platform.

Contract pinned here:
- the injected caller identity WINS over whatever the model typed
- placeholder ids never reach a DB lookup
- with NO injection, behaviour is unchanged for real ids and becomes an
  actionable, self-correctable message for placeholders (never a dead end)
- tools without an ``agent_id`` parameter are left strictly alone
- the JSON schema still advertises ``agent_id`` (the model's call must
  keep validating)
"""
from __future__ import annotations

import contextlib

import pytest

from narranexus.platform.module_system._mcp_identity import (
    AGENT_ID_HEADER,
    BEARER_AGENT_PREFIX,
    agent_id_headers,
    caller_agent_id_from_request,
    install_caller_identity,
    is_placeholder_agent_id,
    resolve_caller_agent_id,
)

REAL = "agent_39b2b72b823b"
OTHER = "agent_someone_else"


# ---------------------------------------------------------------------------
# Fake ambient request (what the MCP server puts in the ContextVar)
# ---------------------------------------------------------------------------


class _Headers(dict):
    """Case-insensitive-enough stand-in for Starlette's Headers."""

    def get(self, key, default=None):  # noqa: D102
        return super().get(key.lower(), default)


@contextlib.contextmanager
def injected(headers: dict | None):
    """Install/clear an ambient MCP request carrying ``headers``."""
    from mcp.server.lowlevel.server import request_ctx

    if headers is None:
        token = request_ctx.set(type("Ctx", (), {"request": None})())
    else:
        request = type("Req", (), {"headers": _Headers({k.lower(): v for k, v in headers.items()})})()
        token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        request_ctx.reset(token)


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    "agent_current",   # the observed failure
    "AGENT_CURRENT",   # case must not matter
    "self", "me", "current", "current_agent", "{agent_id}", "<agent_id>",
    "", "   ", None,
])
def test_placeholders_are_recognised(value):
    assert is_placeholder_agent_id(value) is True


@pytest.mark.parametrize("value", [REAL, "agent_abc123", "agent_0dcee899"])
def test_real_ids_are_not_placeholders(value):
    assert is_placeholder_agent_id(value) is False


# ---------------------------------------------------------------------------
# Reading the injected identity
# ---------------------------------------------------------------------------


def test_identity_read_from_explicit_header():
    with injected({AGENT_ID_HEADER: REAL}):
        assert caller_agent_id_from_request() == REAL


def test_identity_read_from_borrowed_bearer():
    """The codex adapter cannot carry arbitrary headers — only a bearer."""
    with injected({"Authorization": f"Bearer {BEARER_AGENT_PREFIX}{REAL}"}):
        assert caller_agent_id_from_request() == REAL


def test_real_bearer_token_is_not_mistaken_for_identity():
    with injected({"Authorization": "Bearer sk-some-real-secret"}):
        assert caller_agent_id_from_request() is None


def test_no_request_in_scope_is_not_an_error():
    """A tool called directly (unit tests, in-process) must not blow up."""
    assert caller_agent_id_from_request() is None


def test_both_headers_agree_via_agent_id_headers():
    hdrs = agent_id_headers(REAL)
    with injected(hdrs):
        assert caller_agent_id_from_request() == REAL


# ---------------------------------------------------------------------------
# Resolution precedence
# ---------------------------------------------------------------------------


def test_injected_identity_replaces_placeholder():
    with injected({AGENT_ID_HEADER: REAL}):
        assert resolve_caller_agent_id("agent_current") == REAL


def test_injected_identity_wins_over_another_agents_id():
    """Hardening: a model echoing a teammate's id cannot read their data."""
    with injected({AGENT_ID_HEADER: REAL}):
        assert resolve_caller_agent_id(OTHER) == REAL


def test_without_injection_a_real_id_passes_through_unchanged():
    assert resolve_caller_agent_id(REAL) == REAL


def test_without_injection_a_placeholder_is_left_for_the_guard():
    # resolve() does not invent an id; the tool-level guard answers.
    assert resolve_caller_agent_id("agent_current") == "agent_current"


# ---------------------------------------------------------------------------
# install_caller_identity over a real FastMCP instance
# ---------------------------------------------------------------------------


def _server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test_module")
    seen: dict = {}

    @mcp.tool()
    async def needs_agent(agent_id: str, entity_id: str) -> dict:
        """Mirrors social_network.get_contact_info."""
        seen["agent_id"] = agent_id
        return {"ok": True, "agent_id": agent_id, "entity_id": entity_id}

    @mcp.tool()
    async def returns_text(agent_id: str) -> str:
        """A str-returning tool — the guard must match this shape."""
        return f"ran as {agent_id}"

    @mcp.tool()
    async def no_agent(x: int) -> dict:
        """Must be left strictly alone."""
        return {"x": x}

    install_caller_identity(mcp)
    return mcp, seen


def _fn(mcp, name):
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name].fn


@pytest.mark.asyncio
async def test_wrapped_tool_uses_injected_identity_over_placeholder():
    mcp, seen = _server()
    with injected(agent_id_headers(REAL)):
        out = await _fn(mcp, "needs_agent")(agent_id="agent_current", entity_id="e1")
    assert out["agent_id"] == REAL
    assert seen["agent_id"] == REAL


@pytest.mark.asyncio
async def test_wrapped_tool_without_injection_answers_actionably():
    """No injection + placeholder → a message the model can act on, in the
    tool's own return shape. This is what replaces the dead end."""
    mcp, _ = _server()
    out = await _fn(mcp, "needs_agent")(agent_id="agent_current", entity_id="e1")
    assert out["success"] is False
    assert "placeholder" in out["error"]
    assert "your instructions" in out["error"].lower()


@pytest.mark.asyncio
async def test_guard_matches_a_str_returning_tool():
    mcp, _ = _server()
    out = await _fn(mcp, "returns_text")(agent_id="self")
    assert isinstance(out, str)
    assert "placeholder" in out


@pytest.mark.asyncio
async def test_real_id_still_works_without_injection():
    mcp, _ = _server()
    out = await _fn(mcp, "needs_agent")(agent_id=REAL, entity_id="e1")
    assert out["ok"] is True and out["agent_id"] == REAL


@pytest.mark.asyncio
async def test_tool_without_agent_id_is_untouched():
    mcp, _ = _server()
    fn = _fn(mcp, "no_agent")
    assert getattr(fn, "_nx_identity_wrapped", False) is False
    assert (await fn(x=7))["x"] == 7


def test_schema_still_declares_agent_id():
    """Wrapping must not change the advertised signature — the model's tool
    call is validated against it."""
    mcp, _ = _server()
    tool = {t.name: t for t in mcp._tool_manager.list_tools()}["needs_agent"]
    assert "agent_id" in tool.parameters.get("properties", {})
    assert "agent_id" in tool.parameters.get("required", [])


def test_install_is_idempotent():
    """ModuleRunner may build a server more than once per process."""
    mcp, _ = _server()
    install_caller_identity(mcp)
    install_caller_identity(mcp)
    fn = _fn(mcp, "needs_agent")
    assert getattr(fn, "__wrapped__", None) is not None
    # Double-wrapping would nest __wrapped__ chains; assert depth is 1.
    assert getattr(fn.__wrapped__, "_nx_identity_wrapped", False) is False


# ---------------------------------------------------------------------------
# The deployment seam
# ---------------------------------------------------------------------------


def test_base_module_wrapper_instruments_and_never_raises():
    """ModuleRunner serves via build_instrumented_mcp_server; a module with
    no server still returns None, and a broken instrumentation must not stop
    the module from being served."""
    from narranexus.platform.module_system.base import XYZBaseModule

    assert hasattr(XYZBaseModule, "build_instrumented_mcp_server")

    class NoServer(XYZBaseModule):
        @staticmethod
        def get_config():
            from narranexus.platform.schema import ModuleConfig

            return ModuleConfig(name="NoServer", priority=9, enabled=True,
                                description="t", module_type="capability")

        async def mcp_server(self):
            return None

    m = NoServer(agent_id=REAL, user_id="u", database_client=None)
    assert m.build_instrumented_mcp_server() is None


def test_broken_instrumentation_still_serves_the_module(monkeypatch):
    """The never-raises half, which the test above did not actually cover:
    identity resolution is an improvement, not a precondition for serving. If
    installing it ever throws, the module must still be served (uninstrumented
    and loudly logged) rather than disappearing from the agent's toolset."""
    from narranexus.platform.module_system import base as base_mod
    from narranexus.platform.schema import ModuleConfig

    sentinel = object()

    class WithServer(base_mod.XYZBaseModule):
        @staticmethod
        def get_config():
            return ModuleConfig(name="WithServer", priority=9, enabled=True,
                                description="t", module_type="capability")

        async def mcp_server(self):
            return None

        def create_mcp_server(self):
            return sentinel

    def boom(_mcp):
        raise RuntimeError("instrumentation exploded")

    monkeypatch.setattr(
        "narranexus.platform.module_system._mcp_identity.install_caller_identity", boom
    )
    m = WithServer(agent_id=REAL, user_id="u", database_client=None)

    assert m.build_instrumented_mcp_server() is sentinel


# ---------------------------------------------------------------------------
# Coverage across the real module registry
# ---------------------------------------------------------------------------


def test_every_registered_module_resolves_caller_identity():
    """The ticket asks for "全部 module MCP 工具统一处理" — assert it, rather
    than trusting that 16 modules were each remembered.

    This passes with ZERO per-module edits because the wiring lives in
    XYZBaseModule.build_instrumented_mcp_server, which every served module
    goes through. A new module gets it by declaring ``agent_id``; the only
    way to regress is to bypass that wrapper, which this test would catch.
    """
    import inspect as _inspect

    from narranexus.platform.module_system import module_registry

    gaps: list[str] = []
    total = 0
    for name, cls in sorted(module_registry.items()):
        module = cls(agent_id=REAL, user_id="u", database_client=None)
        server = module.build_instrumented_mcp_server()
        if server is None:
            continue
        for tool in server._tool_manager.list_tools():
            try:
                params = _inspect.signature(tool.fn).parameters
            except (TypeError, ValueError):
                continue
            if "agent_id" not in params:
                continue
            total += 1
            if not getattr(tool.fn, "_nx_identity_wrapped", False):
                gaps.append(f"{name}.{tool.name}")

    assert gaps == [], f"tools still trusting the model's agent_id: {gaps}"
    # Guard against the assertion silently passing on an empty sweep.
    assert total > 80, f"expected the full tool surface, only saw {total}"


# ---------------------------------------------------------------------------
# Turn source (the codex-only channel — PR #229 review item 1)
# ---------------------------------------------------------------------------


def test_turn_source_read_from_the_explicit_header():
    from narranexus.platform.module_system._mcp_identity import (
        TURN_SOURCE_HEADER,
        caller_turn_source,
    )

    with injected({TURN_SOURCE_HEADER: "chat"}):
        assert caller_turn_source() == "chat"


def test_turn_source_read_from_the_bearer_when_the_header_is_gone():
    """The codex reality: only the bearer arrives."""
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_AGENT_PREFIX,
        BEARER_FIELD_SEP,
        caller_turn_source,
    )

    bearer = f"Bearer {BEARER_AGENT_PREFIX}{REAL}{BEARER_FIELD_SEP}message_bus"
    with injected({"Authorization": bearer}):
        assert caller_turn_source() == "message_bus"
        # …and the identity must still parse out of the same value.
        assert caller_agent_id_from_request() == REAL


def test_identity_unaffected_by_a_bearer_without_turn_source():
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_AGENT_PREFIX,
        caller_turn_source,
    )

    with injected({"Authorization": f"Bearer {BEARER_AGENT_PREFIX}{REAL}"}):
        assert caller_agent_id_from_request() == REAL
        assert caller_turn_source() is None


def test_no_turn_source_anywhere_is_none_not_a_guess():
    from narranexus.platform.module_system._mcp_identity import caller_turn_source

    with injected({AGENT_ID_HEADER: REAL}):
        assert caller_turn_source() is None


# ---------------------------------------------------------------------------
# The bearer is a positional record — arity contract (PR #229 review item 3)
# ---------------------------------------------------------------------------


def _bearer(*fields: str) -> str:
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_AGENT_PREFIX,
        BEARER_FIELD_SEP,
    )

    return f"Bearer {BEARER_AGENT_PREFIX}{BEARER_FIELD_SEP.join(fields)}"


def test_a_later_field_never_bleeds_into_the_turn_source():
    """The bug the shared parser exists to prevent: a hand-rolled
    ``split(SEP, 1)`` returned "<turn_source>~<next_field>" as the turn
    source, so adding a third field would silently poison the second."""
    from narranexus.platform.module_system._mcp_identity import caller_turn_source

    with injected({"Authorization": _bearer(REAL, "message_bus", "agent_peer1")}):
        assert caller_turn_source() == "message_bus"
        assert caller_agent_id_from_request() == REAL


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
def test_every_field_count_parses(count):
    """Trailing fields are omitted on the wire, so readers must tolerate any
    count — and each present field must land in its own slot.

    Every count is also a back-compat case: a sender that predates a later
    field transmits fewer, and every earlier field must still read exactly as
    before.

    The per-POSITION assertions below are load-bearing, not thoroughness for
    its own sake. ``user_id`` (dev) and ``root_run_id`` (cascade stop) were
    written in parallel and both landed on slot #5 before this merge. An arity
    check alone would go green the moment the count says 6 — even with the two
    swapped, which would decode a run id as a user id on the wire. Only
    asserting slot by slot can catch that.
    """
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_FIELDS,
        caller_errand_scope,
        caller_event_id_from_request,
        caller_root_run_id,
        caller_team_id_from_request,
        caller_turn_source,
        caller_user_id_from_request,
    )

    assert len(BEARER_FIELDS) == 10, "arity changed — update _parse_bearer + this test"
    assert BEARER_FIELDS[4] == "user_id" and BEARER_FIELDS[5] == "root_run_id", (
        "field ORDER changed — the wire is positional; a swap silently "
        "decodes one fact as another"
    )
    assert BEARER_FIELDS[6] == "team_id" and BEARER_FIELDS[7] == "event_id", (
        "field ORDER changed — first-to-dev keeps its slot (#255 landed first)"
    )
    assert BEARER_FIELDS[8] == "identity_token", (
        "identity_token must stay at slot #9 — verifiers read it positionally"
    )
    assert BEARER_FIELDS[9] == "thread_id"
    values = [
        REAL, "message_bus", "agent_peer1", "ch_errand1", "user_owner1",
        # #6 root_run_id, #7 team_id, #8 event_id — each pair here was written
        # in parallel and resolved first-to-dev-keeps-the-slot; identity_token
        # (this PR) yielded #7 to team_id the same way and lands at #9.
        "evt_root1", "team_1", "evt_1", "tok.abc", "thread_1",
    ][:count]
    with injected({"Authorization": _bearer(*values)}):
        assert caller_agent_id_from_request() == REAL
        assert caller_turn_source() == (values[1] if count >= 2 else None)
        peer, channel = caller_errand_scope()
        assert peer == (values[2] if count >= 3 else None)
        assert channel == (values[3] if count >= 4 else None)
        assert caller_user_id_from_request() == (values[4] if count >= 5 else None)
        assert caller_root_run_id() == (values[5] if count >= 6 else None)
        assert caller_team_id_from_request() == (values[6] if count >= 7 else None)
        assert caller_event_id_from_request() == (values[7] if count >= 8 else None)
        parsed = _parse_bearer_for_test(_bearer(*values))
        assert parsed.identity_token == (values[8] if count >= 9 else None)
        assert parsed.thread_id == (values[9] if count >= 10 else None)


def _parse_bearer_for_test(auth: str):
    from narranexus.platform.module_system._mcp_identity import _parse_bearer

    return _parse_bearer(auth)


def test_an_empty_middle_field_reads_as_unknown_not_as_a_shift():
    """A caller that knows its errand scope but not its turn source must not
    shift the scope one slot to the left."""
    from narranexus.platform.module_system._mcp_identity import (
        caller_errand_scope,
        caller_turn_source,
    )

    with injected({"Authorization": _bearer(REAL, "", "agent_peer1", "ch_errand1")}):
        assert caller_turn_source() is None
        assert caller_errand_scope() == ("agent_peer1", "ch_errand1")


def test_extra_fields_are_dropped_not_appended_to_the_last_one():
    from narranexus.platform.module_system._mcp_identity import caller_errand_scope

    with injected({
        "Authorization": _bearer(
            REAL, "message_bus", "agent_peer1", "ch_errand1", "future_fact"
        )
    }):
        assert caller_errand_scope() == ("agent_peer1", "ch_errand1")


def test_a_real_token_containing_the_marker_is_not_parsed():
    """Anchored, not substring: the parser must not slice up a real bearer."""
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_AGENT_PREFIX,
        caller_errand_scope,
        caller_turn_source,
    )

    with injected({"Authorization": f"Bearer real{BEARER_AGENT_PREFIX}{REAL}~chat"}):
        assert caller_agent_id_from_request() is None
        assert caller_turn_source() is None
        assert caller_errand_scope() == (None, None)


def test_root_run_id_round_trips_on_both_channels():
    """The trigger tree must survive on the bearer alone.

    codex forwards no custom headers, so a header-only lineage would break the
    cascade for every codex-backed agent — the exact hole the turn source had
    (PR #229 review). Iron rule #15: a first-class adapter is not a corner case.
    """
    from narranexus.platform.module_system._mcp_identity import (
        ROOT_RUN_ID_HEADER,
        agent_id_headers,
        caller_root_run_id,
    )

    headers = agent_id_headers(
        REAL, turn_source="message_bus", root_run_id="evt_root1",
    )
    assert headers[ROOT_RUN_ID_HEADER] == "evt_root1"

    with injected(headers):
        assert caller_root_run_id() == "evt_root1"
    # Bearer alone (codex): same answer.
    with injected({"Authorization": headers["Authorization"]}):
        assert caller_root_run_id() == "evt_root1"


def test_root_run_id_survives_an_empty_errand_scope():
    """A turn with a tree but no errand must not shift the tree left.

    The common shape: a team-room turn always has a root, and never has an
    errand scope. If the empty middle fields collapsed, the root would be read
    as the errand peer and the cascade would select nothing.
    """
    from narranexus.platform.module_system._mcp_identity import (
        agent_id_headers,
        caller_errand_scope,
        caller_root_run_id,
    )

    headers = agent_id_headers(REAL, turn_source="message_bus", root_run_id="evt_root1")
    with injected({"Authorization": headers["Authorization"]}):
        assert caller_errand_scope() == (None, None)
        assert caller_root_run_id() == "evt_root1"


def test_errand_scope_round_trips_on_both_channels():
    """agent_id_headers must emit the scope explicitly AND on the bearer —
    codex forwards nothing but the bearer, so a header-only fact is a hole
    (the same hole the turn source had)."""
    from narranexus.platform.module_system._mcp_identity import (
        ERRAND_CHANNEL_HEADER,
        ERRAND_PEER_HEADER,
        agent_id_headers,
        caller_errand_scope,
    )

    headers = agent_id_headers(
        REAL, turn_source="message_bus",
        errand_peer="agent_peer1", errand_channel="ch_errand1",
    )
    assert headers[ERRAND_PEER_HEADER] == "agent_peer1"
    assert headers[ERRAND_CHANNEL_HEADER] == "ch_errand1"

    with injected(headers):
        assert caller_errand_scope() == ("agent_peer1", "ch_errand1")
    # Bearer alone (codex): same answer.
    with injected({"Authorization": headers["Authorization"]}):
        assert caller_errand_scope() == ("agent_peer1", "ch_errand1")


def test_no_errand_scope_emits_no_scope_headers_and_no_trailing_separators():
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_AGENT_PREFIX,
        ERRAND_CHANNEL_HEADER,
        ERRAND_PEER_HEADER,
        caller_errand_scope,
    )

    headers = agent_id_headers(REAL, turn_source="chat")
    assert ERRAND_PEER_HEADER not in headers
    assert ERRAND_CHANNEL_HEADER not in headers
    assert headers["Authorization"] == f"Bearer {BEARER_AGENT_PREFIX}{REAL}~chat"
    with injected(headers):
        assert caller_errand_scope() == (None, None)

# ---------------------------------------------------------------------------
# identity_token (last bearer field) — carriage + dispatch-time stamping
# ---------------------------------------------------------------------------


def test_agent_id_headers_carries_identity_token():
    from narranexus.platform.module_system._mcp_identity import (
        IDENTITY_TOKEN_HEADER,
        agent_id_headers,
    )

    h = agent_id_headers(REAL, user_id="usr_1", identity_token="tok.abc")
    assert h[IDENTITY_TOKEN_HEADER] == "tok.abc"
    # codex only forwards the bearer — the token MUST ride it too.
    assert h["Authorization"].endswith("~tok.abc")


def test_agent_id_headers_without_token_is_unchanged():
    from narranexus.platform.module_system._mcp_identity import (
        IDENTITY_TOKEN_HEADER,
        agent_id_headers,
    )

    h = agent_id_headers(REAL, user_id="usr_1")
    assert IDENTITY_TOKEN_HEADER not in h
    assert not h["Authorization"].endswith("~")


def test_stamp_identity_token_rebuilds_existing_headers():
    from narranexus.platform.module_system._mcp_identity import (
        _parse_bearer,
        agent_id_headers,
        stamp_identity_token,
    )

    servers = {
        "awareness": {
            "url": "http://mcp:7801/sse",
            "headers": agent_id_headers(REAL, turn_source="chat", user_id="usr_1"),
        }
    }
    stamp_identity_token(servers, "tok.abc")
    ident = _parse_bearer(servers["awareness"]["headers"]["Authorization"])
    assert ident.identity_token == "tok.abc"
    # Every previously-carried fact survives the restamp.
    assert ident.agent_id == REAL
    assert ident.turn_source == "chat"
    assert ident.user_id == "usr_1"


def test_stamp_skips_headerless_and_foreign_specs():
    from narranexus.platform.module_system._mcp_identity import stamp_identity_token

    servers = {
        "no_headers": {"url": "http://mcp:7801/sse"},
        "foreign": {"url": "http://x/", "headers": {"Authorization": "Bearer real-token"}},
    }
    stamp_identity_token(servers, "tok.abc")
    assert "headers" not in servers["no_headers"]
    # A non-nx bearer must never be rewritten into an identity record.
    assert servers["foreign"]["headers"] == {"Authorization": "Bearer real-token"}


def test_stamp_preserves_every_bearer_fact():
    """stamp_identity_token rebuilds the record through the one builder — any
    field it fails to pass through is ERASED from the bearer, and codex
    forwards nothing else (round-6 review #2: dropping team_id would silently
    demote every team turn to private on that adapter). Driven by
    BEARER_FIELDS itself, so the NEXT appended field trips this test the
    moment stamp forgets to thread it."""
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_FIELDS,
        _parse_bearer,
        agent_id_headers,
        stamp_identity_token,
    )

    values = {
        name: f"v{i}" for i, name in enumerate(BEARER_FIELDS)
        if name != "identity_token"
    }
    values["agent_id"] = REAL
    servers = {"m": {"url": "http://mcp:7801/sse", "headers": agent_id_headers(**values)}}
    stamp_identity_token(servers, "tok.xyz")
    parsed = _parse_bearer(servers["m"]["headers"]["Authorization"])
    for name, expected in values.items():
        assert getattr(parsed, name) == expected, (
            f"stamp erased {name!r} from the bearer — pass it through in "
            f"stamp_identity_token"
        )
    assert parsed.identity_token == "tok.xyz"
