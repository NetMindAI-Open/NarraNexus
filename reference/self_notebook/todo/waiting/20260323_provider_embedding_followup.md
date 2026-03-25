# 20260323 Provider/Embedding Feature Follow-up

## 1. Matrix Debug
- `matrix_create_room` fails with `Invalid user_id: ["@agent_d9b68b7f4990:localhost"]`
- Synapse says user ID is invalid, but NexusMatrix registration returned this ID
- Likely cause: NexusMatrix `/registry/register` succeeded in its own DB but Synapse user creation failed silently
- Need to check: NexusMatrix's register flow → does it actually call Synapse admin API to create the user?
- Verify with: `curl http://localhost:8008/_synapse/admin/v2/users/@agent_d9b68b7f4990:localhost`
- File: `src/xyz_agent_context/module/matrix_module/_matrix_credential_manager.py:362`

## 2. Embedding Model Switch: Ensure Existing Logic Continues Working
- When user switches from old model (e.g., text-embedding-3-small) to new model (e.g., BAAI/bge-m3) via Provider Config UI:
  - `use_embedding_store()` becomes True → reads ONLY from `embeddings_store`
  - Old data in legacy columns (narratives.routing_embedding, etc.) is ignored
  - User needs to click "Rebuild" to populate `embeddings_store` for existing data
  - During rebuild, search results will be incomplete (only new data visible)
- Need to verify: the full flow of switching model in the UI → rebuild → all search working
- Need to test: switching back to old model (do old vectors still exist in embeddings_store?)
- EmbeddingBanner correctly shows only when `use_embedding_store()=True` and `all_done=False`

## 3. Variable Naming Improvements
- `openai_config` is misleading — it's used for helper_llm slot which may not be OpenAI (e.g., NetMind minimax)
  - Consider renaming to `helper_llm_config` or keeping `openai_config` but documenting it means "OpenAI-protocol config"
- `openai_agents_sdk.py` class name `OpenAIAgentsSDK` — same issue, it's not necessarily OpenAI
  - Consider renaming to `HelperLLMSDK` or `ChatCompletionSDK`
- `_structured_output_blocklist` is module-level global — consider moving into the class or a config
- `ClaudeConfig.to_cli_env()` — the method name doesn't indicate it handles bearer_token vs api_key distinction
- `NETMIND_ANTHROPIC_BASE_URL` / `NETMIND_OPENAI_BASE_URL` in provider_registry.py — hardcoded, should potentially be configurable
