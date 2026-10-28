---
code_file: plugins/builtin.llm_clients/src/narranexus_plugins/llm_clients/contributions.py
last_verified: 2026-09-04
stub: false
---

# builtin.llm_clients — contributions.py

The three `model.clients` contributions in registry order: `ANTHROPIC` (AnthropicHelperSDK), `OPENAI` (still the platform `adapters.openai_agents.OpenAIAgentsSDK`, loaded lazily) and `CLI` (CliHelperSDK); `CONTRIBUTIONS` is the tuple the manifest names. `helper_sdk.ensure_builtin_clients()` registers them through the kernel on first lookup — the platform never imports this module.
