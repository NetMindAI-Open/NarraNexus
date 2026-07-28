---
code_file: src/xyz_agent_context/agent_framework/loop/events.py
last_verified: 2026-07-27
stub: false
---
# loop/events.py — driver 事件契约的唯一事实源

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
