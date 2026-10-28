---
code_file: plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/contribution.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 新增 `MESSAGE_SOURCES`

总线是消息来源但不是渠道，所以走 `ingress.message_sources` 位。从
`src/narranexus/platform/message_bus/__init__.py` 的模块顶层注册搬过来——那次注册只要有人
import 到 `agent_framework` 就会发生，而拥有总线模块的其实是本插件。

两个字段的语义必须一起读：`reply_tools` 列出这一轮所有**送达**的工具（
`message_agent` / `message_team` 是发给别的 agent，`notify_owner` 是 Owner Relay），而
`owner_visible_reply_tools` 只有 `notify_owner`——总线的对端发送不会出现在 owner 的网页聊
天里。把两者混为一谈，就是 2026-08-01 把真实总线送达记成 NO-REPLY、污染兜底决策指标的那
次事故。

# plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/contribution.py

## 2026-09-07 — the plugin's own contribution table

MODULES (and TRIGGERS where the plugin has one) are the objects the manifest names; the platform holds no list of builtins. Channel plugins derive both from their ChannelDescriptor through narranexus.platform.channel.contributions.contributions_from.
