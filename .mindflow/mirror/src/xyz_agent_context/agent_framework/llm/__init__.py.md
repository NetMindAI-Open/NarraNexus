---
code_file: src/xyz_agent_context/agent_framework/llm/__init__.py
last_verified: 2026-07-24
stub: false
---

# agent_framework/llm/__init__.py — group anchor

Created in the 2026-07-24 agent_framework regrouping (61 flat files →
loop/ adapters/ llm/ providers/ + 2 cross-cutting root files). Atomic LLM operations — single calls, no agent loop: the
protocol-keyed helper factory (helper_sdk) with its anthropic/cli/gemini
backends, failure classification (failure), and audio transcription
(transcription/). (llm_api's empty leftover shell was deleted here —
embedding moved out long ago; zero imports remained.)
