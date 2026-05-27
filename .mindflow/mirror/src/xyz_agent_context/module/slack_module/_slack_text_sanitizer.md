---
code_file: src/xyz_agent_context/module/slack_module/_slack_text_sanitizer.py
stub: false
last_verified: 2026-05-22
---

## Why it exists

Defensive normalization at the Slack SDK boundary. LLMs (especially
CN-locale ones writing to a Slack workspace) produce two link-format
patterns that look fine in standard markdown but are broken in Slack
mrkdwn:

1. ``[text](url)`` — GitHub markdown. Slack does NOT parse this; the
   reader sees literal ``[text](url)`` and the link is uncllickable.
2. Bare URL adjacent to CJK punctuation — Slack's auto-linkifier
   extends a bare URL until ASCII whitespace or ``<``/``>``. CJK
   punct (``，。；：``) is non-ASCII so it gets absorbed into the URL.
   ``"访问 https://example.com，详细"`` becomes one broken URL.

We already prompt the agent to use ``<URL|text>`` (iron rule #4 in
``slack_module.py``) but LLMs ignore that fairly often. Prompt-only
defence is unreliable; this sanitiser runs unconditionally on the
wire so the bug can't reach the user.

## Design decisions

- **Pure function, no I/O.** Called from ``SlackSDKClient`` for every
  text-bearing call (``chat.postMessage``, ``chat.update``,
  ``chat.postEphemeral``, ``chat.scheduleMessage``, ``chat.meMessage``).
  Idempotent so running it twice is safe.
- **Conservative on bare URLs.** We only wrap a bare URL when it's
  immediately followed by a non-ASCII char (the actual buggy case).
  ASCII-context URLs are left alone — Slack's native auto-linker
  handles those correctly, and wrapping them would visually churn
  every message for no benefit.
- **Code blocks pass through untouched.** Triple-backtick fences and
  inline backticks frequently contain markdown-looking syntax and
  URLs that must not be reformatted (think: code snippets, regexes,
  curl commands).
- **Existing Slack entities are masked, not re-parsed.** ``<url>``,
  ``<url|text>``, ``<@U...>``, ``<#C...>``, ``<!subteam^...>`` get
  swapped for a NUL-delimited sentinel before the bare-URL pass and
  restored afterwards. Avoids double-wrapping and avoids matching
  things like ``<2 < 3>`` (math).
- **Markdown conversion runs first, masking second.** This way a
  ``[text](url)`` produced by the LLM becomes ``<url|text>`` and is
  masked from the bare-URL pass in the same run.

## Upstream / downstream

- **Upstream**: ``slack_sdk_client.SlackSDKClient.send_message``
  (direct path, used by ``send_to_agent`` cross-channel delivery and
  by the trigger's reply fallback) and ``api_call`` (the agent-facing
  ``slack_cli`` MCP dispatcher). Both call ``sanitize_slack_mrkdwn``
  on outbound ``text``.
- **Downstream**: nothing — it's a leaf utility.

## Gotchas

- The sanitiser only inspects ``text``. ``blocks`` (Block Kit JSON) is
  rendered as the canonical Slack body when present and bypasses
  mrkdwn parsing entirely — there's nothing to fix there. Agents that
  ship ``blocks`` carrying broken inline links would need a separate
  walker.
- CJK detection is by ``[^\x00-\x7F]`` (any non-ASCII). This catches
  emoji too — wrapping a URL before an emoji is harmless (Slack
  treats ``<url>`` followed by emoji exactly like a bare URL followed
  by emoji in display).
- The regex URL char set excludes ``,`` even though RFC 3986 allows
  it as a sub-delim. Real-world URLs rarely contain commas, and
  including the comma would let the URL match consume a sentence
  comma like ``See https://x.com, then…``.
