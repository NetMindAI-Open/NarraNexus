---
code_file: src/narranexus/platform/browser/browser_service.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

审批相关方法删除；`pending_notices` 只读登录请求。新增 `installing` / `install_progress` /
`forget_session` 公开访问器，路由不再触碰私有字段（review I6）。`policy_for(fresh=True)` 的理由改为
"Settings 在 API 进程写、会话在 MCP host 读"。

# Browser service ownership

新会话默认读取保存的本地运行模式；内部调用仍可显式传入 headless。配置错误以
结构化 ERROR 返回，不偷偷换模式。已存在会话先返回，即使偏好改变或配置损坏也
不重启正在使用的浏览器。有头和无头继续使用同一个来源对应的 agent profile，
通过同一套内嵌画面与输入通道运行；原生窗口会额外显示，属于用户的明确选择。

2026-09-23：默认 locator 读取本机来源选择，正式 Chrome 与托管浏览器继续使用相同
的内嵌运行方式。按实际可执行文件为正式 Chrome 使用 system profile，托管版本仍为
default，防止跨版本降级 profile。已有会话先返回，不因来源配置变化而重启；系统
浏览器不可用时引导重新选择来源，不能用下载托管浏览器假装修好了系统 Chrome。

This is the common runtime gate for the UI and MCP tools. Installation remains
delegated to its existing coordinator; this service does not duplicate download
or installer ownership.

Each agent has one live session. Open and close use the same per-agent lock, so
concurrent opens cannot launch two processes against one profile and a close
cannot miss a pending launch. Dead sessions are hidden from lookup but retained
until cleanup has completed before replacement. Host shutdown calls close to
drain sessions, including launches already holding their agent lock.
Shutdown rejects new launches, shares one cancellation-safe drain task and waits
for every session even if another close fails. Cleanup errors are reported only
after all sessions have been attempted, so one storage outage cannot orphan the
remaining browser processes.

Privileged permission decisions read fresh storage. Database failure or malformed policy
data cannot silently replace a stored deny with a default or stale allow.
Approval mutations are delegated exclusively to ApprovalStore's atomic update;
there is no cached-document save path that could overwrite another answer.
Owner settings use PolicyStore to update one implemented capability atomically,
invalidate the local cache and emit a policy_updated service-audit event. The
public view exposes only advanced-script permissions.

Session scopes belong to individual tool tasks. Reusing an open session binds
the caller's turn and conversation for audit attribution. MCP tools
must bind_scope on every invocation, including reads and screenshots.

Normal launches always receive a durable ServiceAuditor sink using the existing
service_audit table (service="browser"). Agent and per-launch session identifiers
correlate lifecycle, control, policy decisions, actions and errors. Writes remain
ordered; asynchronous failures and false write outcomes are observed, counted and
logged. This follows the repository's advisory policy: storage failures do not
interrupt browser work or pretend the row landed. Custom audit callbacks remain
optional observers in addition to the durable sink.

The service wraps the launched session's close and responds to its closed audit
event, so explicit shutdown and transport-initiated shutdown both stop the probe
and drain pending writes. A stopped record is emitted after process/session close
finishes, and service shutdown drains all tracked observation tasks. Lifecycle
records identify the opening scope; background control and probe records do not
borrow the first turn's scope. Action decisions carry the caller's current scope.

L2 health evaluates the fixed expression 1 through CDP immediately and every 60
seconds. Successful probes write heartbeat with latency and frame counters;
failed or five-second-unresponsive probes write error instead. The probe does not
take the action lock, read page content, cancel an agent operation, close the
session or impose any agent deadline. Lack of frames alone is not unhealthy.

## 2026-09-23：持久登录通知与人工交还

request_login 通过独立 BrowserLoginRepository 创建通知；pending_approvals 合并独立能力
审批及 kind=login 通知，供后端同一所有权 API 返回。网址访问不会产生通知。登录通知不修改权限，也不含
凭证或页面内容。工具等待显式人工交还后执行正常 read_page，返回新快照及
handoff=completed/login_state=unverified，不能把交还当作成功登录。

创建通知前不读页面。已有人工 owner 在第一次存储 await 前被记录，创建与 stream
回调使用同一 agent 锁，使交还不能越过初始 owner 的落盘；已有接管只需交还一次。
只有同一会话、实际拥有控制权的连接能完成，断线恢复 pending。会话替换、工具取消
及正常关闭退役请求；新会话第一次请求清理旧会话遗留。异常退出或数据库持续失败
不保证通知即时清理，但绝不视为完成。

进程内请求 ID 到会话代号的映射只用于唤醒存储失败的等待者，不替代跨进程持久通知。
控制回执失败即标记这些已知请求为 ERROR，兜底不再次读取数据库。没有活跃登录请求
时普通接管不访问登录表。最终读取校验 HTTP(S) 和控制权，跨站登录不再触发访问授权。

启动不读取网站策略或注入审批回调。普通浏览不受权限存储故障影响；脚本操作按需
通过 policy_provider 获取当前策略。显式传入的策略仍保持权威，不被刷新器替换。
