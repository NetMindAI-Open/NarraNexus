---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/custom_openai.py
last_verified: 2026-05-13
stub: false
---

# custom_openai.py — user-configured OpenAI provider

Anything ``source='user'`` + ``protocol='openai'``. Serves both
helper_llm and embedding slots from the same card row.

OpenAIConfig + EmbeddingConfig both get the card's ``api_key`` and
``base_url``. Empty base_url means "use OpenAI official defaults" —
the OpenAI SDK fills it in.
