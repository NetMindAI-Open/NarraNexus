---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/tooling.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

新增 `ToolImage(mime_type, data)`：`as_content()` 给 provider（OpenAI image_url data URL），`describe()`
给日志（不含字节）。ToolResult 新字段 `images` 默认空元组，老工具零改动；`as_text()` 仍只含文本。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — ToolContext.extra_readable_roots

本回合除 workspace 外还可读的绝对根。框架**不解释**其含义（不知道什么是 `_shared`、
什么是 team），只由平台决定授予什么——协作区按设计位于任何单个 agent workspace 之外。
缺省空元组 = 纯 workspace 收敛，放宽永远是 opt-in。消费者见 [[policy.py]]。


## 2026-07-31 — ToolCall.truncated

parse 失败分两种,给的建议正好相反:被流切断→**发小一点**,JSON 写坏→**发对一
点**。所以除 parse_error 外再带一个 truncated 布尔,由 model_client 从收到的字
节判定而非上游 stop_reason(网关会谎报,见该文件 mirror)。指错方向比不指更糟——
模型会去修一个不存在的转义 bug。

## 2026-07-30 — ToolCall.parse_error

参数 JSON 未解析成功的调用带 parse_error 字段穿过 loop:这种调用必须被回答、
永不执行(带残缺参数执行=误导性下游错误,2026-07-30 事故)。

# contracts/tooling — 工具面契约与标签工具

description 跟 ToolSpec 走(单一事实源防 prompt 漂移)。ToolAnnotations.marker_only=标签工具:调用即信号,dispatcher 短路执行,语义全在事件流(参数流式=用户看到的回复,投递归事件消费方)。deny/失败都是错误型 ToolResult,永不以异常穿透循环。
