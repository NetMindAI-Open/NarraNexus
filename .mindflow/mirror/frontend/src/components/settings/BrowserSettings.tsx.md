---
code_file: frontend/src/components/settings/BrowserSettings.tsx
last_verified: 2026-09-23
stub: false
---

# BrowserSettings

本地增加“显示浏览器窗口（有头模式）”开关，使用相同的登录 profile，关闭为无头。
界面说明默认无头、下次启动生效、登录状态保留，并在开关旁提示部分网站不支持无头：
遇到不支持或访问报错时，手动开启有头模式、重新启动浏览器后重试。
来源与模式共用保存中的互斥状态、请求版本和
错误处理，避免旧查询回写或重复点击覆盖已保存选择；只有成功回执改变控件值。
云端隐藏本地运行控件，修改模式不触碰当前会话。

本地运行时状态提供来源下拉选择：托管 Chrome for Testing 或本机正式版 Chrome。
保存期间禁用冲突操作，成功回执才更新选择，失败保留原值并显示原因；未完成的旧状态
请求不能覆盖保存结果。说明新会话才生效、当前会话继续、两个来源分别保存登录态。
系统 Chrome 不可用时引导选择或安装正式 Chrome，不展示下载另一种浏览器的按钮。

Agent refusals direct users here to install the browser runtime. Both settings
hosts use this component, and all default requests go through authenticated API
methods. A failed status lookup is an announced, retryable fault, never evidence
that the runtime is absent. Missing and broken installations have distinct labels.

The install POST remains pending for the duration of the installation. That
pending request prevents another install even before status says "installing".
Progress polls run serially; component cleanup and request revisions invalidate
responses from previous status sources or before an action. A failed progress
poll preserves cancellation and keeps checking, while hiding stale percentages.

Cancellation uses `cancelBrowserInstall`. Request acceptance is not completion:
the UI waits for a fresh terminal status and the install request to settle before
offering installation again. Both HTTP failures and explicit `ok: false` results
remain visible and retryable. Cancellation reported by the installer is a status,
not an install error.

NM buttons, progress bars and spinners provide consistent controls. Percentages
are shown only for downloads with a known positive total; extraction and
verification have their own indeterminate labels. The UI makes no claims about
playback controls or browser takeover. Injectable endpoints support deferred
promise tests without downloading or cancelling a real runtime.

## 2026-09-23 - 站点权限与手动恢复的生产接线

两个设置宿主共用此入口。高级脚本设置复用已有 agent 名单，默认当前聊天 agent；
更换管理对象仅改变局部选择，不改变聊天。BrowserScriptPermissions 通过统一 API
管理独立脚本能力。普通网址的权限列表、默认访问状态、允许/禁止和例外规则已全部移除。

运行时及安装响应中的 manual_install 原样送入复制组件，包含安装、检查、取消
命令以及真实 shell/安装目录。安装失败后的恢复信息独立保留，后续状态请求失败
不能把它丢掉；确认 ready 后隐藏安装恢复。客户端不拼装命令或替换安装器路径。
