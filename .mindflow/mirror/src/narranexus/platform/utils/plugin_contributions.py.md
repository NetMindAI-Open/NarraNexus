---
code_file: src/narranexus/platform/utils/plugin_contributions.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — stdio 配置的 `env` 带子进程引导

`plugin_mcp_servers()` 的 stdio 分支 `env` 改为 `subprocess_env(spec.env)`（见 [[subprocess_env]]）：没有它，模板 stdio
server 在任何框架下都起不来。url 分支不变。

## 2026-09-03（批 2a.5）— Agent 面向四个 kind 的注册表读侧

context_runtime（MCP server / tools）、skill 模块（skills）、团队市场（bundles）都需要同一件事：按声明序
遍历注册表、经工厂构造、坏条目只记 warning 不炸回合。这里就是那个循环，按 kind 定型：
`plugin_mcp_servers()` 输出与回合 `mcp_servers` 同形（url 传输 → `{url, headers}`，stdio → `{command,args,env}`，
同名先声明者赢）；`plugin_tools()` 按 provider 序、去重；`plugin_skills()`/`plugin_bundles()` 只返回文件真实
存在的。`_registries()` 是唯一的 `KERNEL_REGISTRIES` 取用点，测试用 monkeypatch 注入干净 `Registries`。
