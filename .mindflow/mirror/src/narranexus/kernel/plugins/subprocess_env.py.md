---
code_file: src/narranexus/kernel/plugins/subprocess_env.py
last_verified: 2026-09-08
stub: false
---

# kernel/plugins/subprocess_env — 子进程里的 `nxplugins`

## 2026-09-08 — 新建：让插件的 stdio MCP server 能 import 自己的包

`nxplugins.<id>` 只是宿主进程 meta_path finder 提供的合成命名空间；模板默认的 stdio server
（`python -m nxplugins.<pkg>.server`）在新解释器里直接 `No module named nxplugins`——Agent 写的第一个工具插件
启用后工具从未出现在它的清单里，根因就是这个（本地插件工场 E2E）。
做法：在 `<plugin_home>/run/pythonpath/nxplugins/__init__.py` 生成一个**真实**包，内容是「装 finder + 按 registry.json
逐个 `install_synthetic_package` + 注册 deps 目录」；子进程 `PYTHONPATH` 指到这里，`import nxplugins` 先落到它，
随后子模块由 finder 接管（finder 对 `nxplugins.<id>` 不看 `path` 参数）。`subprocess_env()` 只给 PYTHONPATH + 插件
home（不复制 os.environ，合并由启动方做），spec 自己的 env 覆盖、PYTHONPATH 则前置。消费方：[[plugin_contributions]]
（stdio 配置的 `env`）→ 任何框架的启动器（NexusPower [[mcp_channel]]、Claude/Codex 原生 stdio）。
