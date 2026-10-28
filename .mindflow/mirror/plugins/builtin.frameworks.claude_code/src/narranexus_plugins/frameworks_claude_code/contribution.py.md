---
code_file: plugins/builtin.frameworks.claude_code/src/narranexus_plugins/frameworks_claude_code/contribution.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — pin 的设计理由（原在 `backend/integrations/plugins/registry.py.md`，随代码搬来）

- `claude-agent-sdk==0.1.43` **是**字面量：`pyproject.toml` 用范围约束给 `uv sync` 弹性，安装器却必须向 pip 请求
  一个具体版本，两者语义不同、不能互相 import，只能人工同步——`tests/backend/integrations/plugins/test_registry.py
  ::test_pip_pins_match_uv_lock` 拿 uv.lock 对账。
- npm requirement 唯独不是字面量而是拼 `cli_binary.PINNED_CLI_VERSION`：那是 agent loop 实际启动哪个 CLI 二进制的
  单一真值（2.1.56 vs 2.1.220 工具排序不同、影响 prompt cache 命中），插件装的必须与 loop 用的是同一版本。
- Gotcha：升级 pyproject 里下限时，症状是插件商店继续装旧版、运行时与后端期望的 SDK 版本不一致；根因是 pip 版本
  是本文件的手抄字面量而非从 pyproject 动态解析（动态解析要引入 TOML 依赖且范围约束也定不出唯一版本）。

# builtin.frameworks.claude_code — contribution.py

The plugin's `agent.frameworks` contribution (batch 6b.2b): `CONTRIBUTION` names `claude_code`, a lazy factory that activates the plugin pyenv and builds `ClaudeAgentSDK`, and the `FrameworkInstall` spec (npm CLI pin + exact pip pin of `claude-agent-sdk`). The host registers it through the manifest in `narranexus.kernel.plugins.builtins`; the platform never imports this module.

## 2026-09-07 — META 携带框架事实（B6）

FrameworkMeta 增补 protocol/oauth_source/runtime_name/login_marker（值即原宿主七张名字表里属于本框架的那一行），导出为 META；宿主全部在调用期从注册表派生，本插件是这些事实的唯一持有者。

## 2026-09-07（round-2 P2-I3）— META states the cloud-safety fact and the empty capability set

`uses_shared_cli_login=True`: the CLI authenticates from `~/.claude/.credentials.json`, a file in the host's single HOME, so
it CAN ride a shared login. Whether cloud nonetheless offers it is the OPERATOR's decision
(`cloud_policy.cli_login_exempt_frameworks`), never this plugin's — a plugin attesting its own cloud
safety would be fail-open by construction. `capabilities=frozenset()` matches the driver's base
contract; its history is flattened at the CLI doorstep, so no `native_replay` either.
