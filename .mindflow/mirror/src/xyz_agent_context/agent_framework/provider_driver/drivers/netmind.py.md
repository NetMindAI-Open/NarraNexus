---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/netmind.py
last_verified: 2026-06-10
stub: false
---
## 2026-06-10 — build_anthropic_helper_config

Implements the new helper-slot builder for anthropic-protocol rows
(guarded the same way as build_claude_config). Lets this card serve
the helper_llm slot directly via the Messages-API helper.


# netmind.py — NetMind aggregator one-key card

Two ``user_providers`` rows per NetMind key — one
``protocol=anthropic`` (bearer_token, ``inference-api/anthropic``
endpoint), one ``protocol=openai`` (api_key, ``inference-api/openai/v1``
endpoint). They share a ``linked_group`` and the api_key.

The Driver instance picks the right config builder by checking the
card's own protocol. Mis-bindings (e.g. helper_llm pointing at the
anthropic row) raise loud NotImplementedError instead of silently
constructing a bogus config.

``supports_anthropic_server_tools=False`` is hardcoded — NetMind is
an aggregator, it doesn't forward Anthropic's server-side tools.
The tool-policy hook denies WebSearch upfront on this card so the
caller doesn't hang.
