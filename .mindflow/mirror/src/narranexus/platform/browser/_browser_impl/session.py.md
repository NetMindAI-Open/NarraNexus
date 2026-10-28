---
code_file: src/narranexus/platform/browser/_browser_impl/session.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

上一轮插入的脚本长度检查被编辑工具同时复制进了 `read_page`（那里不可达、提示也会误导），已删除；只有
`run_script` 校验脚本长度。

## 2026-10-08（PR #410 review）

`run_script` 先校验表达式长度（`MAX_SCRIPT_CHARS`）再等控制权；`decide` 不再传 turn/thread。
`bind_scope` 注释改为如实表述：自述 scope，只做归属与观察绑定。帧带 `mime_type`。下文提到
"授权/审批"的段落已不成立：唯一权限是 owner 配置的 full_cdp_access。

## 2026-09-24 浏览器视觉输入

`observe_page`：在页面 stream 锁内先用固定函数探测视口与裁剪区，再 `Page.captureScreenshot`，截完
再探测一次，URL/document/视口任何变化都作废。生成的 VisualObservation 绑定 page_id 与调用 scope。
`act(observation_id=...)` 只允许坐标 click/scroll，执行前重新比对页面状态，任何动作、人工接管都会
清空观察；过期即拒绝并提示重新 browser_look，绝不在变化后的页面上猜坐标。

## 2026-09-24 人工新建与导航

人工新建空白标签、地址栏导航在操作锁内再次验证连接控制权，不能调用等待 Agent
控制权的 navigate 装饰方法。新页保留旧页，设为活动页；导航核对 page_id，迟到的
旧页请求不跳转到另一标签。固定 HTTP(S) 校验、按键释放及审计脱敏沿用现有规则。

## 2026-09-23 多页面支持

首帧截图与 resize 共用每页 stream 锁，防止 Chrome 截图结束时恢复旧 metrics 覆盖新尺寸。
stream 不等待 Agent 操作锁，长脚本执行期间仍可观看；resize 继续服从既有控制权校验。
观看中的页面改变尺寸后，在同一 stream 锁内补一帧，保证静态页画面及时更新；重复尺寸
和未观看页不额外截图。

BrowserPages 管理所有目标，_cdp 指向 Agent 活动页。页面变更与动作共用操作锁，鼠标
按下/释放不会跨页。Agent 动作前后处理已到达的目标事件，结果报告实际 page_id 及页面
列表。新开、切换、关闭仍等待人工交还。每页独立维护 viewport、缓存、stream 锁与订阅。
旁观切页不改变 Agent 目标；接管切页先释放旧页按键，迟到的旧页输入拒绝。最后一个
观看者退出后清缓存，重连从 renderer 获取新首帧；关闭标签只影响目标页。

# Session permission and control boundary

MCP tools and stream viewers share this session. Agent operations are serialized
and wait without a deadline while a human controls it. Handover and user input
use the same lock; only the owning connection drives. Release emits key-up and
mouse-release for held inputs before returning control to the agent.

Panel resize uses the same action lock and rechecks ownership after acquiring it.
While the agent holds control any viewer may resize; during human control only
the owning connection may do so. Spectators return immediately instead of waiting
for takeover to end. Integer dimensions clamp to 240..1920 by 240..1440, with DPR
fixed at one and mobile emulation disabled. Repeated sizes do not reset metrics.

bind_scope stores trusted turn/conversation identifiers in a ContextVar. It must
be called in each tool task: session reuse cannot reuse an earlier turn's grant,
and concurrent tasks cannot borrow each other's audit scope.

_record emits a metadata whitelist with trusted current scopes. Full URLs become
origins; selectors, form values, scripts, page content, arbitrary action names and
raw exception text are excluded. BrowserService supplies the default durable
sink and owns its ordered writes, responsiveness probe and shutdown drain.

普通导航、读取、固定操作和截图只校验 HTTP(S) URL 与控制权，不读取访问策略或创建
审批。移除 on_ask 回调及访问判定，历史禁止记录不能阻断这些操作；跳转后的页面
同样适用。非 HTTP(S) 仍拒绝。截图返回 PNG，继续使用既有 evidence/artifact 链路。
仅任意脚本读取最新高级权限，刷新失败返回 ERROR，不使用缓存绕过撤销。

read_page 接受 selector/offset/limit，只调用固定快照函数。文本分页在页面内完成，
避免先传回全部长正文；元数据给出当前总长与 next_offset。无匹配、参数非法或页面
异常返回 ERROR，部分页有明确提示。操作锁、当前 origin 和人工控制门控保持一致；
分页与普通控件定位不要求脚本权限。

act provides click/fill/select/press/scroll with the same web-URL and control checks.
It uses validated JSON arguments in fixed DOM expressions and CDP input; callers
cannot supply executable fragments. Press and click release their inputs even if
cancelled. Ordinary browsing enables search and forms without an
arbitrary-script grant. Selectors address the main document; coordinates address
the visible viewport. Page handlers retain their normal site behavior.

This is not a browser network sandbox. A normal page can load resources and
redirect independently of navigation. Arbitrary page JavaScript additionally
requires deliberately configured full_cdp_access because it can send requests,
submit forms or navigate; unrestricted browsing never authorizes it. Browser
launch disables filesystem downloads. Separate upload/download policy fields do
not imply an implemented sandbox for unrestricted scripts.

Screencast startup is idempotent, the latest complete image is replayed on
subscription, and CDP liveness participates in is_open. Close is shared and
cancellation-safe; disconnecting one viewer never closes the agent's browser.
