---
code_file: scripts/manyfold_trigger_experiment/fake_manyfold.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — 上传显式 overwrite=true(review 连带)

write 端点默认翻 False 后,桥对同 event-id 路径的重转发上传显式授权
覆盖,保持重试幂等。
# fake_manyfold.py — 本地扮演 Manyfold 的"钟"与"耳朵"

## 为什么存在

托管模式的端到端验证需要平台侧配合,但本地没有 Manyfold 部署。本脚本
按 NarraNexus 公开的 HTTP 契约(inventory pull / run_job 控制消息 /
model-B channel_context / notify webhook / files write)扮演平台,验证
抽象事件流——不复刻 Firecracker/挂起唤醒("本地无法 1:1 模拟,抽象
逻辑一致即可")。源自 feat/manyfold-cloud 的实验 harness(PR #118 期,
rujing.yan),2026-08-03 移植到 feat/manyfold-im-ingress 并扩展。

## 2026-08-03 — 扩展:活的替代连接 + v1 契约 + 多模态

- `listen-matrix`:经 `GET /manyfold/channels` 拉 matrix 凭据(与平台同
  路),sync 长轮询;回声过滤(自身 mxid)、dm/group(joined_members
  计数)、mention(m.mentions + mxid/localpart 子串)、媒体下载 →
  `POST files/write` 落 workspace(= 平台 ingestWorkspace)→ 按 v1
  channel_context(chat_type/is_mention/attachments)转发,流式打印
  transcript(reasoning 暗色/工具/content 高亮)。非 @ 群消息默认带
  `is_mention=false` 转发(验证 Q8 静默摄取);`--drop-silent` 对照。
- `listen-wechat`:iLink getupdates 游标长轮询——**复用仓内
  WeChatSDKClient**(header/游标/errcode 语义单一居所,需 `uv run`);
  `reply_token=context_token` 透传。
- 前置硬条件写进 docstring:Nexus 必须 `NEXUS_EXTERNAL_TRIGGERS=1`,
  否则本桥与原生 trigger 双接同一 bot,全部结论作废。

## Gotcha

- 桥刻意**不做**去重/重试梯队/严格 mention 检测(平台职责的简化替身);
  测试判定时勿把桥的简化当端点 bug(计划文档 §4 有同款提醒)。
- matrix 首次 sync 只取 next_batch 基线,跳过历史 backlog。
- 配套测试计划:
  `reference/self_notebook/plans/2026-08-03-manyfold-im-ingress-local-e2e.md`。
