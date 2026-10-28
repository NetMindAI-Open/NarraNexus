---
code_file: backend/plugins_host.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 本模块 import 期发布 web seam（批 6c，G2-I1）

新增一行 `install_web_host()`（实现见 [[plugin_sdk_host]]）。挂载插件 router 与
「插件 router 能问出『谁在调用 / 他能不能碰这个 agent / 上传上限多少』」是同一件事，
所以 seam 的发布点就在做挂载的这个模块里。放在 lifespan 会留下「路由已挂、seam 未就绪」
的窗口；放在 `backend/main.py` 则漏掉所有不经过它就调 `mount_plugin_routes` 的进程。

## 2026-09-03（批 2b.4）— `LazyRouterApp`

按插件前缀 `app.mount` 一个 ASGI 小应用：首个请求才 `await activate()` 拿到 `RouterSpec`、建子 FastAPI 并
include；之后直通。认证中间件在主 app 上先跑，所以未登录请求根本不会触发激活。激活失败记住错误、
答 503（带插件 id），不在请求循环里反复重试。`mount_lazy_router` 强制前缀在 `/api/x/<id>` 之下。

## 2026-09-03（批 2a.4）— backend 宿主消费 `backend.routes`

`mount_plugin_routes(app, registries)` 在 `main.py` 里于全部壳路由之后、SPA 兜底之前调用一次：插件遮蔽不了
壳路由，兜底吞不掉插件路由。非 `builtin.` owner 的前缀强制在 `/api/x/<id>` 之下，越界只记 `refused`
不挂载；工厂抛错同样隔离（一个坏插件不能拖垮宿主）。`auth="none"` 是插件拿到公开端点的唯一途径：
前缀写进 `backend.auth.PLUGIN_EXEMPT_PREFIXES`（通过模块属性访问，测试可整体替换集合）。
没有用户插件时零挂载，`routes.json` 快照不变。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`register_builtins_for_import()` runs at `backend.main` import so builtin contributions (notably `builtin.teams`' router) exist before `mount_plugin_routes`; it drops `builtin_overrides`-disabled owners first, and the lifespan `boot` repeats the load idempotently. `start_backend_workers()/stop_backend_workers()` are the only consumer of `backend.workers` specs with `host="backend"`: they run inside the API process, one task per spec, failures isolated per plugin, stop awaited with a bounded timeout. Nothing in this file knows what teams is.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

`register_builtins_for_import` applies the same on-demand dependency probe as the boot, so a deps-missing builtin's routes/workers are not mounted at import either.

Batch 6c.3: `register_builtins_for_import` follows the distribution (`plugins_boot.distribution()`): builtins outside it drop their registrations before routes mount, bundled plugins are prepared and loaded at import like builtins.

Batch 6 fix (found running the stack): `mount_user_plugin_routes(app, registries, manifests=None)` mounts a `LazyRouterApp` at `/api/x/<id>` for every registry.json plugin declaring `backend.routes` at import time (before the SPA fallback); the first request resolves and combines the plugin's registered routers (prefix-checked, auth=none exemptions honoured), a plugin whose contributions never loaded answers 503, cloud mounts nothing.

## 2026-09-07 — public prefixes from the manifest at mount time; one exempt entry; probe-only deps at import; lazy app hygiene

A user plugin's unauthenticated endpoints are declared in backend.publicPrefixes and registered when the lazy router is MOUNTED — an external platform posting a webhook has no session, so the exemption had to exist before activation (registering it inside activation meant the middleware's 401 kept the plugin from ever activating). A RouterSpec(auth='none') whose prefix is not declared is refused at activation: a public endpoint must be in the manifest the user approved, never a value the plugin computes. Exempt / quota-bypass prefixes are stored once (backend.auth matches on segment boundaries). register_builtins_for_import only PROBES on-demand dependencies (install=False): a pip install during import of backend.main made the first desktop launch look hung with nothing serving /health. LazyRouterApp closes WebSocket handshakes instead of answering HTTP, and reset() lets a fixed plugin activate again.


## 2026-09-07 — `RoutePolicy`：manifest 是两条挂载路径**共同的**权威

`auth="none"` 和 `quota_bypass` 是「插件可以挂后端路由」这件事上仅有的两个安全/计费
旋钮，而它们各自只在**一条**挂载路径上被执行过：

- `auth="none"` 只在 lazy 路径（`mount_user_plugin_routes` 的激活闭包）对着 manifest
  的 `backend.publicPrefixes` 核过。eager 路径（`mount_plugin_routes`）直接
  `PLUGIN_EXEMPT_PREFIXES.add(spec.prefix)` —— 而 distribution 的 **bundled 插件走的
  正是 eager 路径**（`register_builtins_for_import` 在 import 期把它们连同 builtin 一起
  load）。也就是说：发行版作者第一次用这个扩展点，就能让一个 manifest 里没有任何公开面
  的插件在 boot 时把自己的前缀变成免鉴权。
- `quota_bypass` 反过来：eager 路径注册了，user 插件却是在**激活内部**注册的。中间件跑
  在 `LazyRouterApp.__call__` 之前，配额耗尽 → 402 → 插件永不激活 → 前缀永不注册，正是
  当初 `auth="none"` 那个自锁死循环。旋钮对 builtin 有效、对它本来服务的第三方插件静默
  失效——比统一忽略更糟，因为契约看起来像被履行了。

现在一个 `RoutePolicy`（`route_policy(owner, manifest, prefix)` 构造）同时回答两条路径的
三件事：`refusal(spec, name)` 判「manifest 有没有声明过」（未声明的 `auth="none"` /
`quota_bypass` 一律拒绝，eager 记 `refused`、lazy 抛 `ValueError` → 503），`register()`
在**挂载时**把声明过的前缀写进 `backend.auth` 的两个集合（必须早于第一个请求，理由同上），
`_normalise_declared` 用 `path_under_prefix` 把越界声明丢掉——计费绕过绝不能用裸
`startswith`。builtin owner 是 `trusted`：它是宿主代码，前缀由 manifest 解析器保留
（`manifest.py` 的 `builtin.` 保留 + `builtins.py` 的 `allow_builtin=True`），没有
`/api/x/<id>` 可言，所以整段规范化和检查都跳过。

`mount_plugin_routes` 多一个 `manifests` 形参。默认值是模块级 `_IMPORT_MANIFESTS`——
`register_builtins_for_import` 刚 load 过的那批，**不是**再 `discover()` 一次（import 期
第二次全盘扫文件系统）。拿不到 manifest 的 owner 得到一个空 policy：路由照挂，但运行时
自称的 `auth="none"` / `quota_bypass` 被拒 —— fail closed。

**下一个人加第三个 `RouterSpec` 策略字段时**：加进 `RoutePolicy` 一次，不要再往两个挂载点
各写一遍——被遗忘的永远是 eager 那个，因为它历史上没有 manifest 句柄。

顺带修掉激活闭包里的错误消息：它插值的是**前一个循环**的 `entry.name`。specs 现在按
`(name, spec)` 收集，报错说的是被拒的那个贡献。

测试：`tests/backend/test_plugin_route_policy.py`（两条路径 × 声明/未声明/越界/segment
边界 × `quota_bypass` 200-vs-402，全部挂真 `auth_middleware`，fixture 里保存-恢复那两个
模块级可变集合）。

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
