---
code_file: plugins/builtin.frameworks.codex_cli/src/narranexus_plugins/frameworks_codex_cli/contribution.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — pin 的设计理由

`openai-codex==0.1.0b3` 是手抄的精确 pin（pyproject 是范围约束，安装器要具体版本），`test_pip_pins_match_uv_lock`
拿 uv.lock 对账；升级下限时必须同步改这里，否则插件商店继续装旧版。

# builtin.frameworks.codex_cli — contribution.py

The `agent.frameworks` contribution of the Codex CLI framework: `CONTRIBUTION` = name `codex_cli`, lazy factory building `CodexSDKv2` after `plugin_paths.activate_pyenv()`, and the install spec (npm `@openai/codex` CLI pin + exact pip pin of the Codex SDK). Registered by the kernel from the builtin manifest (batch 6b.2b).

## 2026-09-07 — META 携带框架事实（B6）

FrameworkMeta 增补 protocol/oauth_source/runtime_name/login_marker（值即原宿主七张名字表里属于本框架的那一行），导出为 META；宿主全部在调用期从注册表派生，本插件是这些事实的唯一持有者。

## 2026-09-07（round-2 P2-I3）— META states the cloud-safety fact and the empty capability set

`uses_shared_cli_login=True`: the CLI authenticates from `~/.codex/auth.json`, a file in the host's single HOME, so
it CAN ride a shared login. Whether cloud nonetheless offers it is the OPERATOR's decision
(`cloud_policy.cli_login_exempt_frameworks`), never this plugin's — a plugin attesting its own cloud
safety would be fail-open by construction. `capabilities=frozenset()` matches the driver's base
contract; its history is flattened at the CLI doorstep, so no `native_replay` either.
