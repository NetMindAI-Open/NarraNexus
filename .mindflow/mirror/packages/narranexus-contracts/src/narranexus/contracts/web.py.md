---
code_file: packages/narranexus-contracts/src/narranexus/contracts/web.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `WebHost.require_workspace_agent_owner`

与 `require_agent_owner` 并列的第三个确权方法，语义更严：身份必填（401）、路径不安全 /
不存在 / 非本人统一 404、查库失败 503，返回 user_id。给任何要把 agent_id 变成工作区目录的
插件路由用；来自 2026-10-28 跨租户读事件。

# contracts/web.py — 让插件路由不必伸手进宿主的私有模块

## 它为什么存在

批 6b 把 13 个 router 从 `backend/routes/` 搬进各自的插件包，**但依赖没跟着搬**：
它们继续 `from backend.routes._ownership import assert_owned`、
`from backend.auth import resolve_current_user_id`、`from backend.config import settings`、
`from backend.routes.dashboard.routes import _assert_agent_visible`。
搬家把耦合的**性质**弄坏了：搬之前是「backend 自己的文件用自己的私有工具」（合法），
搬之后是「一个独立打包的 wheel 用宿主的下划线私有模块」（`docs/API_POLICY.md` §1 明令禁止）。

真正的代价不在洁癖上，而在生态上：`builtin.job` 是文档指定的 router 样板，
第三方照抄它会发现 `assert_owned` / `resolve_current_user_id` 一个都 import 不到。
剩下的两条路都很糟——自己重写 ownership 校验（2026-08-12 那批 IDOR 就是这么来的），
或者去 import 我们随时会改名的私有模块。

## 为什么是「请求作用域」而不是传 user_id

`require_agent_owner(request, agent_id)` 收的是 request，不是已经解出来的 user_id。
因为 local / cloud 两种模式下「当前用户是谁」的差别整个封在宿主中间件里
（local 模式甚至**不做**鉴权，这是设计，见 `backend/routes/_ownership.py` 的
SECURITY POSTURE 段）。如果 seam 只收 user_id，每个插件都得自己复现那个模式差异，
等于把中间件存在的理由推回给插件。

两个 ownership 出口（`require_agent_owner` 抛 HTTP 错、`check_agent_owner` 返回错误串）
是照抄宿主既有的双出口：channel 路由用 `{"success": false, "error": ...}` 载荷回答，
不是 HTTP 状态码。两者从**同一个**判定映射，谁也不去解析对方的散文。

## 三个刻意的取舍

- **framework-free**。本包不依赖 fastapi（和 `contracts.route.RouterSpec.router` 一样，
  `request` 是 `Any`）。所以 `AuthError` 在这里只能是一个纯 Exception 基类，
  真正要抛的实例由宿主的 `auth_error(code, detail)` 工厂给——宿主的
  `backend.auth_errors.AuthError` 继承本基类，所以插件 `except AuthError` 成立，
  而宿主注册的 exception handler（把 `code` 渲染进响应体）依然生效。
- **`HostSettings` 故意只有一个字段**。八个插件调用点要的全是 `max_upload_bytes`。
  写成整个 `backend.config.Settings` 的 Protocol 等于把宿主配置的每个字段都变成契约。
  加一个字段是一次契约变更，这正是想要的摩擦。
- **上传上限的来源常量也在这里**：`MAX_UPLOAD_BYTES_ENV = "MAX_UPLOAD_BYTES"`、
  `DEFAULT_MAX_UPLOAD_BYTES = 50 MiB`。backend `config.settings` 和 SDK 在无 HTTP 宿主进程里的
  回落都用它们，保证 backend / workers / MCP 三种进程读到同一个值。
- **`artifact_view_token` 归宿主**。token 就是宿主那条 public raw 路由的鉴权本身；
  让插件拿到签名密钥等于复制一份宿主的信任边界。`filter_public_mcp_servers` 同理——
  出网策略是宿主的决定，不是每个插件各自的判断。

## 稳定性

`API_VERSIONS["web"] = 0`，`STABILITY["web"] = ALPHA`，理由写在 `docs/API_POLICY.md` §2：
它只有一个批次的年龄，ownership 与 visibility 的切分还在评审中。为了好看标 STABLE
就等于承诺一个我们还兑现不了的废弃窗口。

实现方在 [[plugin_sdk_host]]，插件侧调用门面在 [[web]]（`narranexus/sdk/web.py`），
ref 在 [[services]]（`contracts/services.py` 的 `WEB_HOST`）。
