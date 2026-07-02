"""
@file_name: _discord_text_sanitizer.py
@date: 2026-06-16
@description: Outbound text helpers for Discord messages.

Discord's markdown dialect is close enough to the GitHub-flavoured
markdown agents already emit that we do NOT rewrite it (unlike Slack,
whose mrkdwn needs `<URL|text>` link surgery). The one hard platform
constraint is the **2000-character per-message limit** — a single
``POST /channels/{id}/messages`` body whose ``content`` exceeds 2000
chars is rejected with ``50035`` (Invalid Form Body). Agents routinely
produce longer replies, so the send path splits on safe boundaries
instead of letting Discord reject the whole message.

This is the ONLY place the 2000 limit is encoded — both
``DiscordSDKClient.send_message`` and ``create_reply`` route through
``split_discord_message`` so the cap can't drift between call sites.
"""
from __future__ import annotations

# Discord rejects message content longer than this. Hard platform limit,
# not a tunable — see https://discord.com/developers/docs/resources/channel.
DISCORD_MESSAGE_LIMIT = 2000


def split_discord_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Splitting prefers, in order: paragraph breaks (``\\n\\n``), line
    breaks (``\\n``), then a hard character cut. A single line longer
    than ``limit`` (e.g. a giant URL or base64 blob) is hard-cut — there
    is no safe word boundary to honour and Discord would reject it whole
    otherwise.

    Returns ``[""]`` for empty input so callers always have at least one
    chunk to send (an empty content is itself rejected by Discord, so the
    caller is expected to guard against truly-empty replies upstream; this
    function's contract is purely "never exceed the limit").
    """
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer a paragraph break, then a line break, then a hard cut.
        cut = window.rfind("\n\n")
        if cut == -1:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip("\n"))
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
