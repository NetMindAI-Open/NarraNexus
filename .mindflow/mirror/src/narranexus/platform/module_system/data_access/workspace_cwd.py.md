---
code_file: src/narranexus/platform/module_system/data_access/workspace_cwd.py
stub: false
last_verified: 2026-08-14
---

## 2026-08-14 — 新建（channel-CLI 共享 CWD 解析，PR#308）

lark_cli_client 与 narra_cli_client 各自维护的 `_resolve_agent_workspace_cwd` +
owner 缓存收敛为一份 `resolve_agent_workspace_cwd(agent_id, *, log_tag)`：

- owner 走 channel seam 的 `get_agent_owner`——direct-db 与零凭据部署行为一致；
- 空 owner **不入缓存**（re-bind 后可重解析），任何失败返回 None=调用方继承父
  CWD（只坏下载、不坏发送——CWD is optional by contract）；
- `log_tag` 保留 `[lark-cli]`/`[narra-cli]` 排障前缀；
- mkdir -p 保证 CLI 首次调用（agent 还没产出过 artifact 时）有处可写。

## 为什么在 data_access 而不是 utils/

两个候选家都被 review 议过（PR#308 round-2 Minor-2）：放 `utils/workspace_paths`
会制造全仓第一条 utils→module 反向边，且把代码移出 pyright 闸的 include 范围
（`src/narranexus/platform/module_system`）；放这里则两个问题都不存在——data_access 不是
Module（不违反铁律 #3 的 module 互不依赖），本来就是两个 CLI client 的共同依赖。

## Upstream / downstream

- **Upstream**: `lark_cli_client._run_with_agent_id`、`narra_cli_client.run_narra_cli`
  （模块级 `from narranexus.platform.module_system.data_access import resolve_agent_workspace_cwd`，
  data_access 加载期不引任何 channel module，无环）。
- **Downstream**: `factory.get_channel_credential_store`（模块级）、
  `utils.attachment_storage.get_workspace_path`（函数内）。

## Gotchas

- `_cwd_owner_cache` 是进程级共享 dict（lark+narra 同一份）——测试侧由
  tests/conftest.py 的 autouse fixture 每用例清空，防跨模块顺序依赖。
- 守卫测试在 test_lark_cli_cwd.py（happy/缓存/空 owner 不缓存/seam 异常四条）。
