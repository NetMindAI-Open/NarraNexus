---
code_dir: src/xyz_agent_context/agent_framework/provider_driver/drivers
last_verified: 2026-05-13
stub: false
---

# drivers — concrete Driver implementations

One module per provider type. Each registers itself with ``@register``
at import time (except ``system.py`` which guards on
``is_cloud_mode()``).

| Module | driver_type | Slots served |
|---|---|---|
| ``custom_anthropic.py`` | ``custom_anthropic`` | agent |
| ``custom_openai.py``    | ``custom_openai``    | helper_llm + embedding |
| ``netmind.py``          | ``netmind``          | agent / helper_llm / embedding (by protocol) |
| ``yunwu.py``            | ``yunwu``            | agent / helper_llm / embedding (by protocol) |
| ``openrouter.py``       | ``openrouter``      | agent / helper_llm / embedding (by protocol) |
| ``claude_oauth.py``     | ``claude_oauth``    | agent only (no /chat or /embed available) |
| ``system.py``           | ``system_pool``    | cloud only; agent + helper_llm + embedding |

## NetMind / Yunwu / OpenRouter — dual-row layout

Each aggregator quick-add writes **two** ``user_providers`` rows — one
``protocol=anthropic`` row carrying the chat-completions-style
endpoint, one ``protocol=openai`` row carrying the openai-aggregator
endpoint. They share a ``linked_group`` and the same ``api_key``.

The Driver therefore doesn't need to look up its sibling row: each
instance is constructed with the right ProviderCard for its protocol.
``build_*_config`` checks ``card.protocol`` and raises
``NotImplementedError`` if the slot binding pointed at the wrong half
(e.g. helper_llm pointing at the anthropic row by mistake — this is
how mis-bindings surface as loud errors instead of silent failures).

## ClaudeOAuthDriver — host CLI managed

``api_key=""`` deliberately. ``ClaudeConfig.to_cli_env`` blanks both
auth env vars in that case so the Claude Code CLI subprocess falls
back to its own ``~/.claude/.credentials.json``. The Driver's
``probe()`` checks file existence (not token validity — token
validation belongs to the CLI itself).

``auth_ref`` carries the sentinel ``claude-cli:~/.claude/.credentials.json``
so the path can be relocated via ``CLAUDE_CLI_HOME`` /
``CLAUDE_CLI_CREDENTIALS_PATH`` env vars without touching the row.

## SystemDriver — cloud only

The only Driver that overrides ``on_call_completed``. Calls
``QuotaService.deduct(user_id, in, out)`` after each LLM call so the
free-tier counter ticks down. Failure to deduct logs a warning but
does NOT raise — the LLM call already succeeded and a quota write
hiccup must not fail the user-facing path.
