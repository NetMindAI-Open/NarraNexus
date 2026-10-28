---
code_file: backend/integrations/plugins/registry.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `build_plugin_specs(registries: Registries | None)`

参数类型从 `Any` 改为 `TYPE_CHECKING` 下的 `Registries`。

# registry.py — 可安装框架插件的派生表

## 2026-09-07（批 1 三轮复审移植）— `registries` 参数；mirror 正文重写

`build_plugin_specs(registries=None)` 与 `framework_registry(registries)` 同形：默认进程注册表，测试可传私有
`Registries(slot_tree_with_builtins())`——新增测试在私有表里注册一个带安装配方的框架后再 build，表里出现；
进程表不受影响。本 mirror 此前的正文仍在描述已删除的 `PLUGIN_SPECS` 常量与早已搬走的 pin 字面量（批 1 复审判为
Critical），正文按当前代码重写如下；pin 的设计理由随代码落在两个框架插件 `contribution.py` 的 mirror。

## 为什么存在

「装什么」这个问题唯一的答案来源。`service.PluginService`（默认参数）从它取 dict，不接受任何一方自己再列一遍
插件叫什么名字、钉了哪个版本——版本号只在各框架插件的 `FrameworkInstall` 里出现一次。

## 上下游关系

- **被谁用**：`service.PluginService._specs`（首次使用时构建一次）；`backend/routes/plugins/routes.py` 只经
  `PluginService` 间接使用，不直接 import 本文件。
- **依赖谁**：`narranexus.platform.agent_framework.loop.driver.framework_registry`（按调用时解析
  `turn.pipeline.act.framework` 位的注册表；该位由 builtin.turn 的 manifest 在 boot 时声明）、
  `narranexus.contracts.framework.FrameworkMeta`、`spec.PluginSpec`。注册表内容由 host boot 从各框架插件的
  manifest 灌入，本文件不 import 任何框架包。

## 设计决策

- 派生而不是快照：注册表可在启动后继续注册框架插件，模块级常量会漏掉后注册者。`PluginService` 在首次请求时
  构建一次（`_specs` 属性），所以对一个 service 实例而言仍是「首次使用时的快照」——见 `service.py.md`。
- 只列「声明了安装配方」的框架：`nexus_power` 内置于基础镜像，没有 `install`，自然不在表里。
- `login_marker` 从 `FrameworkMeta` 透传（B6）：登录态文件在哪由框架自己说。

## Gotcha / 边界情况

- **触发**：新增一个框架 Contribution 但 `FrameworkMeta.install=None` → **症状**：插件商店看不到它 →
  这是设计（不可按需安装的框架不进商店），不是 bug。
- 未 boot 的进程调用它会从 `framework_registry()` 得到 `UnknownEntry: unknown slot`（带「has this process booted」
  提示），而不是空表——空表会让「装了插件、商店看不到」无声化。
- pip pin 与 uv.lock 走岔的 Gotcha 见 `frameworks_claude_code/contribution.py.md`。
