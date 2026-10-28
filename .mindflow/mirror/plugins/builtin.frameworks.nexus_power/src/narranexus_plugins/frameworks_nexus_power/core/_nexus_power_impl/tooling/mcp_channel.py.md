---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/tooling/mcp_channel.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

MCP `ImageContent` 转成 `ToolImage` 挂在 ToolResult.images；`_render_content` 跳过图片部件，文本里永不
出现 base64。isError 结果只取文本（错误路径的图片没有下游消费者）。

## 2026-09-08 — stdio 传输

`_connect_one` 按 `spec.is_stdio` 分流：stdio 走 `mcp.client.stdio.stdio_client`（子进程 env = 运行器环境 ⊕ spec.env，
spec.env 来自 [[plugin_contributions]] 的引导变量），否则维持 SSE。`stdio_command()` 把 "python"/"python3" 解析成
`sys.executable`——插件要 import `narranexus.sdk` 与 fastmcp，只有宿主解释器有它们；PATH 上的裸 python 是用户 shell 的。
每回合起一个子进程是本地版可接受的成本（fastmcp 冷启 <1s）；云端没有用户插件，不走这条路。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)(配套:注册确定性)

connect 把**连接**与**注册**分离:gather 完成序随机,不再让 _connect_one 边连边写
共享 _specs(旧行为=初始工具序按完成序抖动,dispatcher 只好排序兜底)。现在
_connect_one 纯返回 (session, tools),connect 按 server 名序统一注册;每批
(初始/每次 add_servers)内有序、批间追加——(批序, server 名序, server 内注册序)
即工具数组的确定性来源。

# tooling/mcp_channel — MCP client 通道(v1 核心)

mcp__{server}__{tool} 命名(三处子串依赖);并发连接、单 server 失败降级为缺席;add_servers=动态展开末端(尾部追加+generation 递增+同名先注册者赢);aclose 全量收割连接(孤儿连接事故类)。结果文本 isError 归一为错误型结果。
