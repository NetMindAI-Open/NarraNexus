---
code_file: src/narranexus/platform/message_bus/schemas.py
last_verified: 2026-09-10
stub: false
---
## 2026-09-10（review r3 M1）— `canonical_ts` 入参不得为 None

multipart 原副本 `str(value or "")` 把 None 排最前，本函数 `str(value)` 会把 None 排成 `"None"`
（最后）——若写成 ack 高水位，lane 永久静默。今天不可达（`bus_messages.created_at` NOT NULL），
docstring 写明前提。

## 2026-09-10（review r2 M3）— `canonical_ts` 搬到这里，唯一的家

原在 [[local_bus]]（docstring 记着一次 `"T"` vs `" "` 排序引发的重触发事故，明写「it gets exactly
one home」），[[multipart]] 曾逐字复制了一份 `_ts`。方向上 multipart 不能反向 import local_bus，
所以搬到两边都依赖的 schemas；local_bus 继续 re-export（trigger 仍从 local_bus import）。

## 2026-09-09 — BusMessage.part_index / part_count / part_group

分片消息的三列（可空；普通消息全 None），随 `_row_to_message` 进出。`part_group` 是
第 1 块的 message_id。见 [[multipart]]。

## 2026-09-09 — BusMessage.part_message_ids（非列）

内存字段：当一条 BusMessage 是多行（分片消息）的**重组体**时，按序列出它代表的每一行
message_id；普通单行消息为 None。不进表——只存在于 trigger 交给 turn 的那份对象上，
让按行记账的东西（投递回执 `_stamp_receipts`）能触达每个分片。重组本身见
[[multipart]]（下一条 commit）。

## 2026-08-14 — BusMessage.segments

`Optional[List[dict]]`，每项是 `{kind: "monologue"|"reply", text}`：agent 自己的思考和
它的回答之间的分界，`content` 因为拼接而丢掉、下游谁也恢复不了的那个信息。

**`content` 一个字节都没变**，这是刻意的：它是所有**文本**消费者读的东西——记忆索引、
其他 agent 的 scrollback。一个渲染特性不能改写系统其余部分读的那份内容。

`None` 表示"没有记录过分界"，包括这个字段存在之前写的每一条消息（铁律 #2：不回填、不做
兼容垫片），读者把它们渲染成一整块——也就是此前的行为。空列表也存成 NULL：它不携带任何
读者能用的信息，而一个看起来像数据的值会引诱读者去相信"这一轮确实没有分界"。

## 2026-08-07 — BusMessage.root_run_id

发送方那一轮所属的触发树。被唤起的 run 从这里继承,级联停止才能越过
agent→agent 的一跳。用户消息与老行为 None。

## 2026-08-04 — BusMessage 增加 sender_turn_source

承载"这条消息是提问还是回复"的事实(发送方那一轮的种类)。存量行与丢
header 的适配器上为 None,消费方必须按未知降级 —— 见
[[message_bus_trigger]] 的降级顺序。

## 2026-07-31 — BusMessage.event_id

`BusMessage` gained `event_id: Optional[str]` — the `events` row of the turn
that produced this message (set by the trigger on agent replies posted into
team rooms). None for user messages and legacy rows.

> ⚠️ 括号里那句已于 2026-08-14 失效 —— 见本文件 08-14 节。

## 2026-07-20 — BusMessage.attachments

`BusMessage` gained `attachments: Optional[List[dict]]` (bus-attachment dicts:
file_id/original_name/mime_type/size_bytes/category/rel_path). None for text-only.
Files travel by reference; see [[_bus_attachment_impl]] for the dict contract.

# schemas.py — MessageBus 数据模型定义

## 为什么存在

`MessageBusService` 的方法参数和返回值需要稳定的类型，同时 `LocalMessageBus` 的数据库行需要反序列化成 Python 对象。`schemas.py` 集中定义这四个数据模型，让接口层（`message_bus_service.py`）和实现层（`local_bus.py`）都依赖同一套类型，不各自定义。

## 上下游关系

**被谁用**：`message_bus_service.py` 在抽象方法签名里用这些类型；`local_bus.py` 在数据库行转换时（`_row_to_message()` 等）实例化这些类；`module/message_bus_module/_message_bus_mcp_tools.py` 用 `BusMessage` 等类型做 MCP 工具的返回值。

**依赖谁**：只依赖 Pydantic v2 和 Python 标准库，无业务逻辑依赖。

## 设计决策

所有时间戳字段类型是 `Any = None`（`Timestamp = Union[str, datetime]` 别名也定义了但实际字段用 `Any`）。这是因为 SQLite 返回时间戳为字符串，MySQL 返回为 datetime 对象，统一用 `Any` 避免在多后端场景下类型验证失败。代价是丧失了类型安全——调用方在比较时间时需要自行处理 `str(ts)` 或 `ts.isoformat()` 的转换。

`model_config = {"arbitrary_types_allowed": True}` 是为了支持 `Any` 时间戳和其他非标准类型在 Pydantic 模型里的使用。

`BusMessage.mentions` 是 `Optional[List[str]]`，在数据库里序列化为 JSON 字符串（`local_bus.py` 的 `_row_to_message()` 里有 `json.loads`）。

## Gotcha / 边界情况

`BusChannelMember` 有两个游标字段：`last_read_at` 和 `last_processed_at`。`last_read_at` 给前端"已读"展示用，`last_processed_at` 给后台 `get_pending_messages()` 用。在数据库里这是两列，不要把它们混用。

## 新人易踩的坑

时间戳比较时不要直接 `msg.created_at > cursor`——在 SQLite 模式下两者都是字符串，字符串比较在 ISO 8601 格式下通常正确，但如果格式不完全一致（有无时区后缀、精度不同）会出现奇怪的排序结果。`LocalMessageBus` 里用的是 `str(latest.created_at)` 保证一致性。

## 2026-08-12 — `BusMessage.routed_by`:`mentions` 的来历

`None` = 发送者自己写的;`"default_responder"` = team 房间没人被 @,路由补了一个,
免得房间没人应答。

为什么必须在**产生这个决定的地方**记下来:下游无法反推。「只有一个 mention,而且
正好是 lead」和「用户就是故意 @ 了 lead」在数据上完全一致,而后者是最常见的用法之一。
靠猜做判定正是铁律 #15 反对的形状。

## 2026-08-14 — `event_id` 的语义扩了:它不再是「平台代发」的标记(更正 07-31)

上面 07-31 那节写的「set by the trigger on agent replies posted into team
rooms」现在只说对了一半。agent 自己调 `bus_send_message` / `bus_send_to_agent`
发的行**也**盖这个 id(取自身份头,见 [[_message_bus_mcp_tools]])。

扩它是因为团队房要回答「平台没代发的这一轮,房间到底听没听见这个 agent 说话」——
两条投递路径只有一条盖章的话,这个问题就只有一半有答案,而另一半只能靠猜;猜错就是
在一个**已经听见回复**的房间里再贴一条「投递失败」。

由此**不要**再把 `event_id IS NOT NULL` 读成「这条是平台代发的」。它现在的含义只是
「发的时候知道自己在哪一轮」。为 None 的三种情形:用户消息、列存在之前的旧行、以及
发送方确实说不出轮次(头缺失时按设计静默降级,消费方一律按「说不准」处理,绝不按
「发生过」)。

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
