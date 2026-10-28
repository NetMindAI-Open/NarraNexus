---
code_file: src/narranexus/platform/agent_profile/
last_verified: 2026-08-20
stub: false
---

# agent_profile/ — 改名这件事的唯一写入方

## 为什么是一个领域包，而不是留在 AwarenessModule 里

这套改名事务最早写在 [[_awareness_writes]] 里，因为身份更正笔记住在 Awareness
的 `instance_awareness` 表。但事务本身写的是 `agents` 行、刷的是
`bus_agent_registry`、还接 `is_public` / `created_by`——**没有一样是 Awareness 的
职责**，Awareness 只是它其中一步。

真正的代价在铁律 #3：`PUT /api/auth/agents/{id}` 和 manyfold 的 provisioning
是核心平台路由，它们 import 了一个**可热插拔的 Module**。把 AwarenessModule 从
`module_registry` 摘掉，不是"少一个功能"，是 route 模块 import 时就 ImportError ——
**后端起不来**。而且当时 mirror 里那句「别再新增写入方，要写就调这个函数」等于
用文档把这个反向依赖固化了。第三轮独立审查指出来的。

现在按仓里既有惯例（`artifact/` `memory/` `message_bus/` `marketplace/`）建成领域包：

```
agent_profile/
├── agent_profile_service.py     公开 seam（协议层）
└── _agent_profile_impl/
    └── profile_write.py         事务实现（私有，不外露）
```

## Awareness 那一步怎么处理

**延迟导入,在函数体内**(`_awareness_identity_writers` / `_record_identity` /
`_reconcile_identity`)。买到的是**归属**:本包与其上的路由都没有对 Module 层的
模块作用域依赖——那才是「摘掉 AwarenessModule 后端起不来」的成因。**不是隔离**:
Python 必然先导入父包,实测连带拉进 22 个兄弟模块。

**没有容错分支**(十五改)。早先版本带一个 `except ImportError` 降级,但同一次改动
新增的另外三处调用点(两个 awareness 写 seam、bundle importer)都是裸 import——
一个只在四分之一调用点成立的契约,读者要么以为它成立、要么以为它没意义。真实的
降级是「agent 没有 AwarenessModule 实例」,两个写入器对此本来就返回 `None`。
缺**包**的部署会在调用时直接抛——四处一致地抛,而不是一处 warning 三处抛。

## 边界

- 全平台**只有本包**写 `agents.agent_name`。护栏与允许清单在
  `tests/schema/test_only_one_writer_of_agent_name.py` —— **可执行的那一份**。
  mirror 只指过去,不再自带一份会漂的拷贝(那份测试的 docstring 自己也这么主张)。
- 建 agent **不走**这里：本函数的前置是**行已存在**（读不到即 `not_found`）。
  新建 agent 也没有"旧名"可更正。
