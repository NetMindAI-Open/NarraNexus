---
code_file: src/xyz_agent_context/agent_runtime/executor_reaper.py
stub: false
last_verified: 2026-07-28
---

## 2026-07-28 — post_reap 钩子随 per-run 会话票一起删除

`post_reap_fn` 这个机制当初只有一个用途：culling 掉闲置 executor 后，顺手
吊销该用户遗留的 free-tier 会话票。per-run 会话票整套已经不存在（改成每用户
一把长期钱包 key，落在 `user_providers` 里），钩子随之失去唯一调用方，按
铁律 #2/#8 一并删掉而不是留着空转。

## 为什么存在

per-user Executor 容器的 idle-cull 协调者。云端每个活跃用户有一个 executor
容器(~1.5G),长期不回收会把内存占满。reaper 周期性地把空闲超过 TTL 的用户
executor 停掉。

## 设计(优雅:单一职责 + 依赖注入)

三个关注点分离,reaper 是纯协调者,不持有任何一方的内部:
- `AgentAdmissionController`([[admission.py]]) — 并发 + 空闲记账(WHO is idle)。
- `ExecutorReaper`(本文件) — **WHEN** to cull(周期 + TTL)。
- `broker_client.stop_executor` — **HOW**(docker 传输,DELETE /executors/{user})。

reaper 通过构造注入 `controller` + `stop_fn`,可用 fake 完整单测,无需真 broker/
真 sleep(`reap_once()` 是可测的单趟)。

## 坑 / 决策

- **铁律 #14**:只回收空闲(0 活跃 loop)的 executor,绝不碰运行中的 loop。
  `claim_idle_users` 在锁内原子地"认领并移除",避免重复回收。
- **竞态**:认领后、停止前若有新 run 到达并复用了那个容器 → 极小窗口内 run 可能
  连到被停容器;`broker.ensure` 幂等会冷启动一个新的,最坏只是一次冷启动(唤醒
  UX 覆盖)。20 分钟 TTL 下碰撞概率极低。
- **stop 失败**:记录并跳过该用户,不中断整趟;broker 自带的 label-based reaper
  兜底清孤儿。
- **fire-and-forget**:`maybe_start_executor_reaper` 起的后台 task 挂了 done-callback
  上报异常(事故教训 #2:裸 create_task 是地雷)。
- **门控**:`maybe_start_executor_reaper` 仅在配置了 `BROKER_URL`(云端)时启动;
  本地/桌面无 per-user executor,返回 None。在 `backend/main.py` lifespan 启动/取消。
- TTL/间隔:`EXECUTOR_IDLE_TTL_SEC`(默认 1200=20min)、`EXECUTOR_REAP_INTERVAL_SEC`
  (默认 120)。
- **免费额度网关票孤儿回收(2026-07-23)**:新增可选 `post_reap_fn(user_id)` 钩子,
  在成功停掉某用户 executor **之后**触发。用途:回收该用户遗留的 gateway 会话票
  (agent 硬崩溃、`agent_loop` 的 finally 没跑到 → 票没作废)。**为什么此刻安全**:
  reaper 只回收 `claim_idle_users` 认领的空闲用户(0 活跃 loop),所以此刻该用户没有
  在跑的 run,任何 ACTIVE 票必是孤儿 → 直接作废不违反铁律 #14(不需要定时器、不需要
  猜哪个 run 还活着)。stop **失败**时**不**触发钩子(容器可能还活着,票不能动)。
  `maybe_start_executor_reaper` 仅在配了 `SYSTEM_DEFAULT_LLM_GATEWAY_URL` 时装配该
  钩子,钩子内 `GatewayKeyService.from_env(db).revoke_all_for_user`。见
  [[gateway_key_service]]。
