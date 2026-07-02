---
code_file: src/xyz_agent_context/module/discord_module/discord_sdk_client.py
stub: false
last_verified: 2026-06-24
---

> Also exposes ``create_dm_channel(user_id)`` (POST /users/@me/channels →
> DM channel id, backs ``discord_dm``), ``list_guilds()`` and
> ``list_guild_channels(guild_id)`` (back ``discord_list_channels``).

## Why it exists

The ONLY Discord-channel file that talks to the Discord REST API. Thin
aiohttp wrapper for the calls Discord code needs OUTSIDE a live Gateway
session: bind-time auth (``get_bot_user``), sending (``send_message`` /
``create_reply``), context history (``get_channel_messages``), sender
name fallback (``get_user``), attachment download (``download_url``).

## Design decisions

- **REST-over-aiohttp instead of discord.py.** discord.py is
  Gateway-first; its high-level helpers assume a live WebSocket. The
  send / bind / history paths all run without one, so a full Gateway
  connect per message would be wasteful. Mirrors how the Telegram module
  rolls its own httpx client. discord.py stays confined to the trigger.
- **2000-char splitting lives behind this client.** ``send_message`` /
  ``create_reply`` route through ``split_discord_message`` so the
  platform cap can't drift between call sites; ``create_reply`` only
  references the original message on the FIRST chunk so the reply arrow
  points correctly and continuations post as plain follow-ups.
- **Stable ``DiscordSDKError.code`` strings** (``unauthorized`` /
  ``forbidden`` / ``not_found`` / ``rate_limited`` / ``http_<status>`` /
  ``oversized``) let callers branch without parsing messages.
  ``PERMANENT_AUTH_CODES`` is intentionally narrow (just ``unauthorized``)
  so a transient blip never disables a healthy credential.
- **``download_url`` sends no auth header.** Discord CDN attachment URLs
  are public (signed ``ex``/``is``/``hm`` query). Byte cap enforced per
  64 KB chunk so a hostile/mis-sized file can't OOM the worker.
- **``trust_env=True``** on every session so HTTPS_PROXY / NO_PROXY are
  honoured (CN devs reaching discord.com through a relay).

## Upstream / downstream

- **Upstream**: ``aiohttp``; ``split_discord_message`` from
  ``_discord_text_sanitizer``.
- **Downstream**: used by ``discord_module`` (sender), ``discord_trigger``
  (download + name fallback), ``discord_context_builder`` (history),
  ``_discord_credential_manager`` / ``_discord_service`` (auth), and the
  MCP tools (send/reply/read).

## Gotchas

- A fresh ``aiohttp.ClientSession`` is created per call (no pooling) —
  fine for current volume; revisit if throughput pain shows up.
- Discord rate-limits (429) surface as ``rate_limited`` with no retry —
  callers should not hammer.
- ``send_message`` / ``create_reply`` skip **whitespace-only** chunks
  (``not chunk.strip()``), not just truly-empty ones — Discord renders a
  whitespace body as a blank message. This is the last-line guard against
  posting a blank reply; the MCP tools also reject whitespace-only text
  upstream so the agent gets a clear error instead of a silent no-send.
