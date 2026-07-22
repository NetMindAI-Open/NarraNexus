---
code_file: src/xyz_agent_context/agent_framework/cloud_policy.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 (PR review 加固) — actor_is_staff 改必填

两个 slot 写入器的 `actor_is_staff` 从 `Optional[bool] = None` 改为
**keyword-only 必填**：默认放行 = 静默绕过（manyfold 缺口的形状），现在
bypass 必须在调用点显式写 `actor_is_staff=None`，review 一眼可见。生产内部
调用方 4 处（OAuth 自动绑 ×2、onboard ×2）+ 测试 ~30 处已显式传参；
providers.py 的 default_slots 循环改传真实角色（防御纵深，不算 bypass）。

# cloud_policy.py — 云端 netmind-only 槽位策略的唯一真源

## 为什么存在

2026-07-17 的 netmind-only 策略最初在两个路由文件各写一份内联判断；code
review（2026-07-18）发现 manyfold 的跨用户 provider 克隆完全绕过了它——散写
的直接代价。本模块把**谓词 + 文案 + 违规异常**收拢到一处，规则变更只改这里。

## 提供什么

- `netmind_slots_only(actor_is_staff)` — 部署×角色谓词（cloud && 非 staff）。
- `ensure_slot_provider_allowed(prov, actor_is_staff)` — 绑定检查；
  `actor_is_staff=None` = 受信内部调用方（onboard / OAuth 自动绑 /
  provisioner，策略在上游已定）直接放行；`prov=None`（行不存在）也放行——
  not-found 是写入器自己的错误。违规抛 `CloudPolicyViolation`。
- `CloudPolicyViolation` — 策略违规（路由映射 403），区别于写入器的
  `ValueError`（坏输入 → 400）。
- `NETMIND_ONLY_DETAIL` / `FRAMEWORK_LOCKED_DETAIL` — 用户可见文案。

## 消费方（改规则前先扫一遍）

- `UserProviderService.set_slot` / `AgentSlotService.set_agent_slot` —
  经 `actor_is_staff` 穿参在写入点强制（含 per-agent 框架钉选门禁）。
- `backend/routes/providers.py` — onboard register-only、default_slots 跳过、
  框架切换 403 文案。
- `backend/routes/manyfold_agents.py` — 跨用户克隆过滤。
- 前端孪生：`frontend/src/lib/agentFramework.ts` 的 `cloudNetmindOnly()`。

## 坑

- staff 判定不在本模块（角色来自 request.state，由路由传入布尔）——保持本
  模块为纯 env 叶子，可单测、无 FastAPI 依赖。
