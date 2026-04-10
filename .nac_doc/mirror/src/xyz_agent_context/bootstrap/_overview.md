---
code_dir: src/xyz_agent_context/bootstrap/
last_verified: 2026-04-10
stub: false
---

# bootstrap/ — Agent 首次启动引导模板

## 目录角色

`bootstrap/` 是一个极简的初始化辅助包，目前只有一个文件 `template.py`，提供两个字符串常量：`BOOTSTRAP_GREETING`（第一条显示给用户的问候语）和 `BOOTSTRAP_MD_TEMPLATE`（写入 Agent 工作区的引导文档内容）。

它的存在解决了一个具体问题：新建的 Agent 没有名字、没有 Awareness、没有任何 Narrative，如果直接进入普通对话模式，LLM 不知道该如何开展第一次对话。Bootstrap 模板提供了一段"首次醒来"的剧本，引导 Agent 通过对话建立身份（名字、称呼用户的方式）。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `template.py` | 两个字符串常量：前端即时展示的问候语 + ContextRuntime 读取的引导文档 |

## 和外部目录的协作

**被谁触发**：Agent 创建时，`backend/routes/` 的 Agent 创建接口把 `BOOTSTRAP_GREETING` 持久化到 DB 作为第一条 assistant 消息，把 `BOOTSTRAP_MD_TEMPLATE` 写入 Agent 的工作区文件系统（路径类似 `~/.nexusagent/agents/{agent_id}/bootstrap.md`）。

**被谁读取**：`context_runtime/` 在构建 Agent 的执行上下文时读取工作区文件，发现 `bootstrap.md` 存在时把其内容注入 LLM 指令，引导 Agent 进入"首次设置"对话模式。

**结束条件**：`BOOTSTRAP_MD_TEMPLATE` 最后一句写着 "Delete this file. You don't need a bootstrap script anymore."——Agent 在完成名字和 Awareness 设置后应该自行调用文件删除工具删掉这个引导文档。这是 Agent 主动结束 bootstrap 阶段的信号。
