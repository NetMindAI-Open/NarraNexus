---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/__init__.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 导出 CREATE_AGENT_EMPTY_NAME_MSG

建 agent 的空名拒绝串进公共导出面,[[store.py]](DirectStore)与
[[social_network.py]](Http 孪生)从这里同引一份,保持 byte-parity。纯转发。

# social_network_module/__init__.py — package surface

Re-exports the SocialNetworkModule package's public symbols. Beyond the module
class it also surfaces the small shared helpers the AgentDataStore seam and the
backend `/social-network/*` routes both import so the two migration paths stay
byte-identical (single source, no drift):

- `social_instance_not_found_msg` — the one canonical "agent has no
  SocialNetworkModule instance" string (PR-4).
- `format_contact_result` / `format_stats_result` — the get_contact_info /
  get_agent_social_stats tools' result-shaping, hoisted here so DirectStore and
  the read twin routes share it (PR-5).
- `format_create_agent_success` / `CREATE_AGENT_NO_OWNER_MSG` — create_agent's
  success dict (with provisioning warnings surfaced) and no-owner message, shared
  by DirectStore and the create-agent route (PR-6).

See [[social_network_module]] for where these live and why.
