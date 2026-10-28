---
code_file: src/narranexus/platform/utils/route_envelope.py
last_verified: 2026-09-10
stub: false
---
# route_envelope.py — 「信封契约」路由的唯一外层兜底

## 为什么存在（2026-09-09，B-31 复审 C1/I1/I2）

一族路由（`/api/channels/{channel}/…` 六个动词、Lark 的三条 OAuth 路由）把**所有预期失败**
——ownership 拒绝、没绑定、字段校验不过——都以 `{"success": False, "error": ...}` @200
返回，前端只看 `success`。它们缺的是对**意外**异常的兜底：驱动错误、子进程死掉、解析器的
下一个 bug 会直接穿透路由，被 Starlette 默认错误中间件变成纯文本 500，前端读不出 `error`
（#118 遗留）。第一版修复把 try/except 手抄进了 7 条兄弟路由里的 1 条（generic.py）和
Lark 的 3 条，而且 Lark 那 3 份把 `HTTPException` 也吞了——同一个 commit 里两种写法。
所以抽成一个装饰器 `structured_envelope(scope)`，六份 8 行的复制块只剩一份。

## 装饰器钉死的两条规则

1. **`HTTPException` 原样再抛。** `check_owned` 在 owner 查询失败时**故意**抛 503，让 db
   宕机在 access log 里是 5xx 可告警（`_ownership.py` 写死的不变量，PR #258 第 4 轮买回来
   的）；`CredentialConflict`→409、未知 channel→404、宿主 `AuthError` 本身就是 HTTPException。
   裸 `except Exception` 会把它们全降级成 200 信封，告警信号归零。
2. **客户端永远看不到 `str(e)`。** 驱动异常文本带 RDS 主机名、SQL 片段、容器路径。响应体是
   固定一句 `Unexpected server error (ref err_xxxxxxxx)` + 独立的 `trace_id` 字段；完整异常
   以同一个 id 进 `logger.exception`，支持侧靠 id 对账。

## 不该套它的地方

匿名入站 webhook（`channel_webhook`）：它的状态码**就是**契约——401/404/429 承载语义，
包成 200 会让投递方以为送达成功，还开了枚举 oracle。`CancelledError` 是 BaseException，
不经过它，客户端断连照常 unwind。

## FastAPI 陷阱（`_resolved_signature`）

FastAPI 0.122 的 `get_typed_signature` 用**端点函数自己的 `__globals__`** 解析字符串注解
（`from __future__ import annotations`），并不 unwrap。装饰器返回的 wrapper 的 globals 是
本模块，没有 `Request`/`BindBody`，路由会在 include_router 时炸 NameError。所以装饰时用
`typing.get_type_hints(fn)`（它会沿 `__wrapped__` 找到正确命名空间）把注解解析成真类型，
钉进 `wrapper.__signature__`；`inspect.signature` 遇到 `__signature__` 停止 unwrap，FastAPI
拿到的是真类型。`tests/lark_module/test_auth_error_handling.py::test_over_http_*` 走真
`TestClient` 钉住这一点。

## 上下游

- 消费方：`backend/routes/channels/generic.py`（schema/bind/credential/test/unbind/set-active）、
  `plugins/builtin.channels.lark/.../routes.py`（auth/login、auth/complete、auth/status）。
- 测试：`tests/backend/test_channel_generic_routes.py`（每个动词的 crash 信封、503 穿透、
  webhook 不套）、`tests/lark_module/test_auth_error_handling.py`。

## 为什么不经 `narranexus.sdk.web` 再导出（复审 PR#393 M4，决定保持现状）

装饰器的定位是 plugin router 用的东西，而仓外 plugin 的宿主 seam 是 `narranexus.sdk.web`；
lark 内置 plugin 现在直接 import `narranexus.platform.utils.route_envelope`（内置 plugin 本就大量
直接 import `narranexus.platform.*`）。没有搬进 SDK，因为它依赖 `fastapi` + `loguru`，而 SDK wheel
目前只依赖 `narranexus-contracts`——为一个装饰器给 contracts-only 的 wheel 加 fastapi 依赖不划算。
仓外 plugin 若需要同样的兜底，将来再导出时注意：`_resolved_signature` 必须继续在**被装饰函数
自己的模块**里解析注解，`test_over_http_a_503_is_a_503_and_a_crash_is_a_200_envelope` 是唯一钉住它的测试。
