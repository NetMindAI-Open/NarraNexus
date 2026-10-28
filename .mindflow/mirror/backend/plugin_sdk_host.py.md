---
code_file: backend/plugin_sdk_host.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 新增 `require_workspace_agent_owner`

把 [[_ownership]] 的第三个 surface（面向 workspace 目录的严格确权）发布给插件路由；
首个消费方是 skills 路由（[[routes]]）。

# plugin_sdk_host.py — 宿主这一侧的 web seam 实现

`contracts.web.WebHost` 的后端实现，boot 时以 `WEB_HOST` 暴露在服务定位器上，
插件通过 [[web]]（`narranexus.sdk.web`）调用。它把依赖方向掰了过来：
从前是「13 个插件包 import 宿主的 6 个私有模块」，现在是「宿主发布一个对象」。

## 里面没有逻辑，这是刻意的

每个方法都只是转调既有的宿主 helper（`_ownership.assert_owned` /
`_mcp_egress.filter_public_mcp_servers` / `auth.resolve_current_user_id` /
`artifacts._token.mint` …）。在这里重写一份 ownership 判定，就是
`backend/routes/_ownership.py` 当初被抽出来要消灭的那种漂移——
那个文件的存在理由就是「重复的 ownership 逻辑必然分叉」。

`dashboard/routes.py` 的 `_resolve_viewer` / `_assert_agent_visible` 因此改成了
公开名 `resolve_viewer` / `assert_agent_visible`：它们现在是 seam 的一部分，
留着下划线只会让「私有」这个记号失去意义。

## 注册时机

在 `backend/plugins_host.py` 的**模块 import 期**调用，而不是 lifespan 的
`boot_backend_plugins()`，也不是 `backend/main.py`。挂载插件 router 和
「能回答『谁在调用 / 他能不能碰这个 agent』」是同一件事，所以发布点就放在
做挂载的那个模块里：放在 lifespan 会留下一个「路由已挂、seam 还解析不了」的窗口，
放在 `main.py` 则会漏掉所有**不经过 `backend.main`** 就挂插件路由的进程——
测试会自己造 `FastAPI()` + `Registries()` 再调 `mount_plugin_routes`
（`tests/module/test_data_access_dispatch.py` 就是这么做的，第一版放 main.py 时它红了）。
`replace=True` 让重复 import（一个进程里造多个 app）保持幂等。

## 边界

导入全部写在方法体内。本模块被 `backend.main` 顶层 import，
而 `backend.auth` / `backend.routes.*` 各自会拖起数据库栈和路由模块；
在这里做顶层 import 会把 import 图搅成环。
