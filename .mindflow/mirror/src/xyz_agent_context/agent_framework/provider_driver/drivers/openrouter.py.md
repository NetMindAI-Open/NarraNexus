---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/openrouter.py
last_verified: 2026-05-13
stub: false
---

# openrouter.py — OpenRouter aggregator one-key card

Same dual-row pattern as ``netmind.py`` / ``yunwu.py``.

OpenRouter's openai-protocol endpoint serves chat-completions but
**not** whisper / embeddings well (see transcription resolver — that
file deliberately skips OpenRouter for /audio). For the LLM-only
slots that this Driver handles, the standard OpenAIConfig works.

Aggregator semantics: ``supports_anthropic_server_tools=False``.
