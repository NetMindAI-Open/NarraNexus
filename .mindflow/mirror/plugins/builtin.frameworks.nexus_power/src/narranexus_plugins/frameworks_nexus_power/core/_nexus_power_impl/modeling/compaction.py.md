---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/modeling/compaction.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

`projected_chars` 只重建 content 里含 image_url 的消息，其余照旧 `len(str(m))` 不拷贝——它在长轮次
的每次压缩检查里对每条 tool result 都跑一遍（review Minor）。

## 2026-09-24 浏览器视觉输入

`estimate_message_tokens` 与 ledger 的 `result_seq_sizes` 共用 `projected_chars`：image_url 部件按
`_IMAGE_TOKEN_ESTIMATE`（4096 token）计，而不是 base64 长度——否则一张截图会被当成几十万 token，
触发无谓的输出预算钳制与裁剪；反之也不能按 0 计。封顶截图实际约 1.1–1.6K 视觉 token，4096 偏保守。
裁剪以整条 tool 消息为单位替换，旧截图随之出上下文，配对不受影响。

## 2026-07-31 — estimate_message_tokens 外借给输出钳制

同一个 `_CHARS_PER_TOKEN` 比例,供 loop 在构造请求时估输入大小。之所以不放在 loop:
账本的实测输入在**一个 turn 的第一步是 0**,而正是那一步的投影已经背着此前所有轮次,
所以必须有一个纯按消息估的数,和实测值取大。估算只用于**定大小**,计费永远走
provider 上报的 usage。

**必须数整条消息,不能只数 content。** turn_ledger 折叠出的 assistant 消息是 OpenAI
形状:工具调用在兄弟键 `tool_calls` 里,且该步只有调用没有文本时 `content` 被置为
None。只数 content 会把一条携带 16KB write_file 参数的消息估成 **1 个 token**,而这个
估算正是 turn 第一步(账本无实测)时钳制的唯一依据。序列化整条 dict 会略微高估(键名、
引号)——对钳制来说**高估是安全方向,低估才会让请求撞墙**。

ToolResultPruner:确定性裁剪不动 LLM,尾部 keep_recent 保护,配对安全(占位仍是合法 tool 消息)。字符估算只用于省量目标,计费永远真实 usage(C3)。SummaryCompactor v1.5 座位:费用归用户、默认用户主模型、usage 单列(Owner 拍板);narrative 联动经事件日志解耦。
