---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/web_search_ddgs_tool.py
last_verified: 2026-04-21
stub: false
---

# web_search_ddgs_tool.py — DDGS-backed web_search MCP tool registration

## 为什么存在

从 `_common_tools_mcp_tools.py` 提取出来的 DDGS 专属逻辑（B1 重构，2026-04-21）。在没有 `BRAVE_API_KEY` 的环境（本地开发、EC2 默认配置）下，由 `_common_tools_mcp_tools.create_common_tools_mcp_server` lazy-import 并调用 `register(mcp)` 注册到 FastMCP 实例上。

独立成文件的原因：为 Brave backend（B2 阶段）铺路——两个 backend 各占一个文件，factory 按 env 选一，互不干扰。

## 这个文件不做什么

**不做真正的搜索**——实际搜索逻辑在 `web_search.py`（`search_many`、`_one`、`_search_sync`）和 `web_search_runner.py`（subprocess entry point）。本文件只负责 subprocess spawn 外壳、retry 循环、MCP tool handler 注册，以及把 bundles 格式化成 markdown（通过 `web_search.format_results`）。

## 上下游关系

- **被谁调用**：`_common_tools_mcp_tools.create_common_tools_mcp_server` 在无 `BRAVE_API_KEY` 时 lazy-import 并调用 `register(mcp)`
- **依赖谁**：
  - `_common_tools_mcp_tools.with_mcp_timeout`（反向 import 上层文件的装饰器，见设计决策）
  - `web_search_runner` 子进程（通过 `python -m <module>` 启动，处理真实 DDGS 调用）
  - `web_search.format_results`（把 bundles 渲染成 markdown）
- **测试文件**：`tests/common_tools_module/test_web_search_subprocess.py`——所有 `tools.*` 引用指向本模块（B1 后 import 已更新）

## 设计决策

- **subprocess 隔离**（Bug 24）：DDGS 底层的 primp/libcurl 线程在卡死时无法被 Python 的 `asyncio.cancel` 回收；足够多的泄漏会导致 FD 表耗尽，杀死整个 MCP 进程的所有连接。子进程 `SIGKILL` 之后，Linux 无条件关掉所有 FD/socket/线程，是唯一有保证的资源回收方式。
- **`_WEB_SEARCH_HANDLER_TIMEOUT_S` 在本文件而非 factory**：handler timeout 是 DDGS backend 特有的参数（依赖 `_SUBPROCESS_TIMEOUT_S * _MAX_ATTEMPTS`），和 factory 解耦。Brave backend 会有自己的 timeout 常量。
- **`with_mcp_timeout` 从上层文件 import**：两个 backend 共用同一个 decorator，放在 factory 文件里统一维护，避免复制。

## 关键常量（module-level，供测试 monkeypatch）

| 常量 | 默认 | 说明 |
|---|---|---|
| `_SUBPROCESS_TIMEOUT_S` | 25.0s | 单次 subprocess 墙钟上限；超时 → SIGKILL |
| `_MAX_ATTEMPTS` | 4 | 最多尝试次数（K=3 重试 + 1 原始）|
| `_RETRY_BACKOFF_S` | 1.0s | 两次尝试之间固定间隔 |
| `_WEB_SEARCH_HANDLER_TIMEOUT_S` | 110.0s | 最外层 handler timeout（覆盖 4×25+3×1=103s 最坏路径 + 余量） |
| `_RUNNER_CMD` | `[sys.executable, "-m", <runner_module>]` | subprocess 启动命令；测试用 monkeypatch 换成 `python -c "..."` |

## Gotcha / 边界情况

- **`@with_mcp_timeout(_WEB_SEARCH_HANDLER_TIMEOUT_S)` 读常量的时机**：装饰器在 `register(mcp)` 被调用时（即 `create_common_tools_mcp_server` 内部）读取 `_WEB_SEARCH_HANDLER_TIMEOUT_S`。测试里 `monkeypatch.setattr(tools, "_WEB_SEARCH_HANDLER_TIMEOUT_S", 2.0)` 必须在 `factory.create_common_tools_mcp_server(port=0)` 之前执行，否则 decorator 已经用旧值创建了闭包。
- **`_spawn_runner` 里的 SIGKILL 是同步的**：`proc.kill()` 之后必须 `await proc.wait()` 确认进程已死，否则会留下僵尸进程。两行都有，不要删任何一行。
- **`_web_search_with_retry` 里的 backoff 只在 attempt < _MAX_ATTEMPTS 时执行**：最后一次失败后不 sleep，直接抛 RuntimeError。这是故意的——最后一次失败后没有下一次，backoff 没有意义。

## 新人易踩的坑

- 修改 `_SUBPROCESS_TIMEOUT_S` / `_MAX_ATTEMPTS` 后记得同步 `_WEB_SEARCH_HANDLER_TIMEOUT_S`——后者必须 ≥ `_MAX_ATTEMPTS * _SUBPROCESS_TIMEOUT_S + (_MAX_ATTEMPTS-1) * _RETRY_BACKOFF_S`
- 测试时 monkeypatch `_RUNNER_CMD` 而不是 `_spawn_runner` 能同时测到 subprocess 创建和 JSON 解析逻辑；monkeypatch `_spawn_runner` 直接控制每次 attempt 的结果（适合测 retry 计数）——两种方式都合理，但目的不同

## 相关约束

- 详见 `.mindflow/mirror/plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_mcp_tools.py.md`——factory 和 dispatch 逻辑在那
