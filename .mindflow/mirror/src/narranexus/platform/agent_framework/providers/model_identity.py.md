---
code_file: src/narranexus/platform/agent_framework/providers/model_identity.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review I3）— provider 覆盖规则也住这里：`config_override_wins` / `resolve_agent_config_slot`

「这个 agent 的调用实际打到哪个 provider」原本手写了三份（[[resolver]]
`_apply_agent_overrides`、[[model_health]] `report_agent_slot_suspect`、熔断器的
`_agent_bound_provider_id`），外加 [[slot_service.py]] 的 `_is_effective_override` 第四份。
现在规则只在这里：`config_override_wins(row)` = 行有非空 `provider_id`；
`resolve_agent_config_slot(db, *, agent_id, user_id, slot_name="agent")` 返回胜出的
`agent_slots` 行，否则 owner 的 `user_slots` 行（DB 错误上抛，由调用方决定失败方向）。

**两条规则刻意不同**：`slot_rebinds`（identity，要求 provider_id **且** agent_framework）
决定 prompt 自称与 driver；`config_override_wins`（provider）决定配置解析。只填了
provider_id 的 stub 在 identity 上回落到 owner 默认，在 provider 上却是胜出——把 provider
问题路由到 `slot_rebinds` 会让熔断器的收窄恢复漏掉这类 agent。锁：
`test_config_override_rule_is_provider_only_unlike_identity`、
`test_resolve_agent_config_slot_applies_the_provider_rule`、
`test_the_overlay_rule_has_no_hand_written_copies`。放在本模块是因为它只有 loguru 一个模块级
依赖，loop 层与 driver 层都能模块级导入而不成环。

## 2026-09-04 — `FRAMEWORK_DISPLAY_NAMES` 注释改为实话

它是 agent 在 system prompt 里的**自称**，不是 UI 标签；前端标签在
[[../../../../frontend/src/lib/frameworkBrand.ts]]，文案刻意不同（"Claude Code" vs SDK 名）。
二轮时我把注释改成「整体不要求一致」，三轮核对后更准的说法是：`codex_cli` / `nexus_power`
与选择器一字不差，只有 `claude_code` 刻意不同（prompt 侧自称 SDK 名）；注释按此事实写。改这里的值等于改
prompt，需单独确认。

## 2026-09-03 (评审修订) — 覆盖规则抽成纯函数，供目录投影共用

`slot_rebinds` / `effective_agent_slot` / `framework_of` + `DEFAULT_AGENT_FRAMEWORK`：
`resolve_agent_model_identity` 自己也改走它们。动机是 PR #383 评审 C1——
`/api/auth/agents` 手写了第四份解析并写错；现在 [[slot_service.py]] 的
`owner_agents_overview` 与这里同一份规则。`DEFAULT_AGENT_FRAMEWORK` 也被
[[user_service.py]] 的 owner 级读取引用——改平台默认只动一处。

# providers/model_identity.py — 解析 agent 真实的 (framework, model) 供 prompt 展示

## 为什么存在

系统 prompt 里那行 "Your LLM model: **X** (Y)" 曾经在 [[context_runtime.py]]
里被写死成 "Claude Agent SDK" / "sonnet-4"，于是**每个** agent 不管真实配置都
自称 Claude Sonnet-4，被问"你是什么模型"就照读（还违反铁律#9：身份绑死某框架）。
本模块把真身份从 runtime 实际派发用的 slot 行里解析出来，让 prompt 说真话
（如 codex_cli+gpt5 → "Codex CLI (gpt-5)"）。

放在 agent_framework 层而非 Module 里，是守铁律#9：[[basic_info_module.py]]
（一个 Module）只调 `resolve_agent_model_identity` 并渲染字符串，永远不认识
框架名。

## 设计决策

- **单一 overlay 实现**：本文件是唯一的 overlay，[[step_3_agent_loop.py]] 的
  `_resolve_agent_framework_name` **委托**到这里（`return (await resolve_...).framework`），
  所以 prompt 展示的身份与 dispatch 真正跑的 driver **不可能不一致**。
  夺权规则：per-agent `agent_slots` 覆盖行只有在**同时带 `provider_id` 和
  `agent_framework`** 时才胜出（provider-only 或 framework-only 的残行都不夺权，
  与 config resolver 一致），否则回退 owner（`agents.created_by`）的 `user_slots`。
  framework 和 model **都从同一个胜出的 slot 行读**（两表都有这两列）。
  - **踩过的坑（PR #84 review）**：`agent_slots.agent_framework` 是 `nullable=True`。
    初版判定只看 `provider_id`，漏了 `agent_framework` 非空这一条——于是"有 provider
    但 framework 为 NULL"的行会被本 resolver 当胜出、渲染成 Claude，而 dispatch 端
    落到 owner 框架真跑 Codex，重新制造错误身份。收敛成单一实现后此类不一致从根上消除
    （方向也纠正了：`agent_runtime → agent_framework` 本就是合法 import 方向，step_3
    早已 `from xyz_agent_context.agent_framework import ...`）。
- **绝不抛异常**：任何缺行/空列/DB 故障都降级到 `(nexus_power, "")`，因为它喂的是
  system-prompt 构建路径，炸了会废掉整轮。降级值仍走同一 display 映射，宁可回退成
  一个"次真实"的默认，也不输出错误品牌。
- **未知 framework 名原样展示**（不塞进 `FRAMEWORK_DISPLAY_NAMES` 的名字直接回显），
  绝不替用户的私有框架名瞎编品牌。

## 上下游

- **被谁用**：[[basic_info_module.py]] `hook_data_gathering` 调它，填
  `ctx_data.agent_info_model_type`（framework 展示名）+ `ctx_data.model_name`
  （真实 model），再由 basic_info 的 [[prompts.py]] 模板 `{...}` 渲染进系统 prompt。
- **依赖谁**：只用 `db.get_one` 读 `agent_slots` / `agents` / `user_slots`。

## 契约测试

`tests/agent_framework/test_agent_model_identity.py`：覆盖胜出 / 无覆盖回退
user_slots / 缺 provider_id 不夺权 / **有 provider 但 framework NULL 不夺权**（PR #84
回归）/ 缺行→nexus_power+空 model / DB 故障兜底 / 未知名原样。
`test_resolve_agent_framework_per_agent.py` 走委托后的 `_resolve_agent_framework_name`，
同样锁 dispatch 端行为——两个测试测的是同一份 overlay 的两个出口。

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.

## 2026-09-07 — 自述名来自 FrameworkMeta（B6）

FRAMEWORK_DISPLAY_NAMES 删除；_display_for 读 framework_meta(name).self_description（runtime_name 或 display_name，由框架插件持有，是 prompt 字符串），未知名回落原名。

## 2026-09-07（round-2 P2-I1）— the default framework comes from the ONE accessor, and still never raises

`DEFAULT_AGENT_FRAMEWORK` (a second copy of `driver.DEFAULT_AGENT_LOOP_FRAMEWORK`) is deleted;
`framework_of` calls `driver.resolve_framework_name`, so the prompt's identity, the slot writer's
validation and the driver that actually runs cannot disagree under a distribution that binds a
different default. The module's promise is unchanged and now explicitly guarded: `resolve_framework_name`
can raise `FrameworkNotInstalledError` on a misbinding, so `framework_of` and `_display_for` catch
that alongside `UnknownEntry` and degrade to the raw column value / the code default — identity
resolution must never break the system-prompt build, while the TURN path still refuses that binding
loudly.
