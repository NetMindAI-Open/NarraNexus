---
code_file: src/xyz_agent_context/channel/message_source_handler.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — extract_reply_text 空白复判 + 三态返回契约（空气泡根因）

citation strip 之后复判空白：gpt-5.x + WebSearch 场景「几乎全是
citation token 的回复」被剥成 `"\n"`，truthy 穿过原有的 `if not text`
falsy 判定，一路落库成空气泡（2026-07-13 报告的真凶）。返回值改为三态
契约：非空 str=回复文本；`""`=**是**回复调用但文本剥空（含 content
缺失）；None=根本不是回复调用（工具名不匹配，或自定义 extractor 拒绝
如 lark_cli 非发送命令）。所有 falsy 消费方（chat_module split、
_delivered_to_origin、step_4 锚点）行为一致；唯一需要区分的消费方是
openai_compat._classify_event——`""` 丢弃事件、None 落 tool_call，按
is_user_reply_tool 名字判会误吞 lark_cli 非发送命令（review 建议的原
方案即此缺陷）。语义影响：纯空白回复算「未交付」——剥完只剩空白说明
本来就没有实质回复。owner-visible 路径经 extract_owner_visible_text
委托，同点覆盖。

## 2026-08-04 (review 三) — effective_owner_visible_names 属性

None 回落规则收敛到单点：属性返回「生效的 owner-visible 名单」，
is_owner_visible_reply_tool 与 chat_module 的日志共用，日志不再手抄
回落逻辑（review round 2 Minor #4：手抄的规则将来必先漂）。

## 2026-08-04 (review 修正) — owner_visible_reply_tool_names：「交付给来源」≠「owner 可见」

PR #230 review 抓到：一份 `user_reply_tool_names` 名单被三个消费方共用，
bus 名单扩容后「bus 交付」被 step_4 误当「给 owner 发过消息」→ owner 会话
锚点被 A2A 回复劫持。拆成两个谓词：
- `user_reply_tool_names` = 交付给「联系你的人」（度量/NO-REPLY 判定语义）。
- `owner_visible_reply_tool_names`（新，默认 None=回落前者）= 输出出现在
  owner web chat。chat/IM 渠道天然两者相同（会话对象就是 owner）；bus
  override 为仅 send_message_to_user_directly（对端是 agent）。
新方法 `is_owner_visible_reply_tool` / `extract_owner_visible_text`（复用
extract_reply_text 的自定义 extractor 路径）。消费方：step_4 锚点判定与
chat_module 的 user-visible split 用 owner-visible；extract_reply_text
保持「交付」语义留给度量与未来兜底。

## 2026-07-03 — `dedicated_trigger` flag + `handlers()` accessor

`MessageSourceHandler` gains `dedicated_trigger: bool = False`: True for
sources with their own long-running trigger process (all six IM channels).
MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag via the new `MessageSourceRegistry.handlers()` snapshot accessor,
replacing a hand-maintained prefix tuple that had drifted (wechat/discord/
narramessenger missing → double dispatch). Any module shipping a
run_*_trigger.py entrypoint MUST set the flag — enforced by
tests/message_bus/test_bus_channel_inbox_skip.py.
# message_source_handler.py — 按 WorkingSource 分发的聊天历史处理表

## 为什么存在

每个 `WorkingSource`（`chat` / `lark` / `message_bus` / `job` / `a2a` / `callback`
/ `skill_study` / 未来的 channel…）映射到一个 `MessageSourceHandler`，替 chat-history
pipeline 回答两个问题：

1. **写侧**——本轮 agent 有没有通过这个 source 的工具回复用户？
   （`is_user_reply_tool` / `extract_reply_text`）
2. **读侧**——这条落库的行喂给 LLM 时该打什么标签？（`format_row_prefix`）

用 `MessageSourceRegistry` 全局注册表而不是 `if working_source == "lark": ...`，是为
落实铁律 #3（模块互不 import：chat_module / context_runtime 不能 import lark_module /
message_bus）和铁律 #4（通用分发在这里，per-source 知识跟着各自模块走）。新 IM trigger
上线 = 一行 `Registry.register(...)`，别处零改动；`dump()` 可打全表方便 debug。

需要定制的 channel（Lark 把回复塞进 `command` 的 `--markdown` flag）注册自带的
`extract_reply_fn`；不需要定制的 source 全落到 `_DEFAULT_HANDLER`。本文件纯配置 +
纯函数，无 I/O / async / DB。

## 2026-06-17 — 在回复抽取唯一收口处剥离 Responses-API 引用标记

PR #25 给 `extract_reply_text` 加了一道内容层清洗：剥掉 OpenAI Responses-API 在跑过
WebSearch 后内联吐进用户可见文本的 "citation" 标记（形如 `citeturn6view0`、
`citeturn2news12`，2026-06-08 gpt-5.5 via codex 观测到）。

为什么剥而不解析：ChatGPT 自家前端会把这些标记用一张单独的 annotation 表渲成可点的
Markdown 链接，但 `openai-codex` Python SDK 0.1.0b3 不暴露那张表
（`OutputTextContentItem` 只带 `{text, type}`），拿不到 URL/title 映射就没法渲染正经
链接——务实做法是直接剥掉，让用户看到干净文本而不是黏在句尾的天书标记。

为什么剥在**这里**而不在 per-framework translator：这些标记是 model 写进
`send_message_to_user_directly`（或任意回复工具）`content` 参数里的**纯字符串内容**，
不是 SDK 协议元数据；在 SDK 边界剥会漏掉 model 写进 `lark_cli` / `slack_cli` /
`tg_cli` markdown 的同类标记。而**每个 channel 的回复都汇流经过本方法**，一处剥覆盖
全部 channel。

实现要点 / gotcha：

- `_strip_responses_api_citation_tokens` 带 `if "cite" not in text` 快路径，无标记
  原样返回。剥完还会收尾：合并双空格、去标点前空白（中英标点都管，i18n 安全）、去行尾
  水平空白（段尾那个 token 形状的洞）。
- 正则 `cite[a-z]+\d+[a-z]+\d+` 要求 cite 后**两轮 alpha+digit**，避免误伤英文单词
  "cite" 后接名词。
- 同时导出 `strip_responses_api_citation_tokens`（无下划线公开别名）供模块外调用方
  （主要是 `response_processor` 给 live UI 流式构建 ProgressMessage 时）复用同一套剥离。
- `extract_reply_text` 被重写成：先从 `extract_reply_fn` 或默认 `content` 取出
  `text`，统一过一遍剥离再返回——无论哪个 extractor 产出，剥离都一致生效。
