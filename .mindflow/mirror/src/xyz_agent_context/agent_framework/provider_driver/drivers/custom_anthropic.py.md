---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/custom_anthropic.py
last_verified: 2026-05-13
stub: false
---

# custom_anthropic.py — user-configured Anthropic provider

Anything ``source='user'`` + ``protocol='anthropic'``. Maps the card's
api_key + base_url + auth_type straight into a ClaudeConfig. The
``supports_anthropic_server_tools`` flag flows through from the
ProviderCard — the tool-policy hook reads it elsewhere to allow/deny
WebSearch.

Agent slot only — ``build_openai_config`` / ``build_embedding_config``
raise NotImplementedError from ``_DriverBase``.
