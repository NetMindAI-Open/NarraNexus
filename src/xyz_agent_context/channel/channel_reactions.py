"""
@file_name: channel_reactions.py
@author: NarraNexus
@date: 2026-07-10
@description: Shared "agent reacts to the user's message" vocabulary + helpers.

The unified ``react_to_user_message`` MCP tool exists on every IM channel module,
and every IM channel's trigger injects the same "ack early" directive into the
per-turn input via ``ChannelTriggerBase._early_feedback_prefix``.
The ONLY genuinely per-channel part is the semantic→platform-token map; the tool
body (resolve → react → best-effort envelope) and the instruction template are
identical, so they live here — in the ``channel/`` package, a legal shared
dependency of every module (same seam as ``channel_trigger_base``), so this does
not violate rule #3 (modules never import each other).

Add a new "task mood": one entry in ``REACTION_VOCABULARY`` + one line in each
module's own semantic→token map. The prompt menu and the tool body update
automatically.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, Optional

from loguru import logger


# The shared "task mood" vocabulary the agent picks from. Each IM module maps
# every name to a token valid on its platform; unknown/invalid → DEFAULT_REACTION.
REACTION_VOCABULARY: tuple[str, ...] = (
    "on_it", "searching", "done", "celebrate", "thumbs_up",
    "heart", "thanks", "applause", "hundred", "warning", "problem",
)

DEFAULT_REACTION = "on_it"


def reaction_menu() -> str:
    """The vocabulary as a ``/``-joined menu string for the prompt."""
    return "/".join(REACTION_VOCABULARY)


async def best_effort_react(
    mapping: Dict[str, str],
    emoji: str,
    react: Callable[[str], Awaitable[object]],
    *,
    log_label: str,
) -> dict:
    """Shared react-tool body: map the semantic ``emoji`` to the platform token
    (unknown → ``on_it``), call ``react(token)``, return a best-effort envelope.

    Never raises — a reaction failure (missing scope, network, deleted message)
    is logged (lesson #3: don't silently swallow) AND returned as
    ``{"success": false, "reason": ...}`` so it never breaks the agent turn.
    """
    token = mapping.get(emoji, mapping[DEFAULT_REACTION])
    try:
        await react(token)
        return {"success": True, "emoji": emoji}
    except Exception as e:  # noqa: BLE001 — best-effort, never break the turn
        logger.warning(
            f"[{log_label}] react_to_user_message failed (emoji={emoji}): "
            f"{type(e).__name__}: {e}"
        )
        return {"success": False, "reason": f"{type(e).__name__}: {e}"}


def render_early_feedback(
    *,
    tool_ref: Optional[str],
    room_id: str,
    message_id: str,
    inline: bool = False,
) -> str:
    """Render the generic "ack early on IM" directive.

    Injected into the per-turn input by ``ChannelTriggerBase._early_feedback_prefix``
    (prepended to the tagged prompt, right after the channel tag) — NOT in
    ``get_instructions`` / the system prompt.

    ``tool_ref``: the react tool name to show (fully-qualified for Lark to match
    its ``mcp__lark_module__…`` convention, bare for the others), or ``None`` for
    a channel with no reaction API (WeChat) — then the ack is message-only.
    ``inline=True`` returns a ``**Early feedback**: …`` line (used by the trigger
    prefix); ``False`` returns a ``### Early feedback`` section.
    """
    if tool_ref:
        how = (
            f"either react to the sender's message with `{tool_ref}(agent_id, "
            f'room_id="{room_id}", message_id="{message_id}", emoji="on_it")`, or '
            'send a quick "on it, one moment"'
        )
        tail = f" (emoji options: {reaction_menu()})"
    else:
        how = 'send a quick "on it, one moment" message'
        tail = ""
    body = (
        "For any request that needs more than a one-line answer, ACK FIRST, THEN "
        f"do the work — {how}. Skip it only for trivial one-line replies.{tail}"
    )
    if inline:
        return f"**Early feedback**: {body}\n"
    return f"\n### Early feedback\n\n{body}\n"
