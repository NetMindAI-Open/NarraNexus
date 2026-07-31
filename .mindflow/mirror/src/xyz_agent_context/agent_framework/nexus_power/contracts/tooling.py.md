---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/tooling.py
last_verified: 2026-07-31
stub: false
---

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
