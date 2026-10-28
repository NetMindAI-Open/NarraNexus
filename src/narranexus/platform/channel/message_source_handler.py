"""
@file_name: message_source_handler.py
@author: Bin Liang
@date: 2026-05-11
@description: Per-source dispatch table for chat-history processing — a call-time view over the plugin registries, not a global anyone can write at import.

Each ``WorkingSource`` value (``chat``, ``lark``, ``message_bus``, ``job``,
``a2a``, ``callback``, ``skill_study``, future channels …) resolves to one
``MessageSourceHandler`` that answers two questions for the chat-history
pipeline:

  1. Write-side  — "Did the agent reply to the user this turn via this
     source's tools?" (``is_user_reply_tool`` / ``extract_reply_text``)
  2. Read-side   — "How should this stored row be labelled to the LLM?"
     (``format_row_prefix``)

Where the answers come from
===========================
Two registries, read at call time and cached on their name tuples (the shape
``module_system/channel_trigger_map.py`` and ``data_access/channel_store.py``
use):

* ``ingress.channels`` — every ``ChannelDescriptor``. A channel declares its
  message source as FIELDS of the descriptor it already ships
  (``reply_tools`` / ``row_prefix_template`` / ``reply_extractor_ref`` /
  ``dedicated_trigger``); ``ChannelDescriptor.message_source`` projects them.
  A channel is therefore still ONE record, and a channel excluded from a
  distribution or disabled in ``registry.json`` takes its handler with it.
* ``ingress.message_sources`` — ``MessageSourceSpec`` entries for sources that
  are NOT channels: the message bus and the job clock, contributed by
  ``builtin.message_bus`` / ``builtin.job`` from their own ``contribution.py``.

Until 2026-09-07 this was a class-level dict written by nine module-level
``MessageSourceRegistry.register(...)`` calls, each wrapped in
``except ValueError: pass``. That table had no owner, never appeared in the
slot tree, ignored ``builtin_overrides`` and distributions, and its answer
depended on whether anything had happened to import the channel's module in
this process — a Lark turn resolving the default handler recorded a delivered
reply as NO-REPLY, silently. The two registries above are now the only way in.

The one remaining fallback
==========================
``_DEFAULT_HANDLER`` answers the genuinely source-less sources listed in
``SOURCELESS_SOURCES`` (owner chat, ``a2a``, ``callback``, ``skill_study``):
they introduce no reply tool of their own and the default behaviour is exactly
right for them. Anything else landing on the default is a signal, not a
default: a name that IS a registered channel is logged at WARNING (once per
name) because it means a channel shipped without its message-source fields.

Why a registry instead of ``if working_source == "lark": ...``
- Iron rule #3 (modules independent): chat_module / context_runtime must not
  import lark_module or message_bus.
- Iron rule #4 (generic vs scenario-specific separated): per-source knowledge
  lives with its source's plugin, generic dispatch lives here.
- Easy to extend: a new IM channel ships four descriptor fields and zero
  changes here.
- Easy to debug: ``MessageSourceRegistry.dump()`` shows the full table.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from loguru import logger

from narranexus.contracts.channel import MessageSourceSpec


# OpenAI Responses-API "citation" tokens that the model emits inline
# in user-facing text when WebSearch ran. Examples observed in the
# wild (2026-06-08, gpt-5.5 via codex): ``citeturn6view0``,
# ``citeturn2news12``, ``citeturn7search9``.
#
# ChatGPT's first-party frontend resolves these into clickable
# Markdown links via a separate annotation table — but the
# ``openai-codex`` Python SDK 0.1.0b3 doesn't surface that table
# (``OutputTextContentItem`` carries just ``{text, type}``). Without
# the URL/title map we can't render proper links; the pragmatic fix
# is to strip the tokens so users see clean prose instead of
# literal cryptic markers glued to sentence ends.
#
# Strip lives HERE (the single reply-extraction chokepoint shared by
# every channel) rather than in any per-framework translator,
# because:
#  * The tokens come from the model's text written into the
#    ``content`` argument of an owner-facing tool
#    (or any other reply tool) — they're plain string content, not
#    SDK-protocol metadata. Stripping at the SDK boundary would
#    miss tokens that the model writes into ``lark_cli`` markdown,
#    ``slack_cli`` markdown, ``tg_cli``, etc.
#  * Every channel funnels its reply through this method, so one
#    strip here covers all of them.
#
# Regex requires TWO alpha+digit cycles after ``cite`` to avoid
# false-matching the English word "cite" followed by a noun.
_CITE_TOKEN_RE = re.compile(r"cite[a-z]+\d+[a-z]+\d+")


def strip_responses_api_citation_tokens(text: str) -> str:
    """Public alias — same as ``_strip_responses_api_citation_tokens``,
    re-exported without the leading underscore so callers outside this
    module (notably ``response_processor`` building ProgressMessages
    for live UI streaming) can apply the same strip. Kept on the
    underscore name too for backwards compat with the internal call
    site below."""
    return _strip_responses_api_citation_tokens(text)


def _strip_responses_api_citation_tokens(text: str) -> str:
    """Remove inline citation tokens and tidy up the leftover spacing.

    Returns ``text`` unchanged if no token is present (fast path);
    otherwise strips every token and collapses doubled spaces / fixes
    spaces-before-punct that the strip introduces.
    """
    if not text or "cite" not in text:
        return text
    cleaned = _CITE_TOKEN_RE.sub("", text)
    if cleaned == text:
        return text
    # Tighten up artifacts the strip itself produced.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # Strip whitespace that's now ahead of punctuation (Chinese +
    # English punctuation kept together so this is i18n-safe).
    cleaned = re.sub(r"\s+([。，；、,.;])", r"\1", cleaned)
    # Strip trailing horizontal whitespace on every line (the strip
    # often leaves a token-shaped hole at end-of-paragraph that the
    # ahead-of-punct rule above doesn't catch because there's no
    # punctuation after it).
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    return cleaned


ReplyExtractor = Callable[[str, Dict[str, Any]], Optional[str]]
"""Custom extractor: given (tool_name, arguments) from a ProgressMessage,
return the user-visible reply text if this tool call sent something to
the user, else None. Channels with non-standard reply tools (e.g. Lark's
`lark_cli` whose payload sits inside `command`'s `--markdown` flag) ship
their own extractor; everyone else uses the default substring + `content`
arg fallback."""


PLATFORM_REPLY_TEXT_KEY = "_platform_reply_text"
"""Argument key carrying reply text the PLATFORM wrote and delivered.

Set by ``step_3``'s IM DM no-reply fallback, which sends through
``ChannelSenderRegistry`` instead of the model calling the channel's tool.
The resulting synthetic frame therefore doesn't match the tool's real
argument shape, and every channel-specific extractor would mis-read it —
so ``extract_reply_text`` honours this key before anything else. Leading
underscore because it is ours, not part of any channel's tool schema.
"""


class _SafeFormatDict(dict):
    """A dict that returns an empty string for missing keys instead of
    raising KeyError, so a row that's missing channel_tag fields still
    renders a sensible prefix instead of crashing the whole prompt."""

    def __missing__(self, key: str) -> str:
        return ""


@dataclass(frozen=True)
class MessageSourceHandler:
    """Per-WorkingSource hook for the chat-history pipeline.

    See module docstring for the design rationale. This class only
    holds configuration + pure helpers — no I/O, no async, no DB.
    """

    name: str
    """Matches the `WorkingSource` enum value, e.g. `lark`, `chat`."""

    user_reply_tool_names: Tuple[str, ...]
    """Substrings of `tool_name` that count as the agent replying to
    the user via this source. Substring match (not equality) so MCP
    prefixes like `mcp__chat_module__notify_owner`
    match the short name registered here."""

    owner_visible_reply_tool_names: Optional[Tuple[str, ...]] = None
    """The subset of reply tools whose output SURFACES IN THE OWNER'S
    WEB CHAT. None (the default) means "same as user_reply_tool_names"
    — correct for chat and the IM channels, where the conversation IS
    with the owner. Sources whose reply target is somebody else (the
    bus: replies go to peer AGENTS) override this to just the
    owner-notify tool, so "delivered to whoever contacted you" (metrics,
    NO-REPLY judgment) and "visible to the owner" (session anchor,
    chat-history persistence) stay two separate questions — conflating
    them let every agent-to-agent bus reply re-anchor the owner's
    session (PR #230 review)."""

    display_label: str = ""
    """Human-readable name for this source, for the one line of prompt that
    tells the agent where the turn came from (`render_origin_declaration`).
    Empty falls back to `name.title()`, which is right for every brand-named
    channel and wrong only where the source name is a platform-internal word —
    those set it explicitly.

    Why the label lives HERE and not in each trigger's prose: the prose used to
    say it, each copy in its own words, and the copies drifted. One field, one
    renderer, and the same registry entry that decides which tools count as a
    reply — so the sentence and the enforcement cannot disagree."""

    row_prefix_template: str = "[{name}]"
    """str.format-style template applied to a flattened
    `{**meta_data, **channel_tag}` dict at render time. Missing keys
    substitute to "" (see _SafeFormatDict) so legacy rows without
    `channel_tag` still render without raising."""

    extract_reply_fn: Optional[ReplyExtractor] = None
    """Optional per-channel reply extractor. When set, completely
    overrides the default substring match + `arguments['content']`
    fallback. Used for channels where the reply text isn't in a
    `content` argument (Lark stuffs it into `command`'s `--markdown`
    flag, for example)."""

    dedicated_trigger: bool = False
    """True when this source has its own long-running trigger process
    (LarkTrigger, WeChatTrigger, ...) that already runs AgentRuntime for
    every inbound message. Until 2026-08-17 `ChannelInboxWriter` mirrored those
    turns into ``bus_messages`` under ``{name}_{chat_id}`` for history/Inbox
    display; the inbox has its own tables now and nothing writes them, but the
    rows survive on deployed databases. `im_channel_prefixes()` derives the
    channel-id prefixes from this flag for two consumers: MessageBusTrigger must
    NOT re-dispatch them (a second run sends duplicate replies — 2026-07-03
    wechat double-dispatch incident) and `LocalMessageBus._unread_predicate` must not
    inject them into agent context. Every module
    that ships a ``run_*_trigger.py`` entrypoint must set this; enforced
    by tests/message_bus/test_bus_channel_inbox_skip.py."""

    @property
    def label(self) -> str:
        """`display_label` with the derive-from-name fallback applied."""
        return self.display_label or self.name.replace("_", " ").title()

    def is_user_reply_tool(self, tool_name: str) -> bool:
        """True when `tool_name` matches any registered reply tool.

        Kept as a public helper for callers that only need the binary
        match (e.g. tests, debug tooling). The primary extraction path
        is `extract_reply_text`, which also pulls the actual content."""
        if not tool_name:
            return False
        return any(pat in tool_name for pat in self.user_reply_tool_names)

    @property
    def effective_owner_visible_names(self) -> Tuple[str, ...]:
        """The owner-visible list after the None-fallback — the single
        place the fallback rule lives, so log lines and matching can
        never drift apart."""
        if self.owner_visible_reply_tool_names is None:
            return self.user_reply_tool_names
        return self.owner_visible_reply_tool_names

    def is_owner_visible_reply_tool(self, tool_name: str) -> bool:
        """True when `tool_name`'s output surfaces in the owner's web
        chat. Falls back to the full reply list when no owner-visible
        subset is declared (chat / IM channels)."""
        if not tool_name:
            return False
        return any(pat in tool_name for pat in self.effective_owner_visible_names)

    def extract_owner_visible_text(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """`extract_reply_text`, gated on owner visibility. Consumers that
        decide what the OWNER saw (session anchor in step_4, the
        user-visible split in ChatModule) call this; consumers that ask
        "did the turn deliver to its origin at all" keep
        `extract_reply_text`."""
        if not self.is_owner_visible_reply_tool(tool_name):
            return None
        return self.extract_reply_text(tool_name, arguments)

    def extract_reply_text(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """Return the user-visible reply text from this tool call.

        Three-way contract (all falsy consumers behave identically, but
        ``_classify_event`` needs the distinction):

        - non-empty ``str`` — the reply text.
        - ``""`` — the call WAS a reply attempt, but its text stripped
          down to blank (all-citation reply, literal whitespace, or a
          missing ``content`` arg). Blank after strip = no reply.
        - ``None`` — the call wasn't a user reply at all (unmatched tool
          name, or a custom extractor rejecting e.g. a lark_cli non-send
          command). Downstream may still surface it as a real tool call.

        Custom `extract_reply_fn` short-circuits this; otherwise falls
        back to substring match on `tool_name` + `arguments['content']`.

        The extracted text is run through
        ``_strip_responses_api_citation_tokens`` regardless of which
        extractor produced it — the strip is a content-layer cleanup
        that applies uniformly to every channel (chat / lark / slack
        / telegram / job). See the module-level helper docstring for
        why we strip rather than resolve.
        """
        # Platform-written frames first. A no-reply fallback reply is
        # delivered by the platform through ChannelSenderRegistry, not by
        # the model calling the channel's tool, so the frame we synthesise
        # is NOT shaped like a real tool call — and every channel-specific
        # extractor would mis-read it: wechat's reads `arguments["text"]`
        # and would fall back to its "(sent via wechat_send)" placeholder,
        # lark's parses a `command` string and would return None (making a
        # delivered reply look like silence). The text is authoritative
        # here precisely because we wrote it.
        platform_text = (arguments or {}).get(PLATFORM_REPLY_TEXT_KEY)
        if platform_text is not None:
            text = platform_text
        elif self.extract_reply_fn is not None:
            text = self.extract_reply_fn(tool_name, arguments or {})
            if text is None:
                return None
        elif self.is_user_reply_tool(tool_name):
            text = (arguments or {}).get("content", "")
        else:
            return None
        text = _strip_responses_api_citation_tokens(text or "")
        if not text.strip():
            return ""
        return text

    def format_row_prefix(self, msg: Dict[str, Any]) -> str:
        """Render the per-row prefix for `msg`.

        Pulls placeholders from `meta_data` and `meta_data.channel_tag`
        (channel_tag wins on overlap so e.g. an inner `sender_name`
        always reflects the IM sender, not a stray meta field)."""
        meta: Dict[str, Any] = msg.get("meta_data") or {}
        ctag = meta.get("channel_tag")
        flat: Dict[str, Any] = {"name": self.name, "role": msg.get("role", "")}
        flat.update({k: v for k, v in meta.items() if not isinstance(v, (dict, list))})
        if isinstance(ctag, dict):
            flat.update({k: v for k, v in ctag.items() if not isinstance(v, (dict, list))})
        return self.row_prefix_template.format_map(_SafeFormatDict(flat))


#: The owner-facing delivery tools — one destination, two registers
#: (`reply_owner` on the owner's own chat turn, `notify_owner` everywhere else;
#: see ChatModule.expressive_tools). Lives HERE because this module already
#: has to reason about both — the default handler lists both — and because every
#: consumer of the distinction is downstream of the registry.
#:
#: Anything asking "did this reach the owner" must accept BOTH. Anything asking
#: "is this the CHANNEL's own tool" must reject both — a filter that named only
#: one silently let the other through, which is how an IM fallback frame got
#: tagged `reply_owner` and would have surfaced in the owner's chat panel as if
#: the agent had addressed them.
_OWNER_TOOL_RE = re.compile(r"(?:reply|notify)_owner$")


def is_owner_tool(tool_name: str | None) -> bool:
    """True for `reply_owner` / `notify_owner`, bare or MCP-prefixed."""
    return bool(tool_name) and _OWNER_TOOL_RE.search(tool_name or "") is not None


_DEFAULT_HANDLER = MessageSourceHandler(
    name="default",
    # BOTH owner-facing names, and that is not belt-and-braces.
    #
    # The owner's own chat turn resolves to this handler (there is no explicit
    # "chat" registration), and its desk carries `reply_owner` — while every
    # other turn carries `notify_owner`. Listing only one of them would make
    # `_has_organic_reply` blind on the surface that uses the other: a chat turn
    # that answered perfectly would read as "never spoke", and the helper-LLM
    # fallback would write a SECOND reply on top of every successful turn.
    #
    # A source that wants the two questions separated declares
    # `owner_visible_reply_tool_names` itself, as message_bus does.
    user_reply_tool_names=("reply_owner", "notify_owner"),
    # Not "Default" — the label is read by the agent, not by us. Every source
    # that lands here (owner chat, a2a, callback, skill_study) is happening
    # inside NarraNexus, which is exactly one of the two social situations the
    # harness teaches.
    display_label="NarraNexus",
    row_prefix_template="[NarraNexus UI]",
)
"""Fallback for any WorkingSource that didn't register itself.

This covers the user-facing chat trigger, A2A, callback, skill_study,
and any future trigger that does not introduce a new reply tool.
We never need to register `chat` explicitly — the default behaviour
is exactly what `chat` needs."""


SOURCELESS_SOURCES: frozenset[str] = frozenset({"chat", "a2a", "callback", "skill_study"})
"""The sources that legitimately have no registration — the platform's own table.

They introduce no reply tool and no row prefix of their own, so
``_DEFAULT_HANDLER`` IS their handler; there is nothing for a plugin to
contribute. Spelled out as data (not as "whatever is missing") so a
registered channel that lands on the default is distinguishable from these,
and can be warned about."""


def _lazy_extractor(ref: str) -> ReplyExtractor:
    """A ``ReplyExtractor`` that imports ``ref`` ("pkg.mod:function") on FIRST CALL.

    Building the view must stay as cheap as reading a registry: resolving the ref
    eagerly would import every channel's module (and its SDK) the moment anything
    asked which sources exist — the import-order coupling this file exists to end."""
    resolved: list[ReplyExtractor] = []

    def extract(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        if not resolved:
            import importlib

            module_path, _, attr = ref.partition(":")
            resolved.append(getattr(importlib.import_module(module_path), attr))
        return resolved[0](tool_name, arguments)

    return extract


def handler_from_spec(spec: MessageSourceSpec) -> MessageSourceHandler:
    """Build the runtime handler for one declared source. Pure; no imports fire here."""
    return MessageSourceHandler(
        name=spec.name,
        user_reply_tool_names=tuple(spec.reply_tools),
        owner_visible_reply_tool_names=(
            None if spec.owner_visible_reply_tools is None else tuple(spec.owner_visible_reply_tools)
        ),
        display_label=spec.display_label,
        row_prefix_template=spec.row_prefix_template,
        extract_reply_fn=_lazy_extractor(spec.reply_extractor_ref) if spec.reply_extractor_ref else None,
        dedicated_trigger=spec.dedicated_trigger,
    )


class MessageSourceView:
    """name -> handler, derived from ``ingress.channels`` + ``ingress.message_sources``.

    Rebuilt only when either registry's name tuple changes (the cache shape
    ``TriggerMapView`` / ``_ChannelSpecs`` use): ``get()`` is called several times
    per turn and once per history row rendered, so an uncached build would
    re-project every descriptor on a read path.

    Per-entry isolation, fail-closed: one descriptor that cannot be built is
    warned about once and skipped — it loses ITS handler (falls back to the
    default, and the fallback warns), never everyone else's.

    Takes an explicit ``registries`` for tests and for any host that runs a
    private ``Registries()``; ``None`` means the process kernel registries.
    """

    def __init__(self, registries: Any = None) -> None:
        self._registries = registries
        self._cache: Dict[str, MessageSourceHandler] | None = None
        self._cache_key: tuple = ()
        self._warned_default: set[str] = set()
        self._warned_broken: set[str] = set()

    def _regs(self):
        if self._registries is not None:
            return self._registries
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        return KERNEL_REGISTRIES

    def _registry(self, path: str):
        try:
            return self._regs().registry_for(path)
        except Exception:  # noqa: BLE001 — slot absent (a host with a private, partial slot tree)
            return None

    def _build(self) -> Dict[str, MessageSourceHandler]:
        channels = self._registry("ingress.channels")
        sources = self._registry("ingress.message_sources")
        key = (
            channels.names() if channels is not None else (),
            sources.names() if sources is not None else (),
        )
        if self._cache is not None and key == self._cache_key:
            return self._cache
        out: Dict[str, MessageSourceHandler] = {}
        for registry, project in ((channels, lambda d: d.message_source), (sources, lambda spec: spec)):
            if registry is None:
                continue
            for entry in registry.entries():
                try:
                    spec = project(entry.factory())
                    if not spec.reply_tools and not spec.reply_extractor_ref:
                        # Declares no message source: it has none, and the default
                        # handler answers for it (an inbound-less credential-only
                        # channel, for instance).
                        continue
                    out[spec.name] = handler_from_spec(spec)
                except Exception as e:  # noqa: BLE001 — one broken entry must not hide every source
                    if entry.name not in self._warned_broken:
                        self._warned_broken.add(entry.name)
                        logger.warning(f"message source {entry.name!r} unavailable, skipped ({type(e).__name__}: {e})")
        self._cache, self._cache_key = out, key
        return out

    def channel_names(self) -> tuple[str, ...]:
        registry = self._registry("ingress.channels")
        return registry.names() if registry is not None else ()

    def get(self, working_source: str) -> MessageSourceHandler:
        """The handler for ``working_source``; the default handler when nothing declares it.

        Never returns None — callers use the result unconditionally. A name that is
        a REGISTERED channel yet has no handler is the case the silent default used
        to hide (a delivered IM reply recorded as NO-REPLY), so it is logged at
        WARNING, once per name per process."""
        handlers = self._build()
        handler = handlers.get(working_source)
        if handler is not None:
            return handler
        if (
            working_source
            and working_source not in SOURCELESS_SOURCES
            and working_source in self.channel_names()
            and working_source not in self._warned_default
        ):
            self._warned_default.add(working_source)
            logger.warning(
                f"MessageSourceRegistry: channel {working_source!r} is registered but declares no "
                f"message source (reply_tools / reply_extractor_ref) — its replies will be judged "
                f"and its rows labelled as plain NarraNexus UI"
            )
        return _DEFAULT_HANDLER

    def handlers(self) -> Dict[str, MessageSourceHandler]:
        """Snapshot of every declared source's handler (name -> handler)."""
        return dict(self._build())

    def dump(self) -> Dict[str, Dict[str, Any]]:
        """JSON-serialisable snapshot for debug logging — drops the extractor callable,
        replacing it with a ``"<custom>" if present else None`` flag."""
        out: Dict[str, Dict[str, Any]] = {}
        for name, h in self._build().items():
            d = asdict(h)
            d["extract_reply_fn"] = "<custom>" if h.extract_reply_fn else None
            out[name] = d
        return out


_VIEW = MessageSourceView()


class MessageSourceRegistry:
    """The process-wide ``MessageSourceView`` as a namespace.

    Kept as a class so the ~15 call sites read the same as before; it holds NO
    state of its own. There is deliberately no ``register()``: a source enters
    through its plugin's contribution (``ingress.channels`` descriptor fields or
    an ``ingress.message_sources`` spec), which is what makes ownership,
    ``builtin_overrides`` and distribution excludes apply to it.
    """

    @classmethod
    def get(cls, working_source: str) -> MessageSourceHandler:
        return _VIEW.get(working_source)

    @classmethod
    def handlers(cls) -> Dict[str, MessageSourceHandler]:
        return _VIEW.handlers()

    @classmethod
    def dump(cls) -> Dict[str, Dict[str, Any]]:
        return _VIEW.dump()


# ============================================================================
# The origin declaration (design §6.1)
# ============================================================================

ORIGIN_DECLARATION_TEMPLATE = (
    "[Origin] {label} · reply with {default_tool}{others_clause}"
)


def im_channel_prefixes() -> tuple[str, ...]:
    """Channel-id prefixes owned by dedicated IM triggers — registry-driven.

    Two consumers, and they guard different things:

    * `MessageBusTrigger` must not RE-DISPATCH these channels — their own trigger
      already ran AgentRuntime for the message.
    * `LocalMessageBus._unread_predicate` must not INJECT them into agent context.

    Both are about the same rows: pre-2026-08-17 IM history that the retired
    `ChannelInboxWriter` wrote into `bus_messages` under `{channel}_{chat_id}`.
    Nothing writes them any more — the inbox has its own tables — but they
    survive on every deployed database, so this is not dead code and must not be
    deleted as such under 铁律 #2. It can retire once those rows are purged; the
    runbook that purges them says so.

    The set used to be a hand-maintained tuple ("lark_", "telegram_", "slack_")
    and it silently drifted — wechat, narramessenger and discord were missing, so
    every message on those channels fired a SECOND agent run wearing the
    Owner-Relay peer-agent prompt (2026-07-03 wechat incident: fabricated
    context_token sends + bogus "我已经在微信上回复你啦" platform DMs). Deriving
    from `dedicated_trigger` keeps a future channel covered the moment its
    descriptor lands in the registry; computed per call because the registry is
    populated at boot and this module is imported long before that.

    Lives here rather than in `message_bus_trigger` because this is where the
    view it reads lives, and because `local_bus` — a lower layer than the
    trigger — now needs it too.
    """
    return tuple(sorted(
        f"{name}_"
        for name, handler in _VIEW.handlers().items()
        if handler.dedicated_trigger
    ))


def render_origin_declaration(
    working_source: str,
    expressive_tools: "Sequence[str]",
    reply_is_plain_text: bool = False,
) -> str:
    """One line naming where this turn came from and what answers it.

    This replaced a paragraph in every trigger prompt, each restating the
    reply rule in its own words. Those restatements are what drifted: a
    channel's copy would still describe a tool the desk no longer carried, and
    the agent had two sentences to choose between with nothing to break the
    tie.

    Both halves of this line come from data the platform already computed:

    * the label from `MessageSourceRegistry` — the same registry entry that
      decides which tool calls count as a reply from this source;
    * the tools from the turn's declared expressive surface — the SAME tuple
      `expressive_tools` produced and `disallowed_tools` enforced.

    So the sentence cannot contradict the desk: there is no second copy of
    either fact to fall out of step.

    Empty tools → empty string. A turn with no declared reply surface must not
    be handed a sentence claiming one; inventing a tool name here would be the
    exact failure the declaration exists to prevent.

    ``reply_is_plain_text`` is the other empty case, and it is not derivable
    from the tools. The line's premise is that origin-first ordering puts the
    origin module's tool at position 0 — true only while the origin module
    declares something. A patrol turn declares nothing (its reply IS the agent's
    plain text, posted by the platform), so position 0 becomes whatever ranked
    next — `notify_owner`, which the registry legitimately lists as a way to
    answer a bus turn. "reply with `notify_owner`" would then tell the lead to
    message its owner instead of writing the room's status line: the wrong act,
    and the one the patrol prompt spends a paragraph forbidding.

    The registry cannot tell these apart — `notify_owner` really is one of this
    source's reply tools — so the caller passes the fact, which the platform
    already stamps as `BUS_PLAIN_TEXT_TURN_EXTRA_KEY`. Other modules keep their
    own tools on such a turn (escalating to the owner mid-sweep is legitimate);
    what must not happen is a sentence presenting one of them as how to answer.
    """
    if reply_is_plain_text:
        return ""
    tools = tuple(expressive_tools or ())
    if not tools:
        return ""
    handler = _VIEW.get(working_source)
    others = ", ".join(f"`{t}`" for t in tools[1:])
    return ORIGIN_DECLARATION_TEMPLATE.format(
        label=handler.label,
        default_tool=f"`{tools[0]}`",
        others_clause=f" (also available: {others})" if others else "",
    )
