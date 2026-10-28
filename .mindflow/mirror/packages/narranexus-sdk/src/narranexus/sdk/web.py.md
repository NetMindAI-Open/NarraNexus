---
code_file: packages/narranexus-sdk/src/narranexus/sdk/web.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `require_workspace_agent_owner`

转发 `WebHost.require_workspace_agent_owner`。路由要把 `agent_id` 拼进 workspace 路径时用它，
不要用 `current_user_id` + 自己拼。

# sdk/web.py — 插件路由真正调用的那扇门

`narranexus.contracts.web` 定义形状，本文件是插件写代码时 import 的东西：
一组模块级函数，每次调用时从服务定位器上取回宿主注册的那个唯一 `WebHost`
（`contracts.services:WEB_HOST`，实现见 [[plugin_sdk_host]]）。

## 为什么是模块级函数而不是一个对象

router 的函数体里没有注入点。一个 handler 手上只有 request，
所以 seam 必须是「拿着 request 就能直接调」的形态。
把 `WebHost` 塞进 `PluginContext` 也行，但 router 拿不到 context——
router 是 import 期收集的，context 是激活期造的。

## 为什么每次调用才解析

后端的路由表在 `backend.main` **import 期**就建好了，那时插件 boot 还没跑。
如果本文件在 import 期解析 `WEB_HOST`，任何 router 模块都会在导入时炸。
按调用解析的代价是一次 dict 查找，换来的是「router 模块可以在任何进程里被 import」。

## 失败是响亮的

`host()` 在没有 HTTP 宿主的进程里抛 `UnknownEntry`（worker / MCP 角色误 import 了 router）。
**绝不**降级成「放行」——一个鉴权 seam 在解析失败时放行，就是把整条路由变成无鉴权，
正是 `_ownership.py` 那段安全说明里反复强调的失败模式。

**唯一例外是 `host_settings()`**：设置是部署级事实，不是请求级事实，而它的主要消费方
（五个渠道 trigger 的 `fetch_attachments`、`narra_send_media`）恰恰跑在 workers / MCP
进程里，那里没有 `WebHost`。所以 `host_settings()` 先 `try_require(WEB_HOST)`，拿不到就
回落到读 env `MAX_UPLOAD_BYTES`（默认值同 backend，二者都来自 `contracts.web` 的
`MAX_UPLOAD_BYTES_ENV` / `DEFAULT_MAX_UPLOAD_BYTES`，不会漂移）。这不是鉴权降级：上传上限
没有「放行」语义。教训：2026-09-11 prod 上它照样 `host()` 抛 `UnknownEntry`，NarraMessenger
收到的每张图片、每个文件都被丢掉；单测全绿是因为同一测试进程先 import 过 `backend.main`，
渠道测试单跑其实是红的。
