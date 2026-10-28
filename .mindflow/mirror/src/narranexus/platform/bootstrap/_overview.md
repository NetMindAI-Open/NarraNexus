---
code_dir: src/narranexus/platform/bootstrap/
last_verified: 2026-08-20
stub: false
---

# bootstrap/ — Agent 首次启动引导（provisioning + profile 体系）

## 目录角色

`bootstrap/` 早已不止 `template.py`：它是"一个全新 Agent 如何变得可用"的
domain 包——`provision.py` 是唯一的新建 Agent 供给 seam（三个创建入口都汇
聚于此），`profiles.py` 把首跑体验做成可插拔 profile（default / none /
arena / onboarding），`welcome_templates.py` 提供双语欢迎 artifact 的
HTML 骨架，`template.py` 保留 default profile 的问候语与引导文档常量。

它解决的问题不变：新建的 Agent 没有名字、没有 Awareness、没有 Narrative，
需要一段"首次醒来"的剧本引导第一次对话；profile 体系让不同场景（Arena
选手、onboarding 引导 Agent）各自拥有整套首跑体验。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `provision.py` | 新建 Agent 的唯一供给序列 seam（行、instances、发现、bootstrap、默认 skill、awareness；2026-08-19 起支持 `bootstrap_ctx_extra` 透传 profile 渲染参数） |
| `profiles.py` | BootstrapProfile 基类 + 注册表 + `apply_bootstrap`（render-then-store） |
| `welcome_templates.py` | 双语欢迎 artifact HTML 骨架与 default 文案 |
| `template.py` | default profile 的问候语 + 引导文档常量 |
| `greeting_seed.py` | 「该不该 seed bootstrap 问候语」的判定（返回 greeting 文本或 None）；写入交 [[../module/chat_module/_chat_writes]] 单写入方，由 `step_1` 对 head narrative 实例调用（2026-08-20） |
| `lifecycle.py` | `is_bootstrap_active` —— 「agent 还在不在引导期」的唯一判定源（Bootstrap.md 存在 + 未过阈值），`greeting_seed` 与 `context_runtime` 共用，防两个写入方 drift（2026-08-20） |

场景 profile 的注册方在各自的消费侧（铁律 #21）：arena profile 在
`backend/integrations/arena/`，onboarding profile 在 `backend/onboarding/`。

## 和外部目录的协作

**被谁触发**：Agent 创建时，`backend/routes/` 的 Agent 创建接口把 `BOOTSTRAP_GREETING` 持久化到 DB 作为第一条 assistant 消息，把 `BOOTSTRAP_MD_TEMPLATE` 写入 Agent 的工作区文件系统（路径类似 `~/.nexusagent/agents/{agent_id}/bootstrap.md`）。

**被谁读取**：`context_runtime/` 在构建 Agent 的执行上下文时读取工作区文件，发现 `bootstrap.md` 存在时把其内容注入 LLM 指令，引导 Agent 进入"首次设置"对话模式。

**结束条件**：`BOOTSTRAP_MD_TEMPLATE` 最后一句写着 "Delete this file. You don't need a bootstrap script anymore."——Agent 在完成名字和 Awareness 设置后应该自行调用文件删除工具删掉这个引导文档。这是 Agent 主动结束 bootstrap 阶段的信号。
