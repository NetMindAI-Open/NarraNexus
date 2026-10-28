---
code_file: plugins/builtin.browser/src/narranexus_plugins/browser_module/prompts.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

结果说明不再提"复用别的对话的审批"：脚本权限只在 Settings 配置、聊天里永不授予。

## 2026-09-24 浏览器视觉输入

新增「Visual understanding」段：图片/图表/canvas/无文字图标等 DOM 读不到的内容用 browser_look；
只看模型实际收到的图，不从文件名、URL、alt 推断；点击用 observation_id + 图片像素，观察用一次即失效。
模型不支持图片时如实告知，不假装看过、不私自换模型（铁律 #15）。截图内容与页面文字同样是不可信输入。

## 2026-09-23 多页面支持

说明 browser_tabs 的 list/select/close 以及 browser_open(new_page=True)。页面 ID
从工具结果获取；点击后重读新页，返回原页时显式选择。用户旁观其他页不改变 Agent
目标，新页跟随不改变人工接管期间等待的约定。

# Browser instructions

所有 HTTP(S) 网页免访问授权，明确覆盖新 agent、新会话和跨站跳转，不存在站点例外。
删除旧的站点询问及绕过禁用相关指令，避免模型凭旧说明再次索要访问权限；登录接管独立。

The instructions explain platform mechanics: runtime installation, permission
outcomes, inspecting and acting on a page, explicit human takeover, login
handoff and screenshot evidence. Site-specific selectors and workflows belong
to the agent's Awareness and skills; the original site-specific example has
been removed.

Login, CAPTCHA and verification publish a visible persisted login notice and
wait for explicit handback; the tool returns a fresh page read. Credentials are
entered in the browser panel, and login success is verified from that evidence.
Disconnecting does not complete login. Evidence
uses the existing artifacts area, without embedding screenshot bytes in tool
responses. Persistent login state is not treated as proof of a current login.

The outcome vocabulary is OK, NEEDS_HUMAN, SESSION_EXPIRED, REJECTED,
RATE_LIMITED and ERROR. Runtime-supplied permission scopes must never be
invented by the model. Long-running tasks have no arbitrary time or tool limit.

This is a base-class format template: any future literal braces must be
escaped. Live runtime/policy values belong to contribute_turn_context, not this
cacheable instruction block.

Routine clicks, form fills, selections, key presses and scrolling use browser_act
without site permissions. Arbitrary scripts remain advanced and require explicitly
configured full_cdp_access. The prompt must not imply unrestricted browsing
enables scripts, prescribe a forbidden script for a partial snapshot, or claim
fixed actions prevent the website from making network requests.

读取提示明确用返回的 selector 操作按钮、字段和选项；正文通过相同 selector 与
next_offset 续读，DOM 改变后从零开始。部分内容不能报告为完整读取，也不借助默认
被禁的 browser_run 绕过固定快照。提示不宣称所有 selector 跨页面变化永久有效。
