---
code_file: src/xyz_agent_context/services/power_account.py
last_verified: 2026-07-13
stub: false
---

# power_account.py — 用户级 Power 账号判别

## Why it exists

"Power 轴"(NetMind 账号能力)的**用户级**一半。回答:对某个 user_id,NetMind
账号功能(billing/订阅/充值、Account & Subscription 面板)是否可用。部署级一半
在 [[deployment_mode.py]] 的 `is_power_login_enabled()`。

## 设计决策

- 判据:`users.user_type == "individual"`(NetMind 登录时由
  [[user_repository.py]] `upsert_netmind_user` 打上)。纯本地用户名用户是
  `"local"`,billing 路由对其干净 404。
- **不放进 deployment_mode.py**:那里是纯同步 env leaf;本判别要读库,故独立成
  service,与其它 `netmind_*` 客户端并列,给 billing 路由一个明确 import。
- **fail-closed**:falsy user_id / 缺行 / 非 individual 一律 False——基于它的
  门禁是"拒绝"而非"放行",不泄漏。
- 上游调用方:[[billing.py]] 的 `_require_power_account`(用户维度端点门禁)。

Spec: reference/self_notebook/plans/2026-07-13-本地双模式登录.md
