---
code_file: src/narranexus/platform/services/background_llm_alerts.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11（review r2 I2/I3/M5/M6）— 余额先于凭据判定；余额通知按 owner 去重

- **判定顺序**：先问 `_out_of_credit_reason`，再退回 `is_credential_error`，与 `classify_self_serviceable`
  「invalid_credentials 放最后」的排序一致。凭据正则很宽（裸 `api key`、独立 token `401`/`403`），
  「403 Forbidden: your credit balance is too low」或回显 key 的额度报错以前被判成凭据、让没钱的 owner 去查 key，
  审计行 category 也记错。正文的 `problem` 按 category 判。
- **去重粒度按类别**（`_dedup_key`）：凭据类仍是 `(agent_id, source_id or agent_id, category)`；
  `provider_balance` 收敛为 `(owner_user_id, "owner", "provider_balance")`——余额是 owner 账户的一个事实，
  按 source 扇出会让一次余额耗尽给每个 narrative / 实体候选名各发一条、每 30 分钟一轮。
  文案/标题里的 `source`、`MessageSource.id` 不变（运维定位用），收敛的只是去重键。
- docstring：函数摘要行改成「凭据或余额」；source 清单里 `_entity_updater.py` 的路径改为插件化后的真实路径。

## 2026-09-11 — 余额耗尽也发 owner 通知；新增 source `team_summary`

Tier-2 的判定从「凭据类」扩到「owner 能自己修的」：`is_credential_error` 之外，再用共享分类器
`classify_self_serviceable` 判 `OUT_OF_CREDIT_REASONS`（`insufficient_balance` / `free_tier_exhausted`），
命中则 category=`provider_balance`、发收件箱通知。起因是 2026-09-07 prod：团队总结因 owner 的 NetMind
余额为零连续三天 400 `balance not enough`，那不是凭据错误，旧逻辑只落审计行，唯一能修的人收不到任何信号。
提示按类别给补救：凭据 → 查 helper_llm 的 key/base URL；余额 → 充值或换 provider；免费额度用完 → 升级 Nexus Pro
或换自己的 key（与 `SELF_SERVICEABLE_USER_MESSAGE` 同口径，不能对免费卡说「充值」）。
正文从「long-memory updates」改成中性的「background updates」，标题带上 source——`team_summary` 不是记忆。
影响所有调用方（narrative/post_turn/entity 链）：它们遇到余额耗尽现在也会通知 owner（30 分钟窗口，按 owner 去重，见上一节）。
message bus 的失败通知走自己的 `_classify_error`，本来就对所有类别都通知，不需要跟改。

## 2026-09-10（review r2 M5）— `_cooling` docstring 写明 fail-open 的代价

表读不到时 dedup 暂失（旧进程内 dict 不会）；自限于随后的收件箱写也会挂，且宁可重复也不静音。

## 2026-09-09（review I5）— 三处进程内冷却迁到 `owner_notice_cooldowns`

`_notify_cooldown` dict / `reset_alert_state()` / `time.monotonic` 全部删除；三个通知面
（后台 LLM 凭据失败、熔断 paused、transient 连击）改走
[[owner_notice_cooldown_repository]]：`_cooling(db, agent, target, category)` 读、
`_arm` 在收件箱写成功后写。`target` = `source_id or agent_id`（同一 agent 两个 narrative
各自失败是两件事），category 沿用 `provider_credential`/`generic`，熔断面用 `cb:<reason>` /
`cb:transient`。同 B-20.3 的三个理由：重启不再重发、多进程一致、粒度按来源。
**fail-open**：窗口读不到就通知（observer never breaks observed），`_arm` 失败只 warning。
测试改用真 `db_client`（收件箱/审计仍 fake），新增「跨实例仍压制」与「库挂了照样通知」。

## 2026-08-25 — source 标签枚举补全（行为未变）

`alert_background_llm_failure` 的 docstring 原本只列了三个 source，而
[[_entity_updater.py]] 这次接线新增了四个（`entity_dedup` /
`entity_extraction` / `description_compression` / `persona_inference`）。
运维照 docstring 那张清单去查会漏掉它们。

**补充（同轮 review）**：旧 docstring 列的三个 source 里，`memory_extraction`
**从来没有任何代码发出过**——全仓只有那一行 docstring 命中。一个查出零行的
标签没法让运维分辨「这条链健康」还是「标签被改名了」，正是这份清单想消灭的
那种摩擦，已删除并注明原因。清单旁边加了一句纪律：**新增 source 必须同一个
commit 同步这里**，否则下一个接线的人还会漏（这一轮就漏了四个）。

同时写明：**同一条因果链有两个 audit service 名**——LLM 失败走
`background_llm`，DB 写入失败走 `social_network_memory`（不是 LLM 失败、
也不打扰 owner）。「记忆为什么停更」的排查要覆盖两个名字。

本文件代码行为未变，仅补文档。

## 2026-07-13 — Agent 实时层熔断器接入

新增熔断器告警面，按**谁能处理**分流：
- `alert_agent_paused`（PAUSE 时——audit 恒写 + owner inbox，auth/quota 都发，按 (agent_id,reason) 30min 去重，文案区分 auth/quota）。
- `alert_agent_transient_streak`（provider 侧持续失败——audit + **owner** 中性知会："还在重试、这是原始错误、你自己判断",绝不说"换模型"，按 (agent_id,"transient") 去重）。
- `audit_agent_internal_streak`（BUSINESS 类：我们的 bug/永久错——**只**写内部审计 + loud `[agent-cb][PLATFORM]` error log，**绝不**发 owner，因为 owner 修不了我们的缺陷）。

复用既有 redact_secrets / ServiceAuditor / InboxRepository / 冷却 map。分流的意义：该用户
修的找用户，我们该修的找我们，谁都别被无关告警骚扰。

# background_llm_alerts.py — 后台 LLM 失败告警

## 为什么存在

脱离任务（narrative updater、Step-5 entity/memory hooks）里的 LLM 失败以前是纯静默：
`logger.exception` 后 `return None`。2026-07 事故里平台 key 过期，这些路径 401 两周
无 owner 可见信号、也无可 SQL 的痕迹，长记忆无声退化。事故教训 #3（别吞异常）、#4
（L2 健康）、#5（DB 审计）都指向同一结论：后台 LLM 失败必须留可查记录，且当它是 owner
能修的凭据问题时，发一条去重的 owner 通知。

`alert_background_llm_failure(...)` 分两级（刻意）：
- **每次失败** → 写一条 `service_audit` error 行。永远开、便宜，运维几周后仍能
  `SELECT` 出"最近 N 天多少次后台 LLM 失败"，即使日志已轮转。
- **仅凭据类失败** → 写 owner inbox 通知（脱敏 + 冷却去重）。瞬时抖动（超时/5xx）
  owner 修不了，不进 inbox，避免告警疲劳。

`source` 短标签（narrative_update / entity_summary / memory_extraction /
post_turn_hooks）进审计 detail 和通知标题。冷却 map 在进程内（重启即清），与 message
bus 的失败通知同款权衡。函数**永不抛**——观察者不得破坏被观察者。

## 上游

被 narrative updater 的 `_async_llm_update` 和 AgentRuntime 的 `_run_hooks_background`
调用。复用 `agent_framework/llm/failure`（分类+脱敏）、`InboxRepository`（owner 通知）、
`ServiceAuditor`（审计）。
