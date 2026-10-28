---
code_file: frontend/src/components/dashboard/SessionSection.tsx
last_verified: 2026-04-13
stub: false
---

# SessionSection.tsx — Intent

## 为什么存在
让 owner 看到**正在跟这个 Agent 说话的具体是谁**——解决 v1 最大的盲点（public agent 同时服务多人时看不到）。默认 collapsed 仅显示头像条，展开后每个 session 可单独点开看最新消息。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` expanded 态 + `agent.sessions.length > 0` 时渲染
- **下游**：
  - `expandState.ts::useExpanded` 管 section 级 + item 级展开
  - `lib/api.ts::getSessionDetail` lazy 加载 session 详情（最新 bus message）
- **数据**：`agent.sessions[]`（从 `backend/state/active_sessions.py` registry snapshot 来）

## 设计决策
1. **Avatar dot 颜色**：用 `user_display` 做稳定 hash → palette 8 色选一。同一用户始终同色——用户识别稳定。
2. **最多显示 5 个头像**：`shown = sessions.slice(0, 5)`，超出显示 `[+N]` chip。避免卡片头部爆裂。
3. **两层展开**（原与 JobsSection 同模式，后者已于 2026-08-27 删除）：
   - Section：点 header 展开完整 session 列表
   - Item：点单个 session 展开"最新消息 + 元信息"详情
4. **Initial 算法**：`user_display` 按空格分词，单词数 ≥2 取首末首字母，1 个取前 2 字母。简单但足够识别。
5. **Item 展开时 stopPropagation**：与已删除的 JobsSection 同做法。
6. **`user_last_message_preview` 内联在折叠行**——不用展开就能看一眼最新一句，提供"快扫"价值。展开才看完整 latest message。

## Gotcha
- `colorForSeed` 的 hash 函数简单（string → int 乘 31），对字符全相同的 display 名可能碰撞。样本量 <10 人时问题不大；如果未来支持 100+ 并发用户，要换更强 hash。
- **头像条默认不显示**——这是**刻意**的（v2.1.1 简化）：session 存在通过 `verb_line` ("Serving 3 users") 已经告知。头像条只有点开 section 才出现，走渐进式披露。
- `getSessionDetail` 需要 `agent_id` query param（后端强制）——api.ts 封装已加。如果将来改成无 agent 的全局 session 查询，要改 API 签名。
- 非 owner（public agent）**不显示**这个 section——权限由 `AgentCard::OwnedCard` 入口判断，本组件不重复检查（信任上游）。
- `user_last_message_preview` 字段**目前后端没实际填充**——都是 null。设计就位但数据流还没接通（需要从 bus_messages 或 streaming cache 拉）。UI 对 null 正确 fallback（不显示那行）。
