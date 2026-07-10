"""
@file_name: message_source_handler.py
@author: Bin Liang
@date: 2026-05-11
@description: Per-source dispatch table for chat-history processing.

Each WorkingSource value (`chat`, `lark`, `message_bus`, `job`, `a2a`,
`callback`, `skill_study`, future channels …) maps to one
MessageSourceHandler that answers two questions for the chat-history
pipeline:

  1. Write-side  — "Did the agent reply to the user this turn via this
     source's tools?" (`is_user_reply_tool(tool_name)`)
  2. Read-side   — "How should this stored row be labelled to the LLM?"
     (`format_row_prefix(msg)`)

Channels that need custom behaviour (Lark recognises `lark_cli` tools,
Matrix would recognise matrix-specific tools, etc.) register their own
handler at module-load time:

    MessageSourceRegistry.register(MessageSourceHandler(
        name="lark",
        user_reply_tool_names=(
            "send_message_to_user_directly",
            "lark_cli +messages-send",
            "lark_cli +messages-reply",
        ),
        row_prefix_template="[Lark · {sender_name} in {room_name}]",
    ))

All sources that need nothing channel-specific (`chat`, `a2a`,
`callback`, `skill_study`, …) fall back to the default handler, which
recognises only `send_message_to_user_directly` and renders rows with a
"[NarraNexus UI · user=<id>]" prefix.

Why a registry instead of `if working_source == "lark": ...`
- Iron rule #3 (modules independent): chat_module / context_runtime
  must not import lark_module or message_bus.
- Iron rule #4 (generic vs scenario-specific separated): per-source
  knowledge lives with its source module, generic dispatch lives here.
- Easy to extend: a new IM trigger ships one `Registry.register(...)`
  call and zero changes elsewhere.
- Easy to debug: `MessageSourceRegistry.dump()` shows the full table.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from loguru import logger


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
#    ``content`` argument of ``send_message_to_user_directly``
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
    prefixes like `mcp__chat_module__send_message_to_user_directly`
    match the short name registered here."""

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
    every inbound message. ChannelInboxWriter persists those turns to
    ``bus_messages`` under ``{name}_{chat_id}`` purely for history/Inbox
    display; MessageBusTrigger uses this flag to derive the channel-id
    prefixes it must NOT re-dispatch (a second run would send duplicate
    replies — 2026-07-03 wechat double-dispatch incident). Every module
    that ships a ``run_*_trigger.py`` entrypoint must set this; enforced
    by tests/message_bus/test_bus_channel_inbox_skip.py."""

    def is_user_reply_tool(self, tool_name: str) -> bool:
        """True when `tool_name` matches any registered reply tool.

        Kept as a public helper for callers that only need the binary
        match (e.g. tests, debug tooling). The primary extraction path
        is `extract_reply_text`, which also pulls the actual content."""
        if not tool_name:
            return False
        return any(pat in tool_name for pat in self.user_reply_tool_names)

    def extract_reply_text(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """Return the user-visible reply text from this tool call, or
        None if the call wasn't a user reply.

        Custom `extract_reply_fn` short-circuits this; otherwise falls
        back to substring match on `tool_name` + `arguments['content']`.

        The extracted text is run through
        ``_strip_responses_api_citation_tokens`` regardless of which
        extractor produced it — the strip is a content-layer cleanup
        that applies uniformly to every channel (chat / lark / slack
        / telegram / job). See the module-level helper docstring for
        why we strip rather than resolve.
        """
        if self.extract_reply_fn is not None:
            text = self.extract_reply_fn(tool_name, arguments or {})
        elif self.is_user_reply_tool(tool_name):
            text = (arguments or {}).get("content", "")
        else:
            return None
        if not text:
            return None
        return _strip_responses_api_citation_tokens(text)

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


_DEFAULT_HANDLER = MessageSourceHandler(
    name="default",
    user_reply_tool_names=("send_message_to_user_directly",),
    row_prefix_template="[NarraNexus UI]",
)
"""Fallback for any WorkingSource that didn't register itself.

This covers the user-facing chat trigger, A2A, callback, skill_study,
and any future trigger that does not introduce a new reply tool.
We never need to register `chat` explicitly — the default behaviour
is exactly what `chat` needs."""


class MessageSourceRegistry:
    """Global registry. Channel-specific modules register one handler
    each at import/module-load time."""

    _handlers: Dict[str, MessageSourceHandler] = {}

    @classmethod
    def register(cls, handler: MessageSourceHandler) -> None:
        """Register `handler` against its `name`.

        Raises if the name is already taken — this is intentional;
        accidental duplicate registration would silently shadow another
        channel's reply detection, which is a class of bug we never
        want to debug at runtime."""
        if handler.name in cls._handlers:
            raise ValueError(
                f"duplicate MessageSourceHandler registration for {handler.name!r}"
            )
        cls._handlers[handler.name] = handler
        logger.info(
            f"MessageSourceRegistry: registered handler for '{handler.name}' "
            f"(reply tools={handler.user_reply_tool_names})"
        )

    @classmethod
    def get(cls, working_source: str) -> MessageSourceHandler:
        """Return the handler for `working_source`, falling back to the
        default handler when nothing is registered. Never returns None
        — callers can use the result unconditionally."""
        return cls._handlers.get(working_source, _DEFAULT_HANDLER)

    @classmethod
    def handlers(cls) -> Dict[str, MessageSourceHandler]:
        """Read-only snapshot of all registered handlers.

        Exists so MessageBusTrigger can derive the dedicated-trigger
        channel prefixes from registrations instead of a hand-maintained
        list (which drifted: wechat/narramessenger/discord were missing)."""
        return dict(cls._handlers)

    @classmethod
    def dump(cls) -> Dict[str, Dict[str, Any]]:
        """Snapshot of the registry for debug logging. JSON-serialisable
        — drops the (non-serialisable) extract_reply_fn callable, replaces
        it with a `"<custom>" if present else None` flag so we still see
        which handlers have custom extraction."""
        out: Dict[str, Dict[str, Any]] = {}
        for name, h in cls._handlers.items():
            d = asdict(h)
            d["extract_reply_fn"] = "<custom>" if h.extract_reply_fn else None
            out[name] = d
        return out
