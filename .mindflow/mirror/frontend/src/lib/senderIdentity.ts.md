---
code_file: frontend/src/lib/senderIdentity.ts
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — 注释里那句"没有人的颜色会变"是假的

hash 确实和被替换的两份实现逐字节一致——但**调色板不是**：两份拷贝在 5–7 槽已经漂移
（`AgentInboxPanel` 是 teal/indigo/fuchsia，`SessionSection` 是 fuchsia/teal/indigo）。
收敛必然要选一方，选的是 inbox 那一份，所以 dashboard 上哈希落在这三槽的 seed **颜色会变
一次**。这是"曾经有两份拷贝"的代价，没有哪种排序能让两边都不动。注释改成说这件事。

# senderIdentity — 一个 agent，一种颜色，在每个显示它的界面上

## 为什么存在

6 个成员的房间里，每条 agent 消息都是同一种灰：唯一的区分是气泡上方 10px 的名字，
而移动端连那个都不显示。颜色是让多方对话可扫读的东西。

但颜色要起作用，前提是它是**身份**——在房间、收件箱、dashboard 上是同一个。**它此前不是。**

## 漂移已经发生了，只是没人看得见

抽取时发现两份既有实现的**色序已经不同**：

| | 位置 5-7 |
|---|---|
| `AgentInboxPanel.senderColor` | teal, indigo, fuchsia |
| `dashboard/SessionSection.colorForSeed` | fuchsia, teal, indigo |

前 5 位相同，之后分岔。**任何哈希落到 5-7 槽的 agent，本来就在两个页面显示两种颜色**——
静默地，没有任何地方能发现。给 team room 再加第三份拷贝，就会变成三种。

## 设计

- **seed 用 agent_id，不用显示名**。改名不该换颜色，而改名恰恰是读者最需要颜色别动的时刻。
  这也让「以后允许用户指定颜色」只需在读取处加覆盖层，不需要数据迁移。
- **每一槽同时带 `dot` 和 `accent`**，调用方无法只给半个面上色——头像和气泡边不一致会让一条消息
  读起来像两个 agent。
- **色序是承载语义的**：它决定颜色跨版本稳定，所以只能追加，不能插入或重排。
- 哈希与被替换的两份**逐字节相同**，所以既有界面上任何 agent 的颜色都不会移动。

相关：[[AgentInboxPanel]]、[[SessionSection]]、[[TeamMessageBubble]]
