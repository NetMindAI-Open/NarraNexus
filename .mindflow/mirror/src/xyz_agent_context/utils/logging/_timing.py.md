---
code_file: src/xyz_agent_context/utils/logging/_timing.py
last_verified: 2026-07-28
stub: false
---

# _timing.py — `timed(...)`：一个东西同时当装饰器和上下文管理器

`timed(name, level=, slow_threshold_ms=)` 返回 `_Timed`，既能 `with` 又能
`@`。装饰时按 `inspect` 判形态分发四种 wrapper（sync / coroutine /
sync generator / **async generator**）——调用方不需要自己选。出口日志三态：
`ok` / 超过 `slow_threshold_ms` 升 WARNING / 异常走 `logger.exception` 后
**原样抛出**（被计时的代码语义必须与不计时时完全一致）。`.tag(**kv)` 只在
上下文管理器形态可用（装饰器形态拿不到那个对象）。

## 2026-07-28 — asyncgen wrapper 必须 `aclosing` 被包裹的生成器（真 bug）

原实现是 `async for item in fn(*a, **kw): yield item`。**`async for` 不会关闭
它迭代的生成器**：消费者对外层 wrapper 调 `aclose()`（或直接丢弃、被取消）时，
GeneratorExit 只落在 wrapper 自己的 yield 上，**被包裹的那个生成器只是继续挂
着**，它的 `finally` 要等 asyncgen 的 GC finalizer hook 才跑——实测 `aclose()`
之后再 `sleep(0)` 都还没跑。

后果不是日志问题，而是**清理契约问题**：任何 `@timed` 异步生成器里"用 finally
释放资源"的写法都从确定性变成了"迟早会跑"的承诺。发现路径：step_3 的
resume 句柄租约（[[step_3_agent_loop.py]] 并发 resume 守卫）在显式 aclose 后
仍未释放。

修法：`async with aclosing(fn(*a, **kw)) as agen: async for item in agen: ...`
——关闭立刻穿透到被包裹的生成器，其 cleanup 在同一个 await 内跑完。正常消费 /
内部抛错 / 中途 aclose 三条路径均验证过（含被包裹 finally 里有 await 的病态
情形）。钉子：tests/utils/logging/test_logging.py
`test_decorator_async_generator_closes_the_wrapped_generator`。
