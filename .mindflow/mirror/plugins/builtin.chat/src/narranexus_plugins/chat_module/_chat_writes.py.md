---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/_chat_writes.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-21 — 问候幂等域升为 per-(agent,user)(深圳复测 B2)

prod 取证实锤:两个测试 agent 的早期 run 各有两条同 event_id 的
assistant——bootstrap 问候 + 真实回复。机制:每开新 narrative 就生成
新空 chat instance,per-instance 空表守卫在每个新 instance 上**重新种**
问候,12s 历史轮询把它拉下来、在提问旁弹出「第二条回复」。

修法:新增 `agent_chat_has_history(db, agent_id, user_id)`——该
(agent,user) 的**任一** ChatModule instance 已有消息即 True(status
刻意不过滤:archived 里的历史同样证明「已首次接触」)。
`seed_bootstrap_greeting` 在自身 instance 检查(便宜、不依赖
module_instances 注册,放前面)之后加这道跨 instance 守卫;hook 兜底
prepend 用同一 helper。出生问候从此只属于第一段对话。
钉在 test_chat_writes.py(sibling 抑制/真首契仍种/按 agent+user 域)
与 test_bootstrap_greeting_order.py(hook 侧 sibling 抑制)。

## 2026-08-20 — bootstrap 问候行的唯一写入方

问候语现在有**两个**写入触发点：`ChatModule.persist_turn` 的懒 prepend（首轮历史为空时），
以及 `step_1` 的开局 seed（经 [[greeting_seed]] 判定后调用）。为避免「问候行长什么样」这份知识在
两处复制后漂移，行结构 + 时间戳约束集中在本文件（它和 [[_chat_reads]] 一样住在拥有
`instance_json_format_memory_chat` 表的 chat 模块里）。

`build_bootstrap_greeting_row(greeting, turn_started_at, instance_id, event_id=None)` 是问候行的
**唯一定义**。三条承重约束都在这里：
- `meta_data.bootstrap = True` 供前端 / auto-delete 识别。
- 时间戳锚在 **turn 起点（`Event.created_at`）减 1ms**，不是 `utc_now()`、也不是 agent 创建时间：
  - turn 起点 -1ms 严格早于用户第一条消息（戳的是 turn 起点）→ 升序渲染时问候排最前；
  - turn 起点 ≈ 用户按下回车的时刻 → 满足前端 `(role, content)+5min` session 副本去重的**时间**
    条件（`buildTimeline.ts`）。锚得更早（如 agent 创建时刻）会冲破这个窗口。**限制**:去重还需
    content 相等,而落库是英文默认串、session 副本是本地化串 → 非英文 UI 下问候仍渲染两次(非本次
    引入,根因修复留单独 PR,记 `reference/self_notebook/todo/`)。本锚点只负责时间条件。
- 序列化前把 naive datetime 归一成 aware-UTC，输出串带 `+00:00`，浏览器 `new Date()` 按 UTC 解析
  而非本地时区（MySQL 返回 naive datetime 的坑）。

`seed_bootstrap_greeting(...)` 幂等落库：实例已有任何消息就直接 no-op（和 hook 的
`len(messages)==0` 同一守卫）。所以它可以对主实例每轮调用 —— 新实例只 seed 一次，之后（或 hook
已写过）历史非空即跳过，问候永不翻倍、已有会话永不被重排。best-effort，失败退回 hook 兜底。
