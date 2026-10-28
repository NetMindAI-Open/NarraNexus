---
code_file: src/narranexus/platform/bootstrap/greeting_seed.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08（dev 实测）— `refresh_bootstrap_greeting_after_rename`

改名后若元数据里的问候正是本档「无名版」渲染结果，就用新名字重渲染并写回 `agent_metadata`；用户/场景自定义的问候不动。
由 `apply_agent_profile_change` 在改名成功后 best-effort 调用（工作室起名走的就是这条事务）。

## 2026-08-21 — 本模块只剩外层便宜过滤;per-agent 幂等归写入方(深圳复测 B2)

原 docstring 用「不然会 re-greet 每个新 narrative」论证 bootstrap_active
门——B2 实锤:bootstrap_active **窗口内**每个新 narrative 照样重种。
真正的幂等契约移进写入方(`chat_module.seed_bootstrap_greeting` 的
`agent_chat_has_history` 跨 instance 守卫,per-(agent,user));本模块的
门保留为外层便宜过滤(过期即整体停摆)。docstring 已同步改写。

## 2026-08-20 — 「该不该 seed 问候语」的判定（门禁必须对齐 hook 的 bootstrap_active）

`resolve_bootstrap_greeting_to_seed(db, agent_id, user_id)` 回答**决策**：这个 (agent, user)
的 bootstrap 问候语要不要落库、落哪条文本 —— 返回 greeting 文本或 `None`。真正的 chat 行写入交
给唯一写入方 [[../module/chat_module/_chat_writes]]（`seed_bootstrap_greeting`），它持有行结构 +
时间戳约束；本模块刻意不碰 chat 表。

**门禁必须和 hook 一致，不能只看 metadata**：`agent_metadata["bootstrap_greeting"]` 在 provision
时写一次、**永不清除**。若只用它当门禁，agent bootstrap 过期后每开一个**新 narrative** 都会得到
一个全新的空 chat 实例，而写入方的幂等是**每实例**的 —— 于是过时问候语会被塞进此后每一个新
narrative 的首条，永久重复。所以 `resolve_*` 的门禁 = **owner-only**（`agent.created_by == user_id`，
先短路，省掉不属于 owner 时的判定）**+** 共享的 [[lifecycle]]`.is_bootstrap_active`（Bootstrap.md
存在 + 未过阈值）。判定收在 `lifecycle` 单一真源里，`context_runtime` 也调它 —— 两个写入方不会
drift（早期版本各自复算 `bootstrap_active`，是 drift 源）。

**上游**：`step_1_select_narrative`，选完 narrative 后仅对 **head（`narrative_list[0]`）** 实例调用
一次（问候语作用域是 (agent, user)，不是 per-narrative）。fast-select / step_4 路径靠
[[chat_module]] 的 `persist_turn` prepend 兜底。已知边缘：若同一 bootstrap 轮内 agent 路由到
新 narrative，hook 会再问候 rebind 后的实例（详见 [[step_1_select_narrative]] 注释）。

全程 best-effort：任何异常返回 `None`，hook prepend 仍是兜底。
