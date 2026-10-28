---
code_file: src/narranexus/platform/channel/binding_tables.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 4 四轮复审移植）— 一渠道一来源、按 `ui.order` 排、空 id 列表拒绝

`home_assistant` 既是注册的描述符又有自己的绑定表，此前在 UNION 里出现两次；现在有自有表的渠道跳过 `channel_credentials`
分支。IM 渠道按描述符 `ui.order`（缺省 1000）排序而不是注册顺序；构建后断言渠道不重复。`bound_channels_query([])`
直接 `ValueError`——空 `IN ()` 在两种方言都是非法 SQL，之前会生成坏语句到执行期才炸。

# src/narranexus/platform/channel/binding_tables.py

## 2026-09-07 — parameterised binding sources for the agents directory

channel_binding_sources() yields one BindingSource per registered IM channel — a sub-select over channel_credentials whose channel name is a %s PARAMETER and whose alias is an index (src_0), never the name — plus the binding-only tables; bound_channels_query() assembles the UNION ALL with the channel name of each branch bound too. Moved out of bundle/ (it is a directory projection, nothing to do with bundles) and made injection-proof by construction rather than by relying on ChannelDescriptor's validator in another package. MySQL twin: tests/backend/test_auth_agents_directory_mysql.py.
