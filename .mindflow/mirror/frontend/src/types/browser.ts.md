---
code_file: frontend/src/types/browser.ts
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

删除 `ApprovalLifetime` / `PendingApproval` / `BrowserPendingRequest`；`BrowserPolicyVerdict` 收紧为
`'allow' | 'deny'`（后端不再产生 `ask`）。

## 2026-09-23 多页面支持

BrowserPage 是当前会话页面的 id/title/url/opener_id，不是 artifact 或访问授权规则。
流的页面集合与控制权相互独立，同一个浏览器可供不同连接观看不同页面。

# 浏览器 API 数据

HTTP 运行时状态附带 selection：来源、能否编辑、本机 Chrome 路径。它描述新会话
将采用的运行时，不代表已经启动的会话被切换；来源仅允许 managed/system。
selection.mode 仅允许 headed/headless，表示下次启动的运行方式，与来源相互独立。

前端共享运行时、安装进度和审批的数据结构，避免面板、设置和全局审批提示各自
猜测协议。progress 的总量与百分比可以为空；审批的 allowed_lifetimes 来自后端
可信上下文，缺少 turn/thread 时不能显示无效的临时授权选项。

权限管理视图只包含 full_cdp_access，不再有 access、临时访问授权计数和访问能力类型。
高级脚本只接受明确 allow/deny，不能通过普通审批开启。PendingApproval 仅允许独立的
downloads/uploads/auto_review，不存在打开网址的审批类型。

BrowserPendingRequest 将站点审批和持久化 login 通知明确区分，通知不能作为审批
提交。BrowserManualInstallHelp 对齐安装器返回的 shell、路径、命令与镜像配置；
前端只展示原文。安装响应和状态响应均可携带恢复信息。
