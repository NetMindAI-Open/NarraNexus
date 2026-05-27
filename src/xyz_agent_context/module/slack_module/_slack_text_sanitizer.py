"""
@file_name: _slack_text_sanitizer.py
@date: 2026-05-22
@description: Defensive Slack mrkdwn sanitizer.

Why this exists
---------------
Two failure modes observed in agent replies on Slack (especially with
CN agents that default to standard markdown):

  1. **GitHub markdown links** ``[text](url)`` render as LITERAL text
     in Slack because mrkdwn does not parse that syntax. Iron rule #4
     in ``slack_module.py`` instructs the LLM to use ``<URL|text>``,
     but LLMs frequently revert to the standard form.

  2. **Bare URL adjacent to CJK punctuation.** Slack's auto-linkifier
     extends a bare URL up to the next ASCII whitespace / ``<`` / ``>``.
     CJK punctuation (``，。；：、！？``) and CJK characters are
     non-ASCII so they get absorbed into the URL, producing a single
     broken link::

         "访问 https://example.com，详细"
         #             ^^^^^^^^^^^^^^^^^^^^^ all becomes one URL → 404

Where it runs
-------------
Inside ``SlackSDKClient`` at the SDK boundary — both ``send_message``
(direct path) and ``api_call`` (the ``slack_cli`` MCP dispatcher) call
this on outbound ``text`` for the message-posting methods. That way the
fix applies regardless of which path the agent chose, including
``send_message_to_user_directly`` → ``send_to_agent`` cross-channel
delivery.

Contract
--------
- Pure function; no I/O, no side effects.
- Idempotent: ``f(f(x)) == f(x)``.
- Conservative: only wraps bare URLs when there's an actual CJK-
  adjacency bug to fix. URLs in pure ASCII context are left to Slack's
  native auto-linker (which handles them correctly).
- Code blocks (triple backticks and inline backticks) are passed
  through verbatim — they often contain markdown-looking syntax and
  URLs that should NOT be reformatted.
- Existing Slack entities (``<url>``, ``<url|text>``, ``<@U...>``,
  ``<#C...>``, ``<!subteam^...>``) are preserved unchanged.
"""

from __future__ import annotations

import re

# ``[text](url)`` — GitHub-style markdown link. Text can't contain ``]``
# or a newline; URL is http(s) and stops at whitespace or closing
# paren.
_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)"
)

# Bare http(s) URL that is FOLLOWED BY a non-ASCII character — the
# only case we need to fix. CJK chars are outside ``[\x00-\x7F]`` so
# this catches CJK punctuation, fullwidth forms, kana, hangul, and
# CJK ideographs alike. Negative lookbehind ``(?<![<|])`` prevents
# matching URLs already inside ``<url>`` or ``<url|text>``.
_BARE_URL_NEXT_TO_NON_ASCII_RE = re.compile(
    r"(?<![<|])(https?://[A-Za-z0-9\-._~:/?#@!$&'*+;=%\[\]]+)(?=[^\x00-\x7F])"
)

# Slack entities that must be left alone. Inside ``<...>`` Slack accepts
# only a small prefix set: http(s)://, mailto:, @ (user), # (channel),
# ! (special / subteam / date). Matching this set lets us pass
# ``<2 < 3>`` (math) through as literal text without false positives.
_EXISTING_ENTITY_RE = re.compile(
    r"<(?:https?://|mailto:|@|#|!)[^>]*>"
)

# Code segmentation. ``re.split`` with a captured group keeps the
# code segments at odd indices so we can pass them through.
_CODE_SEGMENT_RE = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)


def sanitize_slack_mrkdwn(text: str) -> str:
    """Return ``text`` rewritten for safe Slack mrkdwn rendering.

    See module docstring for the failure modes addressed and the
    contract guarantees.
    """
    if not text:
        return text

    parts = _CODE_SEGMENT_RE.split(text)
    out: list[str] = []
    for idx, part in enumerate(parts):
        # Odd indices are the matched code segments → passthrough.
        if idx % 2 == 1:
            out.append(part)
        else:
            out.append(_rewrite(part))
    return "".join(out)


def _rewrite(segment: str) -> str:
    # 1. GitHub markdown links → mrkdwn links. After this step, every
    #    previously-buggy link is now a valid ``<url|text>`` entity.
    segment = _MARKDOWN_LINK_RE.sub(r"<\2|\1>", segment)

    # 2. Mask all existing Slack entities (including the ones just
    #    created in step 1) so step 3's bare-URL pass doesn't reach
    #    into them.
    masked: list[str] = []

    def _mask(m: re.Match[str]) -> str:
        masked.append(m.group(0))
        # NUL chars are forbidden in Slack messages anyway, so using
        # them as a sentinel is collision-proof.
        return f"\x00M{len(masked) - 1}\x00"

    segment = _EXISTING_ENTITY_RE.sub(_mask, segment)

    # 3. Wrap bare URLs that sit immediately before a non-ASCII char.
    segment = _BARE_URL_NEXT_TO_NON_ASCII_RE.sub(
        lambda m: f"<{m.group(1)}>", segment
    )

    # 4. Restore the masked entities.
    for i, entity in enumerate(masked):
        segment = segment.replace(f"\x00M{i}\x00", entity)
    return segment
