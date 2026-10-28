---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/web_search_runner.py
last_verified: 2026-04-21
stub: false
---

# web_search_runner.py — web_search 的子进程入口

## 为什么存在

Bug 24 的核心文件。web_search 现在不在 MCP 主进程里跑 DDGS 了，而是每次调用都由 `_common_tools_mcp_tools.py` 里的 `_spawn_runner` **起一个全新的 Python 子进程**专门跑这次搜索。本文件就是那个子进程的入口脚本。

**为什么必须是独立脚本文件**：`_common_tools_mcp_tools.py` 调的是 `python -m narranexus_plugins.common_tools_module._common_tools_impl.web_search_runner`——模块路径必须能被 Python 直接 `-m` 拉起。不能写成函数塞在 `_common_tools_mcp_tools.py` 里。

## 上下游关系

- **被谁用**：`_common_tools_mcp_tools.py` 里 `_spawn_runner` 调 `asyncio.create_subprocess_exec(*_RUNNER_CMD, ...)`，而 `_RUNNER_CMD = [sys.executable, "-m", "<本模块路径>"]`
- **依赖谁**：`_common_tools_impl.web_search` 的 `search_many`（子进程启动后 import 它，跑完就退）
- **刻意不依赖谁**：NarraNexus 的 DB、module、services、日志系统——子进程要启动快、占内存少；把 heavy import 留给主进程

## IO 契约（父进程靠这个和我沟通）

### stdin（父进程写，子进程读）

UTF-8 JSON：
```json
{
  "queries": ["hello world", "python asyncio"],
  "max_results_per_query": 5
}
```
`queries` 必填，`max_results_per_query` 可省（默认 5）。

### stdout（子进程写，父进程读）

**只在 exit code = 0 时保证是 JSON**：
```json
{"bundles": [{"query": "...", "error": null | "...", "results": [...]}, ...]}
```

bundle 的结构和 `search_many` 返回值一致（见 `web_search.py.md`）。

### stderr

debug / error 信息，父进程 truncate 到 500 字节放进 `_RunnerFailure` 的异常消息里帮助排查。**子进程不要往 stderr 打大量日志**——stdout/stderr pipe 有 OS 缓冲限制，塞爆会死锁。

### Exit codes（契约的一部分）

| code | 常量 | 什么时候 |
|---|---|---|
| 0 | `EXIT_OK` | 成功，stdout 是合法 bundles JSON |
| 1 | `EXIT_BAD_INPUT` | stdin 不是合法 JSON / 缺 `queries` key / 字段类型不对 |
| 2 | `EXIT_INTERNAL_ERROR` | `search_many` 抛异常（这不该发生因为 search_many 本身 swallow 异常，但兜底）|

父进程只 care "是不是 0"——非 0 就作为 `_RunnerFailure` 触发重试。

## 设计决策

### 为什么不做 argv 传递

早期草案用命令行参数传 queries。问题：
- queries 可能很长（多个句子）→ argv 长度上限不确定
- Shell escaping 不安全，哪怕 `create_subprocess_exec` 不走 shell，JSON 本身也是受控格式
- JSON 能直接携带嵌套（`max_results` 整数）不用再序列化

用 stdin JSON 更简洁、更通用。

### 为什么不用 `-u`（unbuffered）

子进程只写一次 stdout（最后 `json.dump`），而且立即退出——不是长期流式进程。不需要 unbuffered。

### 为什么 `asyncio.run(_main())`

`search_many` 是 async 的（它内部 `asyncio.gather` 并发跑 queries）。子进程里必须起一个 event loop 才能 await 它。`asyncio.run` 是一次性用最简洁的方式，程序退出时自动清理 loop。

## Gotcha / 边界情况

- **子进程的 cwd / env 继承父进程**。compose 里 HOME / 代理变量都是继承过来的，不需要手动处理
- **空 queries 是 happy path**：`search_many([], ...)` 直接返回 `[]`，子进程 10ms 内退出。**整个链路跑一次 network 都没碰**——测试里正好用这个做 end-to-end smoke test
- **异常绝不往外抛**：所有路径都要 `return` exit code，让主进程有机会看到非零码触发重试。如果异常泄漏，父进程会看到 subprocess crash 但没有清晰的 stderr 信息

## 新人易踩的坑

- **不要在这里写 NarraNexus 的 import**。每多 import 一个 heavy module，subprocess cold start 多几十毫秒。本文件已经被刻意限制到只 import `asyncio / json / sys` + 一个兄弟模块
- **不要在这里改 exit code 语义**——父进程的 `_spawn_runner` 按 `returncode != 0` 判断失败，和这里的三个 code 对齐
- **测试时：** 直接 `from ... import web_search_runner as runner; await runner._main()`——monkeypatch `sys.stdin / sys.stdout / runner.search_many` 即可，不用真起子进程
