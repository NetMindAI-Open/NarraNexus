---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_impl/web_search.py
last_verified: 2026-04-20
stub: false
---

# web_search.py — DuckDuckGo 多 query 并行搜索实现

## 为什么存在

给 LLM 一个轻量的"去互联网查信息"能力，不依赖 Anthropic 的 server-side `WebSearch`（aggregator provider 不支持，会 45s 挂死），也不是 Claude CLI 内置 `WebFetch`（只能单 URL）。用 `ddgs` 库封装 DuckDuckGo 文本搜索，多 query 并行 fan-out。

## 上下游关系

- **被谁用**：`_common_tools_mcp_tools.py:web_search` MCP tool handler（注册在 CommonToolsModule MCP server，端口 7807）
- **依赖谁**：`ddgs` pypi 库（内部用 primp/libcurl 发请求）

## 三层 timeout 防御（Bug 20 · 2026-04-20）

**事故：** 2026-04-18 18:15:36 一次 DDGS HTTPS call 走进了 primp 的 `CLOSE_WAIT` 处理不好的 edge case（对 AWS 跨区 DDGS proxy `32.194.63.207`，server 发 FIN、rx_queue 32 bytes 未读、primp 没 `close()`）。DDGS 库的 `ThreadPoolExecutor.__exit__ → shutdown(wait=True)` 等到死。我们外层原先 `await asyncio.to_thread(_search_sync)` + `await asyncio.gather(...)` 两处都没 timeout，连带把共享 MCP 容器挂 33+ 小时。

**修复：** 三层独立 timeout 叠加，任何一层可以救：

| 层 | 常量 | 默认 | 位置 |
|---|---|---|---|
| 1 | `DDGS_CLIENT_TIMEOUT_S` | 5s | `DDGS(timeout=...)` 传给库内部 |
| 2 | `PER_QUERY_TIMEOUT_S` | 15s | `_one` 里 `asyncio.wait_for(to_thread, ...)` |
| 3 | `OVERALL_TIMEOUT_S` | 30s | `search_many` 里 `asyncio.wait_for(gather, ...)` |

外层 MCP handler 还有第四层 45s（见 `_common_tools_mcp_tools.py` 的 `with_mcp_timeout`）。Idle timeout 从 1200s 降到 600s（`xyz_claude_agent_sdk.py`）做最终兜底。

## 已知残留问题

**线程泄漏。** `asyncio.wait_for(asyncio.to_thread(...), timeout=15)` 超时时，Python worker thread 还在 primp 底层 socket 里阻塞——**没有安全的从外部杀线程的机制**。asyncio 继续走但物理线程漏了。DDGS 内部 `ThreadPoolExecutor` 一样，它的 worker 也漏。

- 默认 asyncio thread pool ≥32 workers，一两次泄漏不致命
- 只影响 `web_search` 一个工具，pool 满了之后该工具报错，其他 MCP 工具不受影响

**彻底方案：** 把 `_search_sync` 搬到 subprocess 里跑（能 `proc.kill()`）。记录在 `reference/self_notebook/todo/waiting/web_search_subprocess_isolation.md`，优先级 P4。

## 设计决策

**每 query 一个 `DDGS()` 实例。** DDGS 实例化成本很低，每个 query 单独 session 让 cookie 隔离，减少 rate-limit 互相拖累。

**错误不 raise，降级为 per-query 错误 bundle。** 一个 query 失败（timeout/HTTP 错误/解析失败）不能拖垮其他 query。`_one` 的 try/except 把所有异常收进 `{"query": q, "error": "...", "results": []}`，上层 `format_results` 渲染成"_search error: ..._"让 LLM 知道这一条挂了但可以继续用别的。

**常量集中在文件顶部。** Timeout 数值（5/15/30s）命名常量，未来调节不用搜文件内部。层次关系：client < per-query < overall，给每一层充足的"内层正常完成+一点 cushion"时间。

## Gotcha / 边界情况

- **DDGS 默认 timeout 是 5s 但我们**仍然**显式传**。防止未来 upstream 改默认值（ddgs 库历史上改过好几次架构，比如加了 module-level `_api_process`）
- **`_search_sync` 里的 `from ddgs import DDGS` 是函数内 import**。这让测试可以 `monkeypatch.setattr(ddgs, "DDGS", FakeCtx)` 拦截——如果改成模块级 import，测试 mock 就得走更复杂路径

## 新人易踩的坑

- 想改 timeout 时只改一层——**三层要一起调**，内层超时必须早于外层，否则外层永远先触发、内层的结构化错误 bundle 就丢了
- 想加新的 MCP 工具——**必须**用 `@with_mcp_timeout(N)` 装饰，N 要 > 工具内部最大 timeout + 少量 buffer。新工具不加 timeout 是 Bug 20 的复发路径
