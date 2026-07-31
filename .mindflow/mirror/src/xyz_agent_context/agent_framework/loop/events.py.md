---
code_file: src/xyz_agent_context/agent_framework/loop/events.py
last_verified: 2026-07-29
stub: false
---

## 2026-07-29 — 删除 DATA_TYPE_RESUME_FAILED(T5)

这个内部 marker 的用途是通知编排层清理过期的 CLI 会话句柄行。句柄机制整体删除后
(见 [[transcript]] 与 [[step_3_agent_loop]]),它没有生产者也没有消费者。

注意它是**线协议**:值曾经逐字节流经 executor 的 NDJSON 通道。删除它属于破坏性
协议变更,要求 orchestrator 与 executor 同批部署(铁律 #2:不做兼容层)。

# loop/events.py — driver 事件契约的唯一事实源

## 2026-07-29 — 新增 `DATA_TYPE_REPLY_DELTA` / `ITEM_TYPE_PLAN`

NexusPower 用的两个新事件面：`DATA_TYPE_REPLY_DELTA = "response.reply.delta"`
承载**表达工具参数**的流式片段（= 真正送达用户的回复，其余 LLM 输出一律算
思考），`ITEM_TYPE_PLAN` 承载整份计划快照。

写在这里而不是 nexus_power 包内：本文件是 driver 事件契约的**唯一**事实源，
任何 driver 想发的形状都得在这儿有名字，否则 [[response_processor]] 那侧就得
靠字符串魔法认。别的 driver 不发这两种，常量存在本身不构成负担。
## 2026-07-28 — 新增 DATA_TYPE_RESUME_FAILED（resume 化 R3）

`DATA_TYPE_RESUME_FAILED = "response.resume_failed"`：claude 适配器在
「请求 resume 但句柄已陈旧 → 同轮冷启动重试已跑」时发出的**内部** marker。
生产端 [[sdk.py]]（claude）`_resume_failed_marker_event`；消费端
[[response_processor.py]]（message=None + mark_resume_failed，分支放在
DATA_TYPE_ERROR 之前）。**绝不转 ErrorMessage**（铁律 #16——用户视角本轮
完全正常）；无 payload 字段。字面量是 NDJSON 线协议的一部分，与其它常量
同规矩：改值即跨版本破坏。

## 2026-07-27 — 新增 DATA_TYPE_USAGE

新增 `DATA_TYPE_USAGE = "response.usage"`:承载从流式 `message_start`/`message_delta`
抠出的每轮 token 用量(见 [[output_transfer]]),给代理的非 Anthropic 模型(终结
`ResultMessage.usage` 为 0)做记账兜底,由 [[response_processor]] 累加。

## 为什么存在

driver 产出的事件 dict 只有两大类六种形状（raw_response_event ×
{text.delta, done, error} / run_item_stream_event × {thinking_item,
tool_call_item, tool_call_output_item}），但形状此前以散落字面量的形式
存在于 output_transfer、claude/codex 适配器、cli_helper、
response_processor、run_collector 六处——一个隐性约定。本文件把约定变
成代码：生产方与消费方 import 同一份常量；将来第三个 driver（自研
nexus loop 的 LegacyEventAdapter）对准这一个文件，而不是逆向工程字面量。

## 关键事实（为什么是这些字段）

- 这些字符串是**线协议**：经 remote executor 的 NDJSON 通道原样传输，
  改值 = 跨 orchestrator/executor 版本的协议破坏，永远不是重构。
- `response.done` 的 usage 是计费链（cost_records → 配额扣减）唯一数据源。
- `tool_call_item.tool_name` 保留 `mcp__{server}__{tool}` 命名空间——
  organic-reply 判定、前端回复气泡、IM 回复提取三处子串匹配依赖它。
- `error_type` 枚举（CLI_ERROR_TYPES）驱动 fallback 跳过决策、熔断器、
  前端 actionable 徽章。
- usage 的 cache-read 字段有 Anthropic/OpenAI 双词汇拼写
  （USAGE_CACHE_READ_KEYS 定序），accumulate_usage 按此顺序取。

## 坑

- TypedDict 只是文档级类型；运行时仍是普通 dict（远端 NDJSON 反序列化
  回来的就是 dict），不要试图 isinstance 检查。
- 契约测试在 `tests/agent_framework/test_loop_event_contract.py`：值被
  钉死 + codex 翻译路径行为验证。`test_codex_sdk_v2_init.py` 的
  thinking_item 源码扫描测试也已改为认常量名。
