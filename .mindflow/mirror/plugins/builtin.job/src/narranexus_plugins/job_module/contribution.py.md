---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/contribution.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 新增 `MESSAGE_SOURCES`

Job 触发的一轮不是渠道，所以它的消息来源是一条独立的 `MessageSourceSpec`（进
`ingress.message_sources`），而不是 `ChannelDescriptor` 上的字段。

内容与原来包 `__init__.py` 顶层那次注册一致：Job 复用 `notify_owner`，`[Background Job]`
这个行前缀才是告诉 LLM「这条存下来的行来自定时任务、不是一次实时对话」的东西，它据此权
衡要不要追问。

# plugins/builtin.job/src/narranexus_plugins/job_module/contribution.py

## 2026-09-07 — the plugin's own contribution table

MODULES (and TRIGGERS where the plugin has one) are the objects the manifest names; the platform holds no list of builtins. Channel plugins derive both from their ChannelDescriptor through narranexus.platform.channel.contributions.contributions_from.
