---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_mcp_tools.py
last_verified: 2026-05-08
stub: false
---

# _common_tools_mcp_tools.py — MCP server factory + env-based web_search backend dispatch

## 为什么存在

CommonToolsModule MCP server 的薄入口层。它做两件事：

1. **定义 `with_mcp_timeout` 装饰器**——Bug 20 留下的架构遗产，给任何 MCP 工具套一层兜底 timeout，防止单工具卡死整个 MCP 容器。
2. **在 `create_common_tools_mcp_server` 里按环境变量分派 backend**——有 `BRAVE_API_KEY` 就用 Brave；没有就用 DDGS。注册的工具名都叫 `web_search`，LLM prompt 无需区分。

在 B1 重构（2026-04-21）之前，DDGS 相关的所有实现（subprocess spawn、retry 循环、tool handler 注册）都内联在本文件。重构后，DDGS 逻辑完整移到 `_common_tools_impl/web_search_ddgs_tool.py`，本文件只剩 factory + decorator。

## 这个文件不做什么

**不实现任何 web_search 逻辑**——subprocess 调用、retry 循环、tool handler 全在各 backend 的专属文件里（`web_search_ddgs_tool.py` / `web_search_brave_tool.py`）。本文件只选哪个 backend，不关心它们内部怎么做。

## 上下游关系

- **被谁用**：`module_runner.py` 启动 CommonToolsModule 的 MCP server（端口 7807）时调 `create_common_tools_mcp_server(port)`
- **依赖谁**（lazy import，在 `create_common_tools_mcp_server` 调用时按 env 选一）：
  - `_common_tools_impl.web_search_ddgs_tool.register(mcp)` — DDGS backend（无 `BRAVE_API_KEY` 时）
  - `_common_tools_impl.web_search_brave_tool.register(mcp, api_key)` — Brave backend（有 `BRAVE_API_KEY` 时，尚未实现）
- **`with_mcp_timeout` 被谁用**：`web_search_ddgs_tool.py`（`from .._common_tools_mcp_tools import with_mcp_timeout`），未来 Brave backend 也会用

## 设计决策

- **Brave import 是 lazy 的**：`from ._common_tools_impl.web_search_brave_tool import ...` 在 `if brave_key:` 分支里，而不是 top-level。原因：Brave 模块在 B1 阶段尚未存在；lazy import 让 Python 不在 import time 触发 `ModuleNotFoundError`。
- **`with_mcp_timeout` 留在本文件不移到 impl**：它是所有 backend 共用的基础设施，不属于任一具体后端；如果移入 `_common_tools_impl/` 会造成循环依赖（`_common_tools_impl` 里的模块要反向 import 本文件的装饰器）。

## `with_mcp_timeout` 装饰器

把 handler 包在 `asyncio.wait_for(fn(...), timeout=seconds)` 里。超时返回 `"[tool_error] ..."` 字符串让 LLM 读到"这个工具暂时不可用"。

**装饰器叠加顺序：** `@mcp.tool()` 在上，`@with_mcp_timeout(...)` 在下——FastMCP 注册时看到的函数已经是带 timeout 包装的版本。

**Note:** `with_mcp_timeout` 只 bound 协程，不能杀掉底层线程或子进程。需要回收资源的工具（web_search）自己要做 subprocess 隔离——装饰器是外面一层网，不是替代。

## Gotcha / 边界情况

- **`@with_mcp_timeout(_WEB_SEARCH_HANDLER_TIMEOUT_S)` 读常量的时机**：该常量现在在 `web_search_ddgs_tool.py` 里。装饰器在 `register(mcp)` 调用时（即 `create_common_tools_mcp_server` 内部）读取常量。所以测试里 `monkeypatch.setattr(tools, "_WEB_SEARCH_HANDLER_TIMEOUT_S", 2)` 必须在 `create_common_tools_mcp_server` 之前，且 `tools` 指向 `web_search_ddgs_tool`，不是本文件。
- **不要在本文件 monkeypatch `_web_search_with_retry`**：B1 后该函数在 `web_search_ddgs_tool`；测试必须 patch 那个模块的属性，不是本文件的属性。

## 新人易踩的坑

- 新加 MCP 工具**必须**在 backend 文件里叠 `@with_mcp_timeout(N)`——没有这个，一次 bug 可以挂整个 MCP 容器所有 session
- backend 选择在 `create_common_tools_mcp_server` 里判断，不在 top-level——所以 `BRAVE_API_KEY` 必须在进程启动时设置好，运行中改 env 不会切换 backend

## 已注册的工具（当前）

1. **web_search** — 按 env 选 Brave 或 DDGS backend（见 `web_search_ddgs_tool.py` / `web_search_brave_tool.py`）
2. **create_artifact** — 文本 artifact 创建/迭代（HTML / ECharts JSON / CSV / Markdown）
3. **upload_artifact_file** — 二进制 artifact 上传（PNG / JPEG / PDF）

后两个由 `_common_tools_impl/artifact_tool.register(mcp)` 注册，在 web_search backend dispatch 之后执行。

## 相关约束

- DDGS backend 细节：`.mindflow/mirror/src/xyz_agent_context/module/common_tools_module/_common_tools_impl/web_search_ddgs_tool.py.md`
- artifact tool 注册细节：`.mindflow/mirror/src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_tool.py.md`
