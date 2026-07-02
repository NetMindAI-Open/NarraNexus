---
code_file: src/xyz_agent_context/module/discord_module/_discord_text_sanitizer.py
stub: false
last_verified: 2026-06-16
---

## Why it exists

Encodes Discord's hard 2000-character-per-message limit in one place.
``split_discord_message`` chunks long agent replies on safe boundaries so
the send path never gets rejected with ``50035`` (Invalid Form Body).

## Design decisions

- **Split, don't truncate.** Agents routinely exceed 2000 chars; dropping
  the tail would lose content. Boundary preference: paragraph break →
  line break → hard cut (a single >2000-char line, e.g. a giant URL, has
  no safe boundary).
- **No markdown rewriting.** Unlike Slack mrkdwn, Discord renders standard
  markdown, so there's nothing to translate — this file is purely about
  the length cap.
- **``[""]`` for empty input** so callers always have ≥1 chunk; guarding
  against truly-empty replies is the caller's job.

## Upstream / downstream

- **Downstream**: ``DiscordSDKClient.send_message`` / ``create_reply``
  both route through ``split_discord_message`` so the cap can't drift.

## Gotchas

- ``DISCORD_MESSAGE_LIMIT`` is a platform constant, not a tunable.
