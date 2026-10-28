---
code_file: templates/README.md
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — 贡献 id 在槽内全局唯一，模板一律以插件名命名

六个模板（tool/routes/worker/bundle/settings/table）此前发通用 id（tools/api/sync/team/schema/items），第二个同类插件必撞 `RegistryConflict` 被隔离——Agent 写的第一个工具插件就撞了 hello_world 示例。现在全部 `__PLUGIN_PKG__` 前缀，README 写明规则，`test_every_template_contribution_id_is_plugin_scoped` + 双插件共存 boot 测试守着；hello_world 示例同改。

## 2026-09-03（批 2e）— 模板目录说明

十二个 kind 各一目录：manifest 片段 + backend 片段 + 自带测试（跑在 `PluginTestHost` 上）+ 需要时的前端源码
与 dist 占位。`narranexus plugin new` 组合它们。模板里的 `.py` 是脚手架产物的原料，不是应用代码。

## 2026-09-07 — 四个新模板 + 命名约定（B10）

stage_strategy（Recall 策略，形状适用于任一阶段槽）/pipeline_profile/context_provider/channel（描述符+webhook 触发器+发送工具模块，取自 tests/plugins/hello_channel 的最小形态）。模板只从 narranexus.sdk 取契约名（新增再导出）。HOOKIMPLS 统一改名 HOOKS；贡献符号命名约定写进 docs/API_POLICY.md §8。
