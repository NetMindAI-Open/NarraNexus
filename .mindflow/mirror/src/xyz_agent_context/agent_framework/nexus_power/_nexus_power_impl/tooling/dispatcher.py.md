---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/dispatcher.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)(配套:C2 落地)

`visible_tools` 删除每通道 `sorted(name)`——排序会把中途展开的新工具**插进
MCP 段中间**,把插入点之后的字节全部挤出 provider 缓存前缀,正好违反本文件
自己承诺的「expansion appends, never resorts」。现顺序=(通道序,注册序);
注册序确定性由通道自己保证(见 [[mcp_channel]] 同日条目)。工具数组初始顺序
因此一次性变化(旧用户首轮 cache miss 一次,之后照常)。

## 2026-07-30 — 派发前 required 校验(hermes 形)

policy/spec/marker 之后、路由之前,按 spec.input_schema.required 查缺:缺字段
不进 handler,错误里带缺失清单+完整 schema 让模型重发。handler 自己的缺省回退
曾把「参数没到」翻译成误导性错误(write 无 path→workspace 根→Is a directory)。
对 MCP/skills 等所有通道生效(schema 同源)。

# tooling/dispatcher — 唯一分发器

代数缓存(通道 generation 元组)、disallowed/allowlist 过滤在 schema 层生效(A1)、标签工具裁决后短路、search_lines=自研模型无关 tool_search 底座。「模型可见≡实际注册」由本类单点保证。
