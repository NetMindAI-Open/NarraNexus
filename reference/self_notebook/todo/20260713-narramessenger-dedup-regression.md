# NarraMessenger 内容去重防护疑似在 Matrix 迁移中丢失

发现于 2026-07-13（claude-agent-sdk 升级回归时，与升级无关的既有问题）。

## 现象

1. `tests/channel/test_content_fingerprint_dedup.py:21` 仍在 import
   `narramessenger_module.narramessenger_trigger.NarramessengerTrigger`，
   该文件已不存在（被 `matrix_trigger.py` / `MatrixTrigger` 取代），
   **dev 上 pytest 全量收集直接失败**。dev 分支只跑 Deploy workflow，
   测试 CI 只在 PR 上跑，所以坏 import 溜进了 dev。
2. 更重要的功能疑点：`MatrixTrigger` **没有定义**
   `CONTENT_DEDUP_WINDOW_SECONDS`（基类 `channel_trigger_base.py:193`
   默认 0 = 不启用内容指纹去重）。而 PR #51（fc3a5981，X1 双回复修复）
   给旧 NarramessengerTrigger 设的窗口是 ≥16 分钟，专门覆盖平台 15 分钟
   invocation deadline 的重放。该测试
   `test_narramessenger_window_covers_platform_deadline` 守的就是这个。

## 结论（2026-07-13 已处理，见 fix/test-health-20260713）

- [x] 确认：X1 的"平台换 invocation_id 重发"失效模式随旧网关 trigger 一起退役
      （channel_trigger_map.py 注释明确 NarramessengerTrigger was retired；
      Matrix 直连的 event_id 跨 /sync 重放稳定，id 键去重层已覆盖）→
      **不需要**给 MatrixTrigger 补窗口，补了反而会吞用户合法重发。
- [x] 测试已改写：断言 MatrixTrigger.CONTENT_DEDUP_WINDOW_SECONDS == 0 是
      有意为之 + 保留 ChannelDedupStore 全部机制测试（未来网关型 trigger
      重新 opt-in 时机制现成）。
- [ ] 遗留：dev 分支只跑 Deploy workflow 不跑测试，release 同步类直推能把坏
      import 带进 dev——是否给 dev push 加最小 pytest --collect-only 门禁，
      涉及部署契约（deploy 仓铁律 #3），单独找 Bin 决策。
