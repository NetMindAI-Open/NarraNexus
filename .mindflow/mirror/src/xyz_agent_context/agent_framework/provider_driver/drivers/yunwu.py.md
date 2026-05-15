---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/yunwu.py
last_verified: 2026-05-13
stub: false
---

# yunwu.py — Yunwu aggregator one-key card

Same dual-row pattern as ``netmind.py``: one anthropic row + one
openai row sharing a ``linked_group``. Differs only in base_url and
auth_type values (Yunwu uses api_key for both protocols, unlike
NetMind's bearer_token-for-anthropic quirk).

Aggregator semantics: ``supports_anthropic_server_tools=False``.
