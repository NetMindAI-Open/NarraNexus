"""
@file_name: _mcp_identity.py
@author: NarraNexus
@date: 2026-08-01
@description: Server-side caller identity for module MCP tools.

Why this exists (P1, 2026-08-02 线下 / evt_0dcee899)
---------------------------------------------------
Every module MCP server is ONE process shared by all agents, and ~90 tools
across 14 modules take ``agent_id`` as a normal tool parameter — i.e. the
caller's own identity was supplied by the MODEL. The module prompts do state
it ("**IMPORTANT:** Your agent_id is `agent_x`"), but a model that fills the
placeholder it assumes by convention — the observed failure passed the
literal ``agent_id="agent_current"`` — hit a hard dead end:

    Error: No SocialNetworkModule instance found for agent_id=agent_current

...and the agent then told the user it could not do the task. Per iron rule
#15 the platform does not police which model a user picks, so a
machine-knowable fact must not depend on model obedience.

Note the ticket's "inject caller identity OR resolve the agent_current/self
alias" is not a real either/or: the server cannot resolve "the current
agent" without being told who called, because the server is shared. Alias
resolution REQUIRES injection. This module does both halves.

The channel: HTTP headers
-------------------------
Measured 2026-08-01 against both transports the adapters use (probe kept in
the PR description): request headers reach the tool on BOTH the SSE path
(claude, tool calls POST to /messages/) and the streamable-HTTP path
(codex, POST /mcp). A URL query param does NOT survive the SSE path — the
tool-call POST goes to /messages/?session_id=..., so the /sse query string
is gone. Headers are therefore the only channel that works uniformly.

Two header spellings are injected, and either is accepted, because the two
adapters can express different things:
  * ``X-NarraNexus-Agent-Id`` — the honest one. The claude adapter forwards
    arbitrary headers verbatim.
  * ``Authorization: Bearer nx-agent:<agent_id>`` — the codex adapter
    CANNOT carry arbitrary headers (see adapters/codex/official_sdk.py:
    "Codex config cannot carry arbitrary HTTP headers"); a bearer token is
    the one header shape it does transmit. Module MCP servers run no auth,
    so borrowing the header is free — but it IS borrowing, hence the
    ``nx-agent:`` prefix so this can never be confused with a real token.

Injection site: context_runtime's per-agent mcp_servers spec build (one
line, one place — the same spec dict both adapters consume).

Trust model change
------------------
Before this, the server did not verify that ``agent_id`` matched the caller
(documented in references/module_system.md §5 as "运行时是可信的"). When the
header is present it now WINS over the parameter, so one agent can no
longer read another agent's data by passing its id — a hardening side
effect. Every module tool documents ``agent_id`` as "your own id", so
nothing legitimate passes someone else's.

Fail-open, always
-----------------
No header (older adapter, a framework we have not taught yet, a direct
curl) → the parameter is used exactly as before. This module can only
ever ADD identity, never remove the previous behaviour.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, NamedTuple, Optional

from loguru import logger

# The honest header. Lowercased on lookup — HTTP headers are case-insensitive
# and Starlette normalises them.
AGENT_ID_HEADER = "X-NarraNexus-Agent-Id"

# Marks a borrowed Authorization bearer as OUR identity channel rather than a
# real credential (the codex adapter can only transmit a bearer).
BEARER_AGENT_PREFIX = "nx-agent:"

# WHICH KIND of turn is calling ("chat" / "job" / "message_bus" / …). Same
# injection seam as the identity, because the same problem applies: a tool
# cannot otherwise know whether the turn it serves is an owner-facing errand
# or an agent answering a peer, and bus_send_to_agent must record that on the
# message (see message_bus_trigger's directive selection).
TURN_SOURCE_HEADER = "X-NarraNexus-Turn-Source"

# WHO owns this turn — the user whose request the agent is serving. Same
# problem, worse failure shape: a model-guessed user_id on job_create still
# CREATES the job (success=True) — under the wrong user, so the owner's Jobs
# list stays empty forever while the agent reports success. Unlike agent_id
# this is resolved placeholder-only (see resolve_caller_user_id): None is a
# legitimate "no filter" value on retrieval tools, and a mismatching real
# value can be legitimate in multi-user flows — measure before overriding.
USER_ID_HEADER = "X-NarraNexus-User-Id"

# WHO/WHERE this turn's own errand is aimed at, when it has one: the peer that
# answered our errand, and the channel that exchange lives in. A bus turn is
# not homogeneous — the same turn can continue our errand AND answer an
# unrelated peer whose question the platform injected (bus unread is collected
# across ALL channels), so "what kind of turn is this" cannot decide the stamp
# alone. The scope lets each SEND compare its own target and stamp itself; see
# message_bus_module/_message_bus_mcp_tools.py::_send_turn_source.
ERRAND_PEER_HEADER = "X-NarraNexus-Errand-Peer"
ERRAND_CHANNEL_HEADER = "X-NarraNexus-Errand-Channel"

# Everything above ALSO rides the borrowed bearer, because codex forwards
# nothing else. Shipping a fact only on an explicit header was a real hole
# (PR #229 review): a codex-side asker always wrote NULL turn source, and the
# recipient fell back to the "have I spoken here" heuristic — the one that
# flips a FOLLOW-UP question back to Owner Relay and reproduces the P1. Iron
# rule #15 forbids treating a first-class adapter as a corner.
#
#     Authorization: Bearer nx-agent:<agent_id>~<turn_source>~<errand_peer>~<errand_channel>
#
# Contract — pin it, do not improvise:
#   * fields are POSITIONAL and their order is frozen; ``BEARER_FIELDS`` names
#     them in order and is the single source of truth for the count;
#   * trailing empty fields are omitted on the wire, so a reader must tolerate
#     ANY count from 1 to len(BEARER_FIELDS), and an empty middle field is a
#     legal "unknown";
#   * a new fact is APPENDED (never inserted mid-record) and added to
#     ``BEARER_FIELDS``;
#   * parse only via ``_parse_bearer``. A hand-rolled ``split(SEP, 1)`` reads
#     "<turn_source>~<next_field>" as the turn source — that is exactly how
#     adding a third field would have silently poisoned the second, and it is
#     why the two readers here no longer each roll their own;
#   * every field stays token68-safe (RFC 7235): "~" qualifies, and it appears
#     in none of our ids (``agent_`` / ``ch_`` + hex), turn sources or stamps.
BEARER_FIELD_SEP = "~"
BEARER_FIELDS = ("agent_id", "turn_source", "errand_peer", "errand_channel", "user_id")

# Values a model supplies when it is guessing instead of reading its prompt.
# These are NOT agent ids and must never reach a DB lookup: treat them as
# "the model did not tell us", then fall back to the injected identity.
# "agent_current" is the observed one (evt_0dcee899); the rest are the
# conventions models reach for next.
PLACEHOLDER_AGENT_IDS = frozenset({
    "agent_current",
    "current",
    "current_agent",
    "self",
    "me",
    "my_agent_id",
    "your_agent_id",
    "agent_self",
    "agent_id",
    "{agent_id}",
    "<agent_id>",
    "none",
    "null",
    "todo",
})


def is_placeholder_agent_id(value: Any) -> bool:
    """True when ``value`` is absent or a known guess rather than a real id."""
    if not isinstance(value, str):
        return value is None
    v = value.strip()
    if not v:
        return True
    return v.lower() in PLACEHOLDER_AGENT_IDS


# The user_id spellings a model reaches for when guessing. NOTE the asymmetry
# with agent ids: ``None`` is NOT in scope here and never will be — retrieval
# tools use ``user_id=None`` as a legitimate "no filter" value, so only a
# guessed STRING marks "the model did not tell us".
PLACEHOLDER_USER_IDS = frozenset({
    "user_current",
    "current_user",
    "current",
    "self",
    "me",
    "user",
    "user_id",
    "{user_id}",
    "<user_id>",
    "my_user_id",
    "your_user_id",
    "requesting_user",
    "owner",
    "none",
    "null",
    "todo",
})


def is_placeholder_user_id(value: Any) -> bool:
    """True when ``value`` is a guessed string. ``None`` is NOT a placeholder
    (legitimate "unset"/"no filter" — see PLACEHOLDER_USER_IDS)."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return True
    return v.lower() in PLACEHOLDER_USER_IDS


class BearerIdentity(NamedTuple):
    """The decoded borrowed bearer. Every field is None when not transmitted."""

    agent_id: Optional[str] = None
    turn_source: Optional[str] = None
    errand_peer: Optional[str] = None
    errand_channel: Optional[str] = None
    user_id: Optional[str] = None


def _parse_bearer(auth: str) -> BearerIdentity:
    """Decode ``Authorization: Bearer nx-agent:…`` into its named fields.

    THE only bearer parser (see the ``BEARER_FIELDS`` contract above). Anchored
    on the full marker rather than searching for a substring: a real token that
    merely CONTAINS "nx-agent:" must never be sliced up and read as identity.
    A field count above ``len(BEARER_FIELDS)`` cannot bleed into the last named
    field — extra segments are dropped, because guessing which future fact they
    were is worse than reporting the ones we understand.
    """
    marker = f"Bearer {BEARER_AGENT_PREFIX}"
    if not auth.startswith(marker):
        return BearerIdentity()
    # Unbounded split, then truncate: a bounded split would leave any extra
    # segment glued to the LAST named field ("ch_1~future_fact" read as a
    # channel id) — a newer sender must degrade to "unknown", never corrupt.
    raw = auth[len(marker):].split(BEARER_FIELD_SEP)
    values = [(v or "").strip() or None for v in raw[: len(BEARER_FIELDS)]]
    values += [None] * (len(BEARER_FIELDS) - len(values))
    return BearerIdentity(*values)


def _ambient_headers():
    """The current MCP request's headers, or None.

    The low-level server stores the request in a ContextVar (the same one
    FastMCP's ``get_context()`` uses). None covers every failure mode — no
    request in scope (a direct unit-test call), a transport with no HTTP
    request, no headers — because identity injection must never be the reason
    a tool stops working.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx

        request = getattr(request_ctx.get(), "request", None)
        return getattr(request, "headers", None) or None
    except LookupError:
        return None
    except Exception as e:  # noqa: BLE001 — identity is never flow control
        logger.debug(f"[mcp-identity] could not read request headers: {e}")
        return None


def _explicit_header(headers, name: str) -> Optional[str]:
    """One platform header, tolerating either casing. Empty → None."""
    value = (headers.get(name.lower()) or headers.get(name) or "").strip()
    return value or None


def _bearer(headers) -> BearerIdentity:
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return _parse_bearer(auth)


def caller_agent_id_from_request() -> Optional[str]:
    """The caller's agent_id as injected by the platform, or None."""
    headers = _ambient_headers()
    if headers is None:
        return None
    try:
        injected = _explicit_header(headers, AGENT_ID_HEADER)
        if injected and not is_placeholder_agent_id(injected):
            return injected

        # Codex path: identity rides a borrowed bearer.
        candidate = _bearer(headers).agent_id
        if candidate and not is_placeholder_agent_id(candidate):
            return candidate
    except Exception as e:  # noqa: BLE001 — identity is never flow control
        logger.debug(f"[mcp-identity] could not read caller identity: {e}")
    return None


def caller_user_id_from_request() -> Optional[str]:
    """The turn owner's user_id as injected by the platform, or None."""
    headers = _ambient_headers()
    if headers is None:
        return None
    try:
        injected = _explicit_header(headers, USER_ID_HEADER)
        if injected and not is_placeholder_user_id(injected):
            return injected

        # Codex path: everything rides the borrowed bearer.
        candidate = _bearer(headers).user_id
        if candidate and not is_placeholder_user_id(candidate):
            return candidate
    except Exception as e:  # noqa: BLE001 — identity is never flow control
        logger.debug(f"[mcp-identity] could not read caller user: {e}")
    return None


def resolve_caller_user_id(supplied: Any) -> Any:
    """The user_id a tool should actually use.

    DELIBERATELY weaker than :func:`resolve_caller_agent_id` — the two
    identities do not share a trust model:

    - placeholder string → injected turn owner (the wrong-user job_create
      would otherwise succeed under a phantom user and the owner's Jobs
      list stays empty while the agent reports success);
    - ``None`` → returned untouched, injection must never scope a
      legitimately unfiltered query;
    - a mismatching REAL value → KEPT, warning-logged. The platform knows
      which agent it launched (one truth), but a different user_id can be
      legitimate in multi-user flows; the warning is the measurement that
      decides whether overriding is ever justified (PR #230 discipline:
      measure before you police).
    """
    if supplied is None:
        return None

    if not is_placeholder_user_id(supplied):
        injected = caller_user_id_from_request()
        if injected is not None and supplied != injected:
            logger.warning(
                f"[mcp-identity] user_id={supplied!r} does not match the turn "
                f"owner ({injected!r}) — keeping the supplied value (measured, "
                f"not policed)"
            )
        return supplied

    injected = caller_user_id_from_request()
    if injected is None:
        return supplied
    logger.info(
        f"[mcp-identity] user_id={supplied!r} is a placeholder — "
        f"using the injected turn owner instead"
    )
    return injected


def resolve_caller_agent_id(supplied: Any) -> Any:
    """The agent_id a tool should actually use.

    Precedence: injected identity (authoritative — the platform knows who
    it launched) > the supplied parameter. Returns ``supplied`` untouched
    when nothing was injected, so behaviour without injection is unchanged.
    """
    injected = caller_agent_id_from_request()
    if not injected:
        return supplied

    if is_placeholder_agent_id(supplied):
        # The exact P1 failure: model guessed, platform knows better.
        logger.info(
            f"[mcp-identity] agent_id={supplied!r} is a placeholder — "
            f"using the injected caller identity instead"
        )
        return injected

    if supplied != injected:
        # Not necessarily an attack: a model can echo a teammate's id it saw
        # in its Known Agents list. The injected value is the truth either
        # way, and this line is the audit trail (L2 signal, incident lesson
        # #5: prefer a durable record over a silent correction).
        logger.warning(
            f"[mcp-identity] agent_id={supplied!r} does not match the caller "
            f"({injected!r}) — using the caller's own id"
        )
        return injected

    return supplied


def install_caller_identity(mcp) -> None:
    """Make every already-registered tool on ``mcp`` resolve its own caller.

    Called once per module at the end of ``create_*_mcp_server`` — that is
    ~14 one-line edits instead of ~90 tool bodies, and a new tool inherits
    the behaviour for free simply by declaring ``agent_id`` (there is no
    per-tool step for anyone to forget).

    Only tools that declare an ``agent_id`` or ``user_id`` parameter are
    wrapped; everything else is left strictly alone.
    """
    try:
        tools = mcp._tool_manager.list_tools()
    except Exception as e:  # noqa: BLE001 — never break server startup
        logger.warning(f"[mcp-identity] cannot enumerate tools, skipping: {e}")
        return

    wrapped = 0
    for tool in tools:
        fn = getattr(tool, "fn", None)
        if fn is None or getattr(fn, "_nx_identity_wrapped", False):
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        if "agent_id" not in params and "user_id" not in params:
            continue

        tool.fn = _wrap_fn(fn)
        wrapped += 1

    if wrapped:
        logger.debug(
            f"[mcp-identity] caller-identity resolution installed on "
            f"{wrapped} tool(s) of {getattr(mcp, 'name', '?')}"
        )


def _annotation_is_dict(annotation: Any) -> bool:
    """True when a return annotation denotes a dict, type or string form.

    Handles ``dict``, ``dict[str, Any]``, ``Dict[str, Any]`` and every one of
    those as a STRING (postponed evaluation). ``inspect.Signature.empty``
    and anything unrecognised → False (treated as text-returning).
    """
    if annotation is inspect.Signature.empty:
        return False
    if annotation is dict or getattr(annotation, "__origin__", None) is dict:
        return True
    text = (annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")).strip()
    return text.lower().startswith("dict")


def _wrap_fn(fn: Callable) -> Callable:
    """Wrap one tool fn so caller identity is resolved before the body runs.

    Declared ``agent_id`` and ``user_id`` parameters follow their respective
    resolution disciplines; tools declaring only one are supported too.

    Preserves the signature (``functools.wraps`` plus an explicit
    ``__signature__``) because FastMCP has already built this tool's JSON
    schema from it — the schema and the callable must keep agreeing.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn

    # Shape the guard's answer like the tool's own: FastMCP validates a
    # tool's result against the schema built from its return annotation, so
    # handing a dict-returning tool a bare string would trade one confusing
    # failure for another.
    #
    # The annotation may be a real type OR a string: several tool modules use
    # `from __future__ import annotations` (message_bus_module among them —
    # the A2A module most central to this bug), which makes every annotation
    # a string. Normalise instead of comparing to the `dict` type, or the
    # guard silently returns the wrong shape exactly where it matters most.
    returns_dict = _annotation_is_dict(sig.return_annotation)

    def _guard(supplied: Any) -> Any:
        msg = placeholder_agent_id_error(supplied)
        return {"success": False, "error": msg} if returns_dict else msg

    def _resolved_kwargs(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        # FastMCP invokes tools with keyword arguments; handle positional
        # defensively so a direct call cannot crash here.
        names = list(sig.parameters)

        # user_id first: placeholder-only resolution, and NO omitted-param
        # injection — an absent/None user_id is a legitimate "no filter"
        # value on retrieval tools, and injecting there silently scopes a
        # query that was meant to be unscoped.
        if "user_id" in kwargs:
            kwargs = {**kwargs, "user_id": resolve_caller_user_id(kwargs["user_id"])}
        elif "user_id" in names:
            u_idx = names.index("user_id")
            if u_idx < len(args):
                new_args = list(args)
                new_args[u_idx] = resolve_caller_user_id(new_args[u_idx])
                args = tuple(new_args)

        if "agent_id" in kwargs:
            kwargs = {**kwargs, "agent_id": resolve_caller_agent_id(kwargs["agent_id"])}
            return args, kwargs
        if "agent_id" in names:
            idx = names.index("agent_id")
            if idx < len(args):
                new_args = list(args)
                new_args[idx] = resolve_caller_agent_id(new_args[idx])
                return tuple(new_args), kwargs
            # Omitted entirely (an optional agent_id): inject when we can.
            injected = caller_agent_id_from_request()
            if injected is not None:
                kwargs = {**kwargs, "agent_id": injected}
        return args, kwargs

    def _still_placeholder(args: tuple, kwargs: dict) -> Any:
        """The resolved agent_id if it is still a guess, else None.

        Reached only when injection was unavailable (an adapter that drops
        headers, a direct MCP client). Answering here — instead of letting a
        lookup fail on "agent_current" — is what turns the incident's dead
        end into something the model can correct on its next tool call.
        """
        if "agent_id" in kwargs:
            value = kwargs["agent_id"]
        else:
            names = list(sig.parameters)
            idx = names.index("agent_id") if "agent_id" in names else -1
            if 0 <= idx < len(args):
                value = args[idx]
            else:
                return None
        return value if is_placeholder_agent_id(value) else None

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            args, kwargs = _resolved_kwargs(args, kwargs)
            unresolved = _still_placeholder(args, kwargs)
            if unresolved is not None:
                return _guard(unresolved)
            return await fn(*args, **kwargs)

        wrapper: Callable = async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            args, kwargs = _resolved_kwargs(args, kwargs)
            unresolved = _still_placeholder(args, kwargs)
            if unresolved is not None:
                return _guard(unresolved)
            return fn(*args, **kwargs)

        wrapper = sync_wrapper

    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper._nx_identity_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def placeholder_agent_id_error(supplied: Any, tool_hint: str = "") -> str:
    """The message a tool returns when ``agent_id`` is a guess and the
    platform could not inject the real one.

    Last line of defence: injection covers every framework we ship, but a
    future adapter (or a direct MCP client) may not carry headers, and the
    tool must not answer with a dead end the model reads as "impossible" —
    that is how the incident produced "I couldn't complete this". The
    result is a normal return VALUE, not an exception, so a model can read
    it and retry within the same turn; the text therefore states exactly
    what to do rather than only what went wrong.
    """
    return (
        f"Error: agent_id={supplied!r} is a placeholder, not a real agent id. "
        f"Your own agent_id is stated in your instructions "
        f"(\"IMPORTANT: Your agent_id is ...\") — copy that value verbatim "
        f"and call this tool again."
        + (f" {tool_hint}" if tool_hint else "")
    )


def agent_id_headers(
    agent_id: str,
    turn_source: str | None = None,
    errand_peer: str | None = None,
    errand_channel: str | None = None,
    user_id: str | None = None,
) -> dict[str, str]:
    """Headers that tell a module MCP server who is calling, and about what.

    Both identity spellings are emitted on purpose — see the module
    docstring: the claude adapter forwards the explicit header, the codex
    adapter can only forward a bearer.

    Every fact is emitted on BOTH channels — an explicit ``X-NarraNexus-*``
    header AND a positional bearer field — so codex (bearer only) is never a
    degraded consumer. Readers must still handle absence: a caller may simply
    not know its own turn source, and most turns have no errand scope at all.
    """
    # Positional bearer record; trailing unknowns are dropped, an unknown in
    # the middle stays as an empty field so later positions keep their meaning.
    fields = [agent_id, turn_source or "", errand_peer or "", errand_channel or "", user_id or ""]
    while len(fields) > 1 and not fields[-1]:
        fields.pop()
    bearer_value = BEARER_AGENT_PREFIX + BEARER_FIELD_SEP.join(fields)

    headers = {
        AGENT_ID_HEADER: agent_id,
        "Authorization": f"Bearer {bearer_value}",
    }
    for header, value in (
        (TURN_SOURCE_HEADER, turn_source),
        (ERRAND_PEER_HEADER, errand_peer),
        (ERRAND_CHANNEL_HEADER, errand_channel),
        (USER_ID_HEADER, user_id),
    ):
        if value:
            headers[header] = str(value)
    return headers


def caller_turn_source() -> Optional[str]:
    """The KIND of turn calling this tool ("chat" / "message_bus" / …), or None.

    None means "we could not tell" — no request in scope, or a caller that did
    not know its own source. Callers must degrade, never guess.
    """
    headers = _ambient_headers()
    if headers is None:
        return None
    try:
        return (
            _explicit_header(headers, TURN_SOURCE_HEADER)
            or _bearer(headers).turn_source
        )
    except Exception as e:  # noqa: BLE001 — never flow control
        logger.debug(f"[mcp-identity] could not read turn source: {e}")
        return None


def caller_errand_scope() -> tuple[Optional[str], Optional[str]]:
    """``(errand_peer, errand_channel)`` for this turn — (None, None) if none.

    A turn has an errand scope only when the platform knows this turn is
    CONTINUING the agent's own errand (MessageBusTrigger's classifier verdict).
    It exists so a single send can ask "am I aimed at that errand, or at some
    other peer whose question merely arrived in the same turn?" — the whole-turn
    answer is not good enough (see ERRAND_PEER_HEADER).
    """
    headers = _ambient_headers()
    if headers is None:
        return (None, None)
    try:
        bearer = _bearer(headers)
        return (
            _explicit_header(headers, ERRAND_PEER_HEADER) or bearer.errand_peer,
            _explicit_header(headers, ERRAND_CHANNEL_HEADER) or bearer.errand_channel,
        )
    except Exception as e:  # noqa: BLE001 — never flow control
        logger.debug(f"[mcp-identity] could not read errand scope: {e}")
        return (None, None)


__all__ = [
    "AGENT_ID_HEADER",
    "BEARER_FIELD_SEP",
    "BEARER_FIELDS",
    "BearerIdentity",
    "TURN_SOURCE_HEADER",
    "ERRAND_PEER_HEADER",
    "ERRAND_CHANNEL_HEADER",
    "USER_ID_HEADER",
    "caller_turn_source",
    "caller_errand_scope",
    "BEARER_AGENT_PREFIX",
    "PLACEHOLDER_AGENT_IDS",
    "PLACEHOLDER_USER_IDS",
    "agent_id_headers",
    "caller_agent_id_from_request",
    "caller_user_id_from_request",
    "install_caller_identity",
    "is_placeholder_agent_id",
    "is_placeholder_user_id",
    "placeholder_agent_id_error",
    "resolve_caller_agent_id",
    "resolve_caller_user_id",
]
