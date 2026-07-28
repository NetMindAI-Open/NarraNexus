---
code_file: tests/backend/test_manyfold_sync.py
last_verified: 2026-07-28
---

# test_manyfold_sync.py — managed-trigger surface

Locks: run-job 控制消息**严格全匹配**（带任何多余文字必须落回普通
agent run）、/manyfold/jobs 排除终态 + 鉴权、/manyfold/channels 解码
telegram 凭据、webhook middleware 只对 config 路由的 2xx 写请求开火、
notify 的 500ms 合并/失败不抛/无 env 时零副作用，以及 execute_job_once
的编排（维护 pass 先行、状态门、drain 上限、永不 raise——JobTrigger
执行体本身是 stub，其行为由 job_module 测试覆盖）。

被测模块是 `backend.routes.manyfold.sync`（2026-07-24 布局整理后 manyfold
路由成包，本测试随之从扁平的 `backend.routes.manyfold_sync` 跟进）。
