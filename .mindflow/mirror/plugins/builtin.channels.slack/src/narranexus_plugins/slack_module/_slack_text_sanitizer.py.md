---
code_file: plugins/builtin.channels.slack/src/narranexus_plugins/slack_module/_slack_text_sanitizer.py
last_verified: 2026-08-18
stub: false
---

# _slack_text_sanitizer.py — Slack mrkdwn 的防御性净化

## 为什么存在

因为 `slack_module.py` 的提示**劝不住模型**。它明确要求用 `<URL|text>`，而模型（尤其是默认
写标准 markdown 的中文 agent）反复退回 `[text](url)` —— 在 Slack 里那个语法不被解析，会作为
字面文本渲染出来。这是「靠散文约束模型行为」的又一个实例，处理方式也一样：不在提示上加码，
而在出口做变换。

第二个失败模式更隐蔽：Slack 的自动链接化会把裸 URL 一直延伸到下一个 **ASCII** 空白或
`<` `>`。CJK 标点（`，。；：、！？`）和汉字都不是 ASCII，于是会被吸进 URL 里，
`"访问 https://example.com，详细"` 变成一条断链。这不是模型的错 —— 它写的是正确的中文。

## 设计取舍

**在发送路径上做，而不是要求模型自律。** 与 `message_bus_module` 的桌面表同一条逻辑：
决定输出形状的应该是平台的变换，不是提示里的一句话。

**只改渲染语法，不改内容。** 净化的是链接语法与 URL 边界，不碰文字 —— 这一点是底线：
改写 agent 说出的话，用户会读成 agent 说了别的。

## Gotcha

- 代码块要先被屏蔽再做替换（`_mask`），否则示例代码里的 `[a](b)` 会被改写掉 —— 而在一个
  讲代码的对话里，那正是最可能出现的形态。

## 2026-08-18 — 首次补写镜像（本 PR 只改了注释）

本文件此前无镜像（全仓 555 个源文件里有 54 个如此，见
`reference/self_notebook/todo/2026-08-18-mirror-coverage-gap.md`）。本 PR 对它的改动是纯注释
（把现在时的 `ChannelInboxWriter` 引用改成过去时），不构成行为变更；补写镜像是因为**碰过的
文件不该留着空缺**，而不是因为这次改动要求它。
