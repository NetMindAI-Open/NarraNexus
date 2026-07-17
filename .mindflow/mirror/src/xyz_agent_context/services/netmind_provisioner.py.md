---
code_file: src/xyz_agent_context/services/netmind_provisioner.py
last_verified: 2026-07-16
stub: false
---

## 2026-07-16 — 铸 key 时捕获 NetMind 账户身份

`ensure_netmind_provider` 在 `onboard_one_key` 成功后,用手上的登录 JWT 调
`NetmindAuthClient().verify_token(token)` 拿到 `user_system_code` + email,写进
`user_providers.netmind_account_id/email`(该用户所有 `source='netmind'` 行——onboard
可能建 anthropic+openai 双 linked 行)。**只存非密的账户 id/email,绝不存 JWT。**
best-effort:捕获失败绝不让 provisioning 失败(key 已铸+已 onboard),账户留 NULL。

为什么:key→账户链原本在铸 key 处就断了(minted key 不透明、不带账户),导致用户分不清
几把 key 属于哪个账户、充错账户。现在 Settings→Providers 能显示每把 key 的账户邮箱。见
`.mindflow/project/references/netmind_billing.md`。

# netmind_provisioner.py — 登录即自动注册 NetMind provider（register/activate 分离）

## 为什么存在

云端登录**就是** NetMind 登录，所以登录后用户的 NetMind 额度应当"开箱即用"，
不该再要一个"用此账户驱动"的手动按钮。这个模块是**唯一**一处：为用户 mint 一个
NetMind 推理 key（走 [[netmind_key_client]]）并创建 netmind 双 provider
（anthropic + openai）。被两个调用方共用：

- 登录处理器 [[auth]]（每次 NetMind 登录 fire-and-forget 触发），
- 显式的 `POST /api/providers/use-subscription` 路由（[[providers]]，现在只是兜底）。

## register vs activate（核心拆分）

- **register 永远做**（只要用户还没有 netmind provider）：mint + 建两行 provider，
  让 LLM Providers 里出现一张 NetMind 卡。
- **activate（绑定 agent/helper 槽）只在用户没有完整可用配置时做**。已经配了自己
  provider 的用户**不被劫持**——NetMind 卡只是"可切换"，不抢占。判定用
  [[provider_resolver]] 的 `_is_user_config_complete`。落到
  [[user_provider_service]] 的 `onboard_one_key(..., activate=...)`：`activate=False`
  时只 `add_provider`，不动 framework/槽。

## 为什么优先级安全

扣费顺序是"系统免费额度（`prefer_system_override` 默认开）→ NetMind 订阅赠额 →
NetMind 余额"，所以**自动接入永远不会在免费额度耗尽前花钱**。这也是敢在登录时
默默 register+activate 的前提。

## 关键防线

- **flag 门控**：`settings.netmind_use_subscription_enabled` 关 → 直接 no-op。
- **先去重再 mint**：`user_providers` 里已有 `source=netmind` → 返回 False，
  绝不重复 mint（重复 key = 重复烧钱）。
- **孤儿 key 清理**：mint 成功但 onboard 失败 → best-effort `delete_key` 再抛原异常，
  不留下会花钱的孤儿 key。
- **fire-and-forget 非致命**：`schedule_ensure_netmind_provider` 用 `create_task`
  且**挂 done-callback**（事故教训 #2：裸 task 的异常只在 GC 时 warning）。登录
  绝不因 NetMind mint 失败而阻塞或失败。
- **绝不打印** loginToken / 生成的 apitoken。

## 待办（翻 flag 前）

进程内 per-user `asyncio.Lock` 只在单 worker + flag 关时够用。多 worker 部署开
flag 前，要换成覆盖**所有** netmind-source 创建者（本模块 + add_provider/onboard）
的分布式锁；`_locks` dict 目前无界，同时要换有界/TTL 结构。
