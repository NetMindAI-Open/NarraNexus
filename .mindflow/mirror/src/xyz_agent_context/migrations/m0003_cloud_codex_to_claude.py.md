---
code_file: src/xyz_agent_context/migrations/m0003_cloud_codex_to_claude.py
last_verified: 2026-07-18
stub: false
---
# m0003 — 云端 codex_cli → claude_code 一次性数据迁移

## 为什么存在

云端策略改成"只允许 Claude Code + NetMind key"后,历史上选过 `codex_cli` 框架的老用户被
**死锁**:`agent_framework='codex_cli'`(owner 默认在 `user_slots[slot_name='agent']`,
per-agent 覆盖在 `agent_slots[slot_name='agent']`,step_3 优先取后者)需要 openai 协议的
agent slot,而 NetMind onboard 把 agent slot 绑到了 anthropic 腿 → 运行时协议冲突、Agent
起不来;而框架切换门禁又方向不敏感,切不回 claude_code。把 framework 值翻成 claude_code 后,
现有 anthropic slot 就自洽了(claude_code→anthropic),解锁。

## 为什么用迁移而非登录钩子

登录钩子只能修到"重新登录"的人;还持有有效 session 的用户会一直卡到 token 过期。这个迁移
**部署时跑一次,修到所有存量用户、零重登**。云端策略又挡住新用户变 codex,所以不需要常驻钩子。

## 关键设计

- **云端 gate 在第一行**(铁律 #7):runner 在 `bash run.sh`/DMG 也跑,本地用户合法用 codex,
  绝不能被翻 → `is_cloud_mode()` 为假直接 `{"skipped": "local mode"}`。
- **两层 framework 都翻**(铁律 #8 扫邻居):`user_slots`(owner 默认) + `agent_slots`
  (per-agent 覆盖,step_3 优先取它)。只翻 owner 层的话,历史上写过 per-agent 覆盖的 agent
  迁移后依然锁死。两张表列结构一致,同一组等值条件(`slot_name='agent' AND
  agent_framework='codex_cli'`)复用同一次 `db.update`,stats 分开计数
  (`migrated_user_slots` / `migrated_agent_slots`)。
- **用 `db.update` 而非手写 SQL**:`db.update` 双方言(SQLite/MySQL 各自出正确占位符)、
  返回受影响 rowcount,因此纯内存 SQLite 测试即可覆盖,**不需要单独的 live-MySQL 方言测试**
  (手写 raw SQL 才需要)。
- **幂等 + 非破坏**(铁律 #6):纯值 UPDATE,`WHERE agent_framework='codex_cli'`,重跑匹配 0 行;
  runner 另有 `schema_migrations` ledger 保证只跑一次。
- **只动 agent slot**:`helper_llm` 不碰(协议要求更松,不是阻塞点)。
- slot 若被绑到非 anthropic provider(罕见,netmind onboard 绑的是 anthropic)是 A1+A2
  的 UI 自救兜底范围,迁移只翻 framework、不做逐用户 rebind(那属于交互路径)。

配套:providers.py 的 403 方向化(允许切回 claude_code)+ 前端两个选择器的弹窗方向修复。
