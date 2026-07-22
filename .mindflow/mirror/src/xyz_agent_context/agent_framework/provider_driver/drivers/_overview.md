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

**在驱动层与 user-pays 驱动没有区别** —— 同样只按 card 构造凭证，不计费。

免费额度扣减发生在驱动之外：``cost_tracker.record_cost`` 在
``provider_source == "system"`` 时从 ``user_quotas`` 扣减，与 ``cost_records``
写入同处；扣减失败只记日志不抛（LLM 调用已成功，不该因记账抖动而让用户请求
失败）。

本驱动此前有一个 ``on_call_completed`` override 声称承担扣减，但它从未被调用，
已于 2026-07-20 删除，详见 [[system]]。
