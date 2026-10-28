---
code_file: src/narranexus/platform/bootstrap/lifecycle.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-20 — bootstrap_active 的单一判定源

`is_bootstrap_active(db, agent_id, owner_id, agent_metadata) -> BootstrapStatus` 是**三个后端消费方**
——`step_1` 问候 seed、`ChatModule.persist_turn` prepend、`context_runtime` 的 Bootstrap 注入/
auto-delete——判「引导期」的共享判定源。返回 `BootstrapStatus(active, present, event_count,
threshold, bootstrap_path)`；判定 = Bootstrap.md 存在（读侧 `resolve_existing_workspace`，兼容
legacy flat 布局）**且**（threshold 为 None 的语义型 profile，或 `event_count < threshold`）。

**不是全仓唯一**:`backend/routes/auth.py` 另算一个**前端信任**的 `AgentInfo.bootstrap_active`——
只 `os.path.isfile(Bootstrap.md)`、**无阈值**（list 接口担不起每 agent 一次 COUNT），它 gate 前端那颗
静态问候气泡。两者只在「越阈值但 Bootstrap.md 还没被 auto-delete」的窄窗口分叉（前端显示气泡、
但两个写入方因 `status.active=False` 拒绝落库）。要统一得先解 list 接口的 N+1（记 `reference/
self_notebook/todo/`）；在那之前,改一处记得连带看 `auth.py` 两处（已加注释指回本文件,可 grep 到）。

**为什么单独成文**：这段判定原本内联在 [[../context_runtime/context_runtime]] 里（决定注不注入
Bootstrap prompt）。当 `step_1` 的问候语 seed 出现后，[[greeting_seed]] 需要**一模一样**地复算它
（否则:门禁看的是永不清除的 `bootstrap_greeting` metadata → bootstrap 过期后每开新 narrative 都
被塞过时问候语）。复制这段(workspace resolver + isfile + events COUNT 裸 SQL + 阈值)会埋 drift:
一侧改阈值语义/布局解析,另一侧就分叉。所以判定归这里,两个调用方都调它。

**副作用留在调用方**：`context_runtime` 保留自己的 auto-delete —— 当 `present and not active`
（Bootstrap.md 在但超阈值）时 `os.remove(status.bootstrap_path)`；`active` 时注入 prompt。helper
只判定、runtime 只动作,故返回 `event_count/threshold/bootstrap_path` 让它能删。owner-vs-当前用户
的门禁在调用方(两个调用方都持 agent row)。

方向:judgment 归 `bootstrap/`(bootstrap 语义的归属地),runtime **逻辑**模块反向 import 这个判定。
(唯一预存的 `bootstrap→context_runtime` 边是 `profiles.py` import `context_runtime.prompts` 常量叶子——
叶子、无环,本 PR 未动。)
COUNT 查询失败按 active 处理(fail-open,同历史 runtime 行为)但现在会 `logger.warning`。真实 SQL
在 [[test_lifecycle]] 用真 db_client 在 sqlite 上执行过(与 prod MySQL 逐字节相同的查询)。
