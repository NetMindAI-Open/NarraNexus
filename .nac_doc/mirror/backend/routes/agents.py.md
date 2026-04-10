---
code_file: backend/routes/agents.py
last_verified: 2026-04-10
stub: false
---

# agents.py — Agent 路由聚合器

## 为什么存在

`agents.py` 是一个纯聚合文件，没有任何路由定义，只做一件事：把 7 个 `agents_*` 子路由模块合并成一个 `router`，让 `main.py` 只需要 `include_router(agents_router, prefix="/api/agents")` 一次就能注册所有 agent 相关路由。

存在的原因是 agent 资源的子域太多（awareness、chat history、files、MCPs、RAG、social network、cost），全放在一个文件里会超过 2000 行，可维护性极差。这个文件是在重构过程中从原来的单体 1850 行文件中拆出来的聚合点。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(agents_router, prefix="/api/agents")`
- **依赖谁**：`agents_awareness.py`、`agents_social_network.py`、`agents_chat_history.py`、`agents_files.py`、`agents_mcps.py`、`agents_rag.py`、`agents_cost.py` 的 router 实例

## 设计决策

被否决的方案是在 `main.py` 里逐一 `include_router` 所有 7 个子路由并分别加 `prefix="/api/agents"`。这样可以工作，但 `main.py` 的导入和注册部分会变得很长，而且如果需要在所有 agents 路由上加统一的 tag 或 dependency，要在 7 个地方修改。聚合器模式把这些关注点集中在一处。

## Gotcha / 边界情况

子路由注册顺序对 `agents_social_network.py` 有影响，因为该文件内部有 `/{agent_id}/social-network/search` 和 `/{agent_id}/social-network/{user_id}` 的路径冲突问题，但这是由子文件内部的路由定义顺序解决的，与本文件的聚合顺序无关。

## 新人易踩的坑

新增 agent 资源子域时，需要同时做两件事：创建新的 `agents_xxx.py` 文件，并在本文件里 import 并 `router.include_router()`。只做一步会导致路由要么缺失要么孤立。
