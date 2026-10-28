---
code_file: src/narranexus/platform/schema/team_work_schema.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-14 — `WorkItemOrigin`：两层分野第一次落到列上

owner 2026-08-07 拍板「工作板与差事分层不合并」，在此之前这个决定只活在注释
里。`origin` 让它在运行时可执行：

- `tool` —— Leader 用 `work_add_item` / `work_complete_item` 显式维护的**任务**，
  一个任务跨多次差事，所以**任何自动逻辑都不许关它**；
- `auto` —— 平台从 @mention 记下的**消息级差事**（[[errand]]），交付即自闭，
  而正是这个自闭让自动开项敢做。

默认 `tool`：这层出现之前的每一行本来就是模型显式要的东西。

# team_work_schema.py — 工作板的数据形状与状态机

## 为什么存在

这是产品里**第一个任务级对象**。在它之前,一切持久化的都是**对话**
(消息、run、差事);一个任务只存在于讨论它的那一轮里,所以 Leader 派出去的
活随着它自己的 run 一起消失 —— 没有任何东西记得它,自然也没有任何东西能发现
它卡住了。工作项就是「活得比创造它的那个 run 更久的任务」。

**与差事分层,不合并**(Owner 拍板 2026-08-07):差事是**消息级**、自动记录
(A @ B 即开、B 回帖即闭);工作项是**任务级**、由工具显式维护,一个工作项
routinely 跨越多次差事。

## 状态机里有两个不属于模型

- **`stalled` 由平台推导**(`bus_agent_activity` + 差事超时)。铁律 #15:
  正确性关键位不能压在模型服从上。若模型能自己声明 stalled,巡查提示词里
  「这些卡住了」就成了把模型自己的猜测念给它听。模型的自由裁量只在「卡住了
  该怎么办」,不在「是不是卡住了」。
- **`paused` 是停止留下的**。停一棵树停的是**运行**,不是任务;没有这个状态,
  工作项会留在 `open`,下一轮巡查就尽职地把 owner 刚停掉的东西复活。恢复是
  用户的显式动作 —— 平台不替用户判定「他只是打断了一下的任务」已经不要了。

`ACTIVE` 三态是巡查唯一关心的集合,`paused` 不在其中——这正是它的全部意义。
`MODEL_SETTABLE` 是工具层的白名单,见 [[_work_board_mcp_tools]]。

## 2026-08-10 — `patrol_is_on` 移出

搬到 `team_schema.py`:它读的两个字段都属于 Team,放在这里等于把一条 Team 规则
藏进工作项模块。本文件回到只管工作项状态机(`ACTIVE` / `MODEL_SETTABLE` 两个
集合,以及它们各自划的那条平台/模型界线)。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
