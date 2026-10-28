---
code_file: src/narranexus/platform/artifact/_artifact_impl/notify.py
last_verified: 2026-08-20
stub: false
---

# notify.py — artifact_changed 事件的staging 收敛点

## 为什么存在

前端发现 artifact 的旧机制是「在聊天流里 grep 工具名」——七类病灶(静默吞/后台盲区/
改名即断,见 research/2026-08-10-artifacts-architecture.md §9.9)的共同根因。修法是
让注册表自己开口:**每一条注册表写路径**(register / target 重注册 / 去重原地更新 /
删除 / heal 重指)在 DB 写成功后调 `stage_artifact_event`,把自包含 payload 落进
`instance_artifact_events` outbox。

## 为什么是 outbox 而不是直接广播

register_artifact 跑在 MCP 工具进程,run 的 Broadcaster 在 backend 进程——跨进程。
outbox 行由 BackgroundRun 在每个 tool-output 事件后 drain 并经 `self.emit()` 重发
(录制 event_stream + 广播一次拿齐)。上下游:写入方=registration/artifact_service
(bulk_delete)/heal;消费方=background_run 的 drain。

## 两条契约(动这文件前必读)

- **best-effort**:staging 失败只 warning,绝不让所伴随的写失败——前端打开时全量拉
  是自愈地板,丢事件=迟到,不是损坏。
- **payload 永不带 file_path**(服务端私有);带全量其余元数据,前端零二次请求
  (getDetail 从关键路径退役)。

## 设计出处

spec 2026-08-18-artifact-events-inventory-pointer §3;action 语义见 ACTIONS 注释。

## 2026-08-18(二)— payload 改为全行(含 file_path)

原「不带 file_path」在 PR-3 落地时被推翻:HTTP 列表路由本就把 file_path 返回
给已认证属主(无秘密可藏),而排除它让 store 出现两种 Artifact 形状
(HtmlRenderer 按 file_path 分支渲染模式)——一致性 > 无意义的排除。

## 2026-08-20 — 插入经 ArtifactEventRepository(#334 I9);投递语义
「至少一次」正本在该 repo 的 mirror。
