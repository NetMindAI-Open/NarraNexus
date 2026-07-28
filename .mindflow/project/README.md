---
doc_type: project_index
last_verified: 2026-04-09
---

# `.mindflow/project/` — Tier-3 深度文档入口

本目录承载 MindFlow 三级文档体系的 **Tier-3** 内容：项目级的权威参考 + 任务 SOP。

## 两个子目录

| 子目录 | 角色 | 生命周期 |
|--------|------|---------|
| [`references/`](./references/) | 子系统深度文档（跨文件主题） | 稳定，长期维护 |
| [`playbooks/`](./playbooks/) | 任务 SOP（带「何时读」触发器） | 跟随流程演进 |

## 入口导航

完整的 references + playbooks 索引及其**何时读**触发条件，统一维护在仓库根的 `CLAUDE.md` 的 **`## 深度文档索引`** 章节 —— agent 和人都从那里查触发器，不要在这里重复维护一份。

## 当前状态（Phase 1）

本目录在基础设施建设 commit 时**尚无内容文件**。Phase 2 的工作是按照 `CLAUDE.md` 深度文档索引列出的清单，手写如下首批文件：

- `references/architecture.md`
- `references/agent_runtime_pipeline.md`
- `references/module_system.md`
- `references/coding_standards.md`
- `playbooks/onboarding.md`
- `playbooks/add_new_module.md`
- `playbooks/write_mindflow.md`

未写就的情况下，agent 会按 `CLAUDE.md` 深度文档索引的 fallback 规则回退到代码 + mirror md。

## 参考

- 方法论全本：[`../README.md`](../README.md)
- NexusAgent 顶层入口：[`../_overview.md`](../_overview.md)
