---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/modeling/projector.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

`_project_tool_images`：OpenAI 兼容端点只接受 user 消息里的图片，不接受 tool 消息。投影时 tool 消息
只留文本，同一批工具结果之后追加**一条** user 消息承载所有图片（前缀说明是工具观察、不是用户指令）。
批内 tool 消息保持连续，并行调用配对不被打断。litellm 转 Anthropic 时会把 tool_result 与这条 user
消息合并进同一个 user 轮（已用 anthropic_messages_pt 实测），两种协议都成立。

## 2026-09-08 — `thinking_replay` 有了第一个消费方

此前 `ProviderProfile.thinking_replay` 全表 "strip" 且零消费方。现在 `project()` 对账本回合消息按它取舍
`reasoning_content`：strip 去键（不认识该键的 provider 可能拒收），keep 原样回传（DeepSeek 思考模式缺它就 400）。
只动本回合的账本消息，base（平台物化的历史）不碰——DeepSeek 明确不需要上一轮的 CoT。去键是投影不是改账本。
# modeling/projector — v1 直通投影

base(平台物化+harness 注入)+账本回合消息拼接;压缩替换由账本投影承担,升级压缩不碰投影(职责分离)。
