---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_voice_delivery.py
stub: false
last_verified: 2026-08-13
---

## 2026-08-13 — 连续同文段去重（exact-equality only）

对抗实测：模型同轮两次 speak 说同一句话（不同 provider call_id），房间/TTS 听两遍。
两道闸：_close_segment 丢弃与上一完成段完全相等的新段（等值=零新内容，丢弃不可能
丢信息，「宁重勿丢」原则不破）；_cumulative 在当前开放段的净化文本仍是上一段前缀时
把它排除出 live 视图（否则 flush 会把重复增量流给 TTS）——一旦分叉整段进入（刻意
重复仍可交付）。非等值的换述重复不拦（相似度阈值会吞意图，见 PR#236 教训）。

## 2026-08-07 — dev 全真探针抓到的重复播报 bug

真实事件流里 PROGRESS **不带 call_id**（空串），而 delta 带 provider 真 id——纯 id 判据把 delta 段错误关袋、权威全文再入一段，房间里每句话播两遍（dev probe gw4/gw5 实锤）。修法（经 PR-251 review 再校正）：①id 双方都有时 id 是权威（同 id 必同段、异 id 必新段）——但**今天 id 分支纯防御**：run_collector 不透传 tool_call_id，PROGRESS 恒空串，no-delta 多段实际只靠前缀判据在扛；②否则**raw 正向前缀**判据——权威文本按构造是 delta 累积的超集，**绝不能比 sanitize 后的文本**（成对删标记不保前缀，delta 切在 markdown/URL/行内代码中间就误判成新段，重复播报原样回归——review 给了实测表）；③**无反向分支**——较短的不相交文本是新段，用短文本替换已完成的长段=静默丢内容，比重复更糟（铁律 #16）。边缘取舍：匿名两 call 说前缀相同的话，后者并入前者段——同文冗余，接受。

## 2026-08-06 — auto review 收口（PR #247 两轮意见）

review 收口：on_segment_text 换 call_id 先关段（无 delta 路径多段不再丢）；close() 即使 mid-stream broken 也重试一次无标记终态 edit——成功即同时完成交付与 live 收口，失败才交还平文兜底。

## 2026-08-06 — voice fast mode: 观测（voice-timing + profile 标记）

桥暴露观测戳：first_delta_at（≈first_model_token）/ first_sent_at（first_matrix_live_reply_sent）/ finalized_at（matrix_live_reply_finalized），供 [voice-timing] 行发射。

## Why it exists

F28 语音交付桥：一个 voice turn 的出站生命周期唯一所有者。handoff §6 契约
——首段可播文字立即发 base live m.text（org.matrix.msc4357.live），后续
m.replace 携**累积**净化全文（body 带 "* " fallback 前缀 + m.new_content，
live 标记两处都在），final edit 两处去标记。Hybrid LiveKit worker 观察这串
事件喂 TTS。

## Design decisions

- **纯逻辑注入**：sender（async content→event_id）与 clock 都是构造参数，
  全生命周期无 homeserver 可测；prod 侧 sender 是 matrix_trigger 里包
  credential 的闭包，直接走 matrix_room_send（_matrix_send 零改动——edit
  就是带 m.relates_to 的普通 send）。
- **净化两道闸的主闸**：sanitize_for_tts 在增量出口执行（markdown/code/
  URL/emoji 结构层保证，跨 delta 的 `**` 撕裂也漏不出去——测试钉住）；
  speak executor 的终检是兜底。
- **节奏**：句边界或 300–500ms 间隔先到者 flush；节奏钟从 bridge 创建起算
  （中间碎片绝不即时出门）。
- **失败永不炸事件循环**：send 异常→bridge 置 broken 继续攒文本；close()
  返回 (text, finalized_ok)，not ok 时 trigger 走 _send_matrix_reply 平文
  兜底（handoff 6.3：不许把答案留在永久 live 态里不交付）。
- 多次 speak 调用按段拼接；PROGRESS 的完整参数文本是权威修正
  （on_segment_text，防 arg delta 不可用的 provider）。

## Downstream

matrix_trigger：_StreamReplyState.voice_bridge 持有；_handle_stream_event
喂**认领工具**（speak 或 narra_reply，2026-08-13 认领制）的
AGENT_REPLY_DELTA / PROGRESS；finalize 处 close + 兜底。桥自身零改动——
段语义/前缀判据对任何认领工具同样成立。
测试 tests/narramessenger_module/test_voice_delivery_bridge.py + test_voice_stream_wiring.py。
