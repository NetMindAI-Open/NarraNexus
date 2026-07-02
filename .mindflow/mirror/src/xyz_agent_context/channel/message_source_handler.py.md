---
code_file: src/xyz_agent_context/channel/message_source_handler.py
last_verified: 2026-06-17
stub: false
---
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
