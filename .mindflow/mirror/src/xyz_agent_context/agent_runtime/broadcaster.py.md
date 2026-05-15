---
code_file: src/xyz_agent_context/agent_runtime/broadcaster.py
last_verified: 2026-05-13
stub: false
---

# broadcaster.py — per-run in-memory pub/sub

## 为什么存在

Phase C 把 agent_loop 从 WebSocket task 拆出来跑成 BackgroundRun task
之后，agent 产生的 stream events 没法直接 `websocket.send_json` 了——
agent 跑在自己的 task，可能有 0..N 个 WebSocket 订阅它（用户开多个 tab、
重新打开页面再 reconnect 等）。Broadcaster 是这个 fan-out 通道。

## Lifecycle 严格绑定（无 TTL）

Broadcaster 跟随 BackgroundRun 寿命：

- BackgroundRun 创建 → Broadcaster 创建
- BackgroundRun 任意 terminal state → `broadcaster.close()` → 所有
  subscriber 立即收到 sentinel 退出
- 没有"完成后保留 N 分钟"的 TTL 机制——重连用户的体验由 event_stream
  表 + final_output 持久化保证（路径 2 是 by-design 用 DB 而不是 in-memory）

## 关键 Quirk: current_thinking_buffer

新订阅者接入时如果有进行中的 thinking 段（还没遇到 type 切换、还没
flush 到 event_stream），broadcaster 先发一条
`{"type":"thinking_partial_replay","content":...}` 给新订阅者，确保
mid-segment 重连的用户看到完整的 thinking 段，不会有 gap。

BackgroundRun 在 `_append_to_segment` / `_flush_segment` 里调
`set_current_thinking_buffer()` 同步快照。

## per-Subscriber 限流

每个 Subscriber 的 asyncio.Queue 有 bound（4096 events）。这是唯一的
有损路径——某个 WS 消费者卡住，针对它的事件会被 drop（log warning）。
其他订阅者不受影响。生产场景下 WS 消费速度应远快于 LLM 产出速度，
不应该触发这个 cap。

## 并发模型

所有 mutation 在同一个 event loop 上——subscribers add/remove、
publish、close 都是同一 BackgroundRun task 里调用。无 lock、无
跨 loop 访问。
