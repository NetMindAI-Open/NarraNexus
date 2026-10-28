---
code_file: frontend/src/lib/ownerTools.ts
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — 注释同步

注释里的 ChatModule 方法名改为能力契约词汇（`contribute_tools`），无行为变化。

## 存在的理由

前端要回答一个问题：**这次工具调用是 agent 在对 owner 说话吗？**

后端把这件事拆成了两个名字——`reply_owner`（owner 问了、正等着）和 `notify_owner`
（owner 不在这场对话里，被打扰一下）。agent 每轮桌上只有其中一个，**前端无从预知是哪个**。

所以任何回答「owner 有没有收到东西」的代码必须**两个都认**。只匹配一个，是这次拆分之前
的写法，它在用另一个名字的每一轮上都静默地错：回复是真的、内容在那儿、气泡就是不渲染，
不报错、不告警。

## 边界

- 匹配裸名与 `mcp__<server>__` 前缀两种形式。
- 与后端 `channel/message_source_handler.py` 的 `_OWNER_TOOL_RE` / `is_owner_tool`
  是同一条规则（规则的规范副本 2026-08-17 已归到该文件），**必须同步移动**。新增第三个
  owner 名字时，这里和那处一起改。

## 消费方

[[chatStore]]（直播路径：拼接、内联时间线、`hasReply`）与 [[segmentTurn]]（回放路径）。
两条路径必须给同一个答案，否则重载一轮之后气泡会消失。
