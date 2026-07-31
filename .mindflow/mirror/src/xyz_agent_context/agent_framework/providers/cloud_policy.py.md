---
code_file: src/xyz_agent_context/agent_framework/providers/cloud_policy.py
last_verified: 2026-07-29
stub: false
---

## 2026-07-29 — 框架门禁改成按「会不会骑共享凭据」判断

新增 `CLOUD_ALLOWED_FRAMEWORKS` + `framework_allowed_in_cloud()`，取代原来
散在三处的 `framework != "claude_code"`。

**这条规则拦的是凭据骑乘，不是框架多样性**：`claude_code` / `codex_cli` 靠
CLI 读 HOME 下的凭据文件认证（`~/.claude/.credentials.json`、
`~/.codex/auth.json`），而云端镜像只有一个 `app` 用户、一个 HOME——那些文件
是容器全局的、由 staff 登录一次种下。非 staff 切到这类框架就会花 staff 的
额度、以 staff 身份行动。`claude_code` 之所以仍允许，是因为云端会给每个用户
发一张 API-key 的 NetMind 卡；真正 staff-only 的是 **OAuth 卡**本身。

NexusPower 从构造上就骑不到：它直接驱动 provider API，用的是 agent slot 上
所绑卡的 key，并且**显式拒绝** OAuth 凭据。云端非 staff 只能绑 NetMind 容量，
所以它跑起来就是跑在用户自己账上——正是本策略想要的结果。

往这个集合里加框架是**安全决策**：只有「永远碰不到共享凭据文件」的才配进。
反过来，未分类的新框架会 fail closed（有测试守）。

## 2026-07-28 — 可绑定 source 从单值变集合

云端非 staff 只能绑 NetMind 运力这条规则不变，但「NetMind 运力」现在有两个
来源：用户自己的 Power 账号（`netmind`）和平台出钱的免费钱包
（`netmind_free`，经我们的网关打到同一个上游）。

所以 `NETMIND_SOURCE` 单常量升级为 `CLOUD_BINDABLE_SOURCES` 集合。做成集合
而不是「常量 + 每个调用点写个 or」，正是为了不让这条规则第二次被内联复制 ——
本文件存在的理由本身。

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
- `backend/routes/manyfold/agents.py` — 跨用户克隆过滤。
- 前端孪生：`frontend/src/lib/agentFramework.ts` 的 `cloudNetmindOnly()`。

## 坑

- staff 判定不在本模块（角色来自 request.state，由路由传入布尔）——保持本
  模块为纯 env 叶子，可单测、无 FastAPI 依赖。
