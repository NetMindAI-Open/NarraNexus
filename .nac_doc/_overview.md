---
doc_type: top_overview
last_verified: 2026-04-09
---

# NexusAgent · `.nac_doc/` 顶层入口

欢迎。这是 NexusAgent 项目的三级文档系统入口。如果你是第一次来，按下面的顺序读：

## 新人推荐阅读路径

1. **先读方法论**：[`.nac_doc/README.md`](./README.md) —— 理解三级文档体系的规则（英文）
2. **再读 CLAUDE.md**：`/CLAUDE.md` —— 项目的铁律、启动工作流、深度文档索引
3. **按需读 Playbook**：`.nac_doc/project/playbooks/onboarding.md` —— Day-1 新人手把手流程
4. **深入架构**：`.nac_doc/project/references/architecture.md` —— 完整架构分层
5. **碰到具体代码再读 mirror**：`.nac_doc/mirror/<对应路径>.md` —— 单文件 intent

## 三级文档速查

| 层 | 位置 | 问题 | 读法 |
|----|------|------|------|
| Tier-1 | 代码内 docstring / 注释 | 这行/这个函数**做什么** | Read 代码时自然读到 |
| Tier-2 | `.nac_doc/mirror/` | 这个文件**为什么存在**、**和谁协作**、**踩过什么坑** | 编辑代码前先读 |
| Tier-3 · references | `.nac_doc/project/references/` | 这个子系统**整体怎么运作**、**不变量是什么** | 跨层重构前读 |
| Tier-3 · playbooks | `.nac_doc/project/playbooks/` | 我要做**这件事**，步骤是什么 | 接到任务时按触发器匹配 |

## 项目特有说明

- **语言**：Tier-2 mirror md 和 Tier-3 playbooks / references 用**中文**；`.nac_doc/README.md` 用英文（为 Skill 复用做准备）
- **代码**：所有代码（含注释）必须用英文（CLAUDE.md 铁律 #1）
- **一次性文档**不在这里：设计 spec / 实施 plan / 临时 todo 在 `reference/self_notebook/`

## 相关

- 方法论全本：[`./README.md`](./README.md)
- 系统设计 spec：`/reference/self_notebook/specs/2026-04-09-nac-doc-system-design.md`
