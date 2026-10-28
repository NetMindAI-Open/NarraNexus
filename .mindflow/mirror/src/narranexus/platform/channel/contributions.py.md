---
code_file: src/narranexus/platform/channel/contributions.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `WorkingSource` 注册收归这里（唯一一处，且不在读路径上）

`contributions_from` 现在顺手调 `register_working_source(descriptor)`。

选这里的理由是**它什么时候跑**：渠道插件的 `contribution.py` 调它，而 manifest loader
只在 **boot** 解析该插件 `provides` 时 import 那个文件，并且只对本发行版装了、
`registry.json` 没禁的插件做。所以「注册」和「这个部署真的有这个渠道」是同一件事。

它取代的三条路径：

1. `descriptor.py` 顶层的 `SOURCE = WorkingSource.register("lark")`——只要有人 import 到
   这个包就注册，跟部署装没装无关；
2. `_ChannelSpecs._build()`——**读** `SUPPORTED_CHANNELS`（HTTP 请求路径上的成员判断）时
   在缓存未命中的分支里注册；
3. `TriggerMapView._build()`——同上，解析 trigger 类时注册。

2 和 3 是真正的坑：`WorkingSource("lark")` 能不能解析，取决于有没有人先读过渠道表。
现在 `_build()` 是纯读。

`TriggerType` 孪生注册仍然在 `WorkingSource.register` 内部同一次调用里完成
（`narrative/models.py` 记录了这条配对，拆开会让该渠道的事件没有 surface 标签）。
`has_inbound` 是闸门：收不到消息的渠道永远不会给某一轮打标签，给它造一个枚举成员等于往
词表里塞一个没有事件能携带的值。

同批新增 `message_source_contribution(spec)`：非渠道来源（总线、Job）进
`ingress.message_sources` 的入口。渠道**不**用它——渠道的消息来源从已经在
`ingress.channels` 里的描述符投影出来，不需要第二条 contribution。

# src/narranexus/platform/channel/contributions.py

## 2026-09-07 — descriptor-derived contributions

contributions_from(descriptor, plugin_id) yields a channel plugin's MODULES and TRIGGERS from the one ChannelDescriptor it already writes (module_ref / trigger_ref), so adding a channel is one descriptor, not six table rows. module_contribution / trigger_contribution are the two shapes every module plugin's contribution.py uses.
