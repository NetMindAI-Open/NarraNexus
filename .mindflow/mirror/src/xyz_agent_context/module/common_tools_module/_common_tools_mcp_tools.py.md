---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_mcp_tools.py
last_verified: 2026-04-20
stub: false
---

# _common_tools_mcp_tools.py — CommonToolsModule MCP 工具注册 + 通用超时装饰器

## 为什么存在

挂所有 "跨模块通用能力" 的 MCP 工具（目前只有 `web_search`），以及提供一个所有 MCP 工具都能用的**通用 timeout 装饰器** `with_mcp_timeout(seconds)`——这是 Bug 20 留下的架构遗产。

## 上下游关系

- **被谁用**：`module_runner.py` 启动 CommonToolsModule 的 MCP server（端口 7807）时调 `create_common_tools_mcp_server(port)`
- **依赖谁**：`_common_tools_impl.web_search`（`search_many` 和 `format_results`）；`mcp.server.fastmcp.FastMCP`

## `with_mcp_timeout` 装饰器（Bug 20 的通用防御）

**动机：** 2026-04-18 那一次事故里，一个 MCP 工具（web_search）因为底层 sync 库卡死把**整个共享 MCP 容器**拖垮 33+ 小时。事后扫过所有现存 MCP 工具，只有 web_search 有"`asyncio.to_thread` + `gather` 全无 timeout"的陷阱 pattern；但**没有任何机制拦截未来新工具重复这个错误**。

**解法：** 一个简单的装饰器，把 handler 包在 `asyncio.wait_for(fn(...), timeout=seconds)` 里。超时返回 `"[tool_error] ..."` 字符串让 LLM 读到"这个工具暂时不可用"。

```python
@mcp.tool()
@with_mcp_timeout(45)
async def some_tool(...) -> str:
    ...
```

**装饰器叠加顺序很重要：** `@mcp.tool()` 在上，`@with_mcp_timeout(...)` 在下——`mcp.tool()` 注册时看到的函数已经是带 timeout 包装的版本。

**装饰器返回类型是 str。** FastMCP 按 wrapped function 的 return annotation 校验输出。现在所有 web_search 类型的工具都 `-> str`，so 这个决定是安全的。如果未来有 `-> dict` 工具要套 timeout，得扩装饰器看 annotation 选 `{"error": ...}` 或 `f"[tool_error] ..."`——这是已知 TODO，没触发就先不做。

## `web_search` 工具本身

多 query 并行搜索，handler 只做：接参 → 调 `search_many` → `format_results` 渲染成 markdown。错误处理：内层 `search_many` 已经把 per-query 错误收进 bundle，handler 只做 top-level catch-all（理论上触发不到）。

**为什么 `from ... import search_many, format_results` 在 handler 内部：** 延迟导入、让测试可以 `monkeypatch.setattr(ws_impl, "search_many", ...)` 在 handler 调用 search_many 前拦截。模块级 import 会 bind name 得太早。

## Timeout budget（本文件 + web_search.py 协作）

| 层 | 位置 | 常量 | 默认 |
|---|---|---|---|
| 1 | `web_search.py` → `DDGS(timeout=...)` | `DDGS_CLIENT_TIMEOUT_S` | 5s |
| 2 | `web_search.py` → `_one` 的 `wait_for(to_thread)` | `PER_QUERY_TIMEOUT_S` | 15s |
| 3 | `web_search.py` → `search_many` 的 `wait_for(gather)` | `OVERALL_TIMEOUT_S` | 30s |
| 4 | **本文件** → `@with_mcp_timeout(45)` on handler | `_WEB_SEARCH_HANDLER_TIMEOUT_S` | 45s |

每一层都给内层留余量（通常 50-100% cushion）让内层有机会先自己降级，外层是最后的刀。Idle timeout 在 `xyz_claude_agent_sdk.py:207` 是 600s，足够覆盖最深的 45s handler + LLM thinking。

## Gotcha / 边界情况

- **装饰器 wrap 后 `mcp.tool()` 读到的 return annotation 是原函数的**——因为 `functools.wraps` 把 `__wrapped__` 属性和注解保留了。测试里拿 FastMCP 的 `list_tools` 仍能看到正确的 `web_search` 名字和签名
- **线程泄漏仍然存在**——见 `web_search.py.md`。装饰器只能让 asyncio 层移动，不能 kill C/Rust 层的 socket syscall

## 新人易踩的坑

- 新加 MCP 工具**必须**叠 `@with_mcp_timeout(N)`——没有这个，一次 bug 可以挂整个 MCP 容器所有 session
- timeout 数值选择：handler timeout > 该工具内部所有 timeout 之和 + buffer。不能搞反
- 如果你的工具是**纯 async**（没有 `to_thread` / 子进程），装饰器也要加——asyncio 层的 bug 一样能阻塞（比如 `await asyncio.Event().wait()` 没 timeout 的情况）
