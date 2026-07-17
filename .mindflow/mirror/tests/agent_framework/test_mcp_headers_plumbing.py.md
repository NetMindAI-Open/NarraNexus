---
code_file: tests/agent_framework/test_mcp_headers_plumbing.py
last_verified: 2026-07-15
---

# test_mcp_headers_plumbing.py — MCP headers 全链路管道守卫

锁定 spec 形状 `{name: {"url", "headers"?}}` 在各层的正确落地：claude 驱动
`_build_claude_mcp_config` headers 原样进 SSE config（内部 MCP 不带键）；
codex 仅 Bearer——`codex_mcp_bearer_env` 抽 token 进 env、overrides 只发
`bearer_token_env_var`、token 明文绝不进 argv；`build_agent_loop_request`
跨 executor 边界携带 headers；路由 `_mask_header_value` 掩码不可逆且
None/{} 透传 None。
