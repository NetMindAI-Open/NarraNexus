---
code_file: plugins/builtin.browser/src/narranexus_plugins/browser_module/_browser_module_impl/tools.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

上限在工具说明里以**字符数**表述（65536 / 4096），不用 KiB：常量计的是字符，模型按字节估算多字节文本会少填。

## 2026-10-08（PR #410 review）

`policy_context` 不再暴露 `allow_history_access`（从未被执行的权限，告诉 agent 等于误导）。
scope 注释改为如实表述：turn/thread 是运行时自述值，与 agent_id 同信任级、不受身份签名覆盖，只用于
审计归属与把登录请求、视觉观察绑定到调用轮次，不授权任何东西（review I4）。工具说明写明参数上限。

## 2026-09-24 浏览器视觉输入

`browser_look` 返回原生 MCP `CallToolResult`：一段 JSON 元数据（observation_id、视口、裁剪区、图片尺寸）
加一张 `ImageContent`，图片本身交给模型，不走 artifact 引用、不把 base64 塞进 JSON。失败只回文本且
`isError`。`browser_act` 新增 `observation_id`：带它时 x/y 是**图片像素**，由会话换算回视口坐标。
工具说明写明输出封顶约 1568px / 1.15MP，想看清细节应缩小区域而不是只调 scale。

## 2026-09-23 多页面支持

browser_tabs 在相同身份/scope 边界列出、选择、关闭页面；列举不等待人工交还，改变
页面的操作由 Session 仲裁。browser_open 的 new_page 显式保留原页，默认继续当前页。
status 返回页面集合，动作返回实际页及活动页。打开工具文案移除历史站点授权提示。

# Browser MCP dispatcher

Every browser verb shares the same caller-scope, runtime-readiness, session and
error handling. The public bearer parser yields agent_id, event_id, thread_id
and user_id from the transport. These are never accepted as model-supplied
scope parameters. The base MCP wrapper still resolves the agent and checks
ownership before invoking these handlers.

The whole call runs in an awaited child task. BrowserSession.bind_scope uses a
ContextVar, so concurrent turns stay isolated and cancellation propagates;
there are no detached jobs or retry ceilings. Open passes the scope to
BrowserService and binds the returned session again. Status, install guidance,
read, fixed actions, run, control, login and evidence also bind every cached session.

State inspection does not create a browser. Missing runtime returns the
service's installation action; missing session points to browser_open. Status
includes configured policy but never grants from another turn. Optional policy
inspection errors cannot hide runtime installation guidance.

Login requests persist an in-app notification through BrowserService, wait for
explicit human handback, and return a fresh web-page read. They neither
collect credentials nor steal a connection's control token. Screenshot
capture checks URL support and control ownership; only successful capture reaches
the artifact helper. The helper returns artifact metadata, not raw image data.

The module imports no other builtin module or private platform implementation.

browser_read 透传 selector/offset，并说明通过 next_offset 连续读取、通过 selector
缩小范围，以及使用快照里的目标调用 browser_act；不再要求特权脚本补全截断内容。
这些参数不携带身份或可执行代码；验证与固定表达式由 session/read 实现负责。

browser_request_login 传递当前可信 turn/thread 到 service。用户已在控制浏览器时仍
能立即创建通知，一次显式交还后自动重新读。完成只标记 handoff，login_state 仍待
agent 根据快照判断；断线不算成功，也不发送实际 IM。

browser_act forwards only fixed action names and supplied selector/text/key/
value/coordinate parameters to session.act. It shares the scope/readiness/error
path and leaves policy, validation and human-control serialization to the
session. Routine actions must remain usable without site permission even
when arbitrary scripts are denied. Parameter values are never built into a
model-supplied JavaScript expression in the module.
