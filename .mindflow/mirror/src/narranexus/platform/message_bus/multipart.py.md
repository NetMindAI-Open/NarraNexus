---
code_file: src/narranexus/platform/message_bus/multipart.py
last_verified: 2026-09-10
stub: false
---

# multipart.py — 长消息分片发送与收件方重组

## 为什么存在

bus 本身不截断（`bus_messages.content` 是 TEXT），截断发生在**发件方模型**：约 4-5k 字的
回复正好撞到工具调用参数 JSON 的输出 token 预算，loop 唯一诚实的回答是「工具没执行，
分小块发」（NexusPower `unparsed_call_result`）。没有分块契约时，「小块」= N 条独立 bus
消息，而 wake 信号在第 1 块落地时就启动收件方的 turn——收件方回答一个片段，后续块又
变成对那个回答的回复。8/31 用户报的三件事（长回复被截、收件方 turn 空、"This turn
ended without delivering a reply" 反复）就是这个形状。

## 契约

`message_agent(text, part_index=i, part_count=n)`。每块各自一行 `bus_messages`（不截、
不重编码），`part_group` = 第 1 块的 message_id，由写入边 `LocalMessageBus._resolve_part_group`
解析：块 >1 必须紧跟同 sender 同 channel 的**最近一块**（index-1、同 count），否则拒绝——
放不进组的碎片不允许存在。块数上限 `MAX_MESSAGE_PARTS`=40，与整组字节预算 `MAX_MULTIPART_TOTAL_BYTES` 并列、各自独立拒绝
（见 [[local_bus]] 写入边）。
车道 batch 是整条 lane 的 LIMIT 而不是这个组的，所以组可能被批次边缘切断（review I2）：
调用方传 `batch_truncated=True` 时不完整的组**一律 hold**、不做过期/取代判定（部分证据下的
判定会把还躺在表里的块标成「never arrived」）；组之前的行照常投递并 ack，下一批就更靠近组；
组顶在批次边缘、什么都推不动时，trigger 用 `PENDING_BATCH_LIMIT_WIDE` 再读一次。

收件侧 `assemble(batch)` 返回 `(deliverable, held)`，**唯一不变式**（review I3/I6）：车道的 ack
游标只推进到「实际投递的最新一行」，任何被 hold 的行都不能在游标之下。所以 `deliverable`
按 `created_at` 排序（合成消息坐在末块的时间上，`relevant[-1]` 既是触发消息也是 ack 高水位），
且**最早被 hold 的行之后的一切都跟着等**——组之后到的无关消息若现在投递，游标就越过了
part 1，组丢失。组之前的照常投递（不再整批陪跑 600s）。三种组结局：
- **不完整且年轻**（距最新一块 < `PART_ASSEMBLY_GRACE_SECONDS`=600s）→ 该组及其后的行被扣住
  （`held=True`），之前的行照常投递并 ack 到它们的最新一行；最后一块自己的 `wake_signal.bump`
  会把轮询拉回来。600s 刻意宽：
  发件方是 turn 中的模型，思考型模型两次工具调用之间可以几分钟；这个数只约束「发件方
  在两块之间死掉」能让收件方等多久。
- **完整** → 合成一条：content 用空串拼（它们是子串不是段落）、attachments 取并集、
  身份（message_id/event_id/root_run_id/turn source）取第 1 块、`created_at` 取最后一块
  （ack 游标必须越过每一行）、`part_message_ids` 列出全部行（投递回执要逐行盖）。
- **不完整但过期或被取代**（同 sender 在本 channel 又开了更新的组——写入边只会延续
  最新一块，旧组永远续不上）→ 按到达的内容投递 + 明确标记缺哪几块。宁可标记也不静默
  丢（铁律 #16），不永远等（铁律 #14：平台不做打断源）。

## 2026-09-10（review r2 M3/M8）— 用 [[schemas]] 的 `canonical_ts`；模块 docstring 写明「目前只对 DM 成立」

`_ts` 副本删除。前置条件：`message_team` 不得先于 lane 级饥饿方案加上分片参数，
`test_team_message_segments` 的豁免集合是现在唯一的闸。

## 2026-09-10（PR #389 I5/M4）— 跨越 hold 边界的完整组也进 held 集合

第三类组：完整、首块在 `held_from` 之前、末块在之后。此前输出过滤会把它整组排除却不把
`held_from` 压到它的首块——中间的普通行投递并 ack 后，首块落到游标之下，下一轮变成无头组、
过 grace 后带假「part 1 never arrived」投出。现在这类组并入 `held_groups` 并重算 `held_from`，
不动点迭代到稳定（集合只增、边界只降）。前置条件写进 docstring：`messages` 须按 `created_at`
升序（「发件方最新的组」取自遍历中最后见到的组）。锁：
`test_assemble_holds_a_complete_group_that_straddles_the_held_boundary`。

## 2026-09-10（PR #389 I1）— 超长拒绝拆成「事实」+「出路」

写入边只说事实（`BusMessageTooLarge(size)`，`oversize_fact`），出路由调用方按自己真有的手段拼：
`message_agent` → `OVERSIZE_REMEDY_PARTS`（它有 part_*）；`message_team` → `OVERSIZE_REMEDY_TEAM`
（拆成多次调用——它没有 part_*，此前那句「用 part_index/part_count」会诱导一次 unknown-argument
的重试环）；team 聊天 HTTP 路由 → `OVERSIZE_REMEDY_HUMAN`（面向人）。`oversize_reason(size, remedy)`
负责拼接。

## 2026-09-10（review r2 C2）— 块数上限回来了：`MAX_MESSAGE_PARTS = 40`

I7 删掉计数上限后，600 块 × 100 字节是合法的组，却没有任何一批（WIDE=500）装得下——
`batch_truncated` 又是无条件 hold，车道永久死锁。计数上限必须远小于
`PENDING_BATCH_LIMIT_WIDE`，在写入边 **part 1 就拒**（`too_many_parts_reason`），和字节预算
并列、各自的拒绝文案。另一半在 [[message_bus_trigger]]：WIDE 重读仍被切且无从推进时，
最后一次以 `batch_truncated=False` 裁决（组头一定在视野内）。

## 整组预算（review I7）

`MAX_MULTIPART_TOTAL_BYTES = 200_000` 是多段消息的字节预算（与 `MAX_MESSAGE_PARTS` 并列）：写入边
[[local_bus]] `_resolve_part_group` 把组内已存块的字节数（Python 里按 UTF-8 算，不用 SQL
`LENGTH()`——SQLite 数字符、MySQL 数字节）加上本块，超了就以 `group_budget_reason` 拒绝本块，
已存的块原样保留，绝不裁剪。单行 60 KB 是列的物理上限，与之是两个不同的拒绝理由（「这一块
放不进一行」vs「整条消息太长，拆成两条」）。

## 常量归属（review I4）

`MAX_BUS_MESSAGE_BYTES` 与 `oversize_reason()` 住在本文件（单一家），由 [[local_bus]]
`send_message` 对所有写入方执行；`message_agent` 工具不再自带副本。

## `split_for_bus`（review r2 I2）

给**平台写入方**（job 报告）用的按字节切块器：字符边界、`"".join(parts)==原文`。模型自己切自己的
文本；这是同一契约由代码执行。

## 边界

`MessageBusModule.gather` 的未读预览仍逐行显示（带 `(part i/n)` 标签），只有 trigger 的
turn 入口做重组；Agent 收件箱线程也按块记录（内容完整，只是分行）。
`test_multipart_messages.py` 钉 12k 字节级往返、hold、取代、过期标记、乱序/超长拒绝；
`test_multipart_mysql.py` 钉那条组查找 raw SQL 的 MySQL 方言。
