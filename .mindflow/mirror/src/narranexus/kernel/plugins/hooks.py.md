---
code_file: src/narranexus/kernel/plugins/hooks.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2e）— `declare` 对相同 spec 幂等

内核词表可能被多条路径声明（Registries 构造 + 测试/宿主显式声明），完全相同的 spec 返回既有 caller；签名不同才
`RegistryConflict`。

## 2026-09-03（批 2a）— `HookImplSpec` + `@hookimpl(name)`

插件在 manifest `backend.hooks` 指向的 `HOOKS` 里放的是 `HookImplSpec`（hook 名 + 函数 + 三个 flag），
不是 `HookImpl`：owner 由 loader 从 manifest id 盖章（插件不能替别人实现钩子），accepted 参数由 caller 从
spec 推导。装饰器形式让声明处一眼看出实现的是哪个钩子。

## 2026-09-03（预审修订）— `freeze()` 真实存在；失败 wrapper 的生成器被关闭

`HookCaller.freeze()`/`HookRegistry.freeze()` 落地（之前 `_frozen` 字段悬空）：冻结后 `add` 抛
`RegistryFrozen`，冻结后 `declare` 的 caller 也是冻结态。wrapper 的 before 半程抛异常时现在
`close()`/`aclose()` 该生成器，让它的 `finally` 跑到（`_close_generator`）。`i.wrapper == wrappers`
取代 `is`。

## 2026-09-03 — many 位的机制：HookSpec / HookImpl / HookCaller（pluggy 语义）

与 `registry.py`（one 位、单提供者）互补：一个调用点、任意多插件参与（spec §5.3、D16）。
不引 pluggy 依赖而是自写一份，原因是我们要 async 实现、per-owner 错误隔离、`block(owner)`
与 `Disposable`，pluggy 三者都不给。语义照抄 pluggy 因为它是被数千 pytest 插件验证过的：
LIFO 注册序 + `tryfirst/trylast`；`wrapper` 是恰 yield 一次的生成器（同步或 async 生成器都行），
外层先 before、内层先 after，看到合并后的 `HookOutcome`；`firstresult` 首个非 None 即停；
实现只声明自己用到的参数（`_accepted_params` 按签名裁剪，`**kwargs` 视为全收），所以
spec 加参数不破坏旧插件（参考文档 §B-15）；实现声明了 spec 没有的必填参数在 `add` 时就
`TypeError`，不留到调用期。
错误处理是「隔离不吞」：每个失败记进 `outcome.errors[(owner, exc)]` 并 warning，其余照跑
（宪章 5 复杂度下沉：重试/超时不在这里，在总线/回合预算层）。yield 两次的 wrapper 记错不炸。
`HookRegistry` 是名字→caller 的表：spec 只能 declare 一次（`RegistryConflict`），未声明的名字
`UnknownEntry`（拼错 fail-loud）。批 2 起 `contracts/events.py` 的九个事件在这里 declare。

## 2026-09-04 · ingress triggers (batch 3c.3)

`HookCaller.add` is idempotent for the same (fn, owner): import-time registration (`module/contributions.register_all`) and the manifest-driven load name the same function and must yield one implementation, not a double call. A different owner adding the same fn is still a second implementation.

## 2026-09-07 — 注释去掉已删的 register_all（round-2）

幂等 add 的动机是测试热重载再导入，不再是导入期注册。
