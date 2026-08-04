---
code_file: backend/routes/manyfold/files.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — write 端点:流式限量 + overwrite 默认 False(review)

64MB 上限改为**边流边检**(request.body() 全量缓冲后再查 = 5GB 恶意/
bug 上传先打爆内存);唯一写口的 overwrite 默认翻 False——覆盖必须
显式授权(附件 ingest 写唯一目录,传 true 只为重试幂等)。
## 2026-08-03 — 新增唯一写端点 `POST …/files/write`(附件通道我方半)

原设计"write 面刻意不暴露"被 managed-attachment 设计(spec 2026-08-03
§Q7 甲方案)推翻一半:平台 chat-attachment ingest 需要把入站文件落进
agent workspace,而平台的 `narraNexusCtx` 只读正是因为我方没有写端点。
现在暴露**恰好一个** write(网关 auth + `_safe_resolve` 防穿越 + 64MB
防御上限 + overwrite 语义 + 自动建父目录);mkdir/mv/rm 仍不暴露——
workspace 的其余变更依旧只属于 agent 自己。平台侧配套:接线
`narraNexusCtx.write` + root 翻 writable + 能力表翻 true(见 spec §8)。
本 mirror 为首次回填(此前缺失)。

# files.py — Manyfold per-agent 文件树 API

## 为什么存在

Manyfold chat 头部的 "Show file tree" 需要 list/stat/read 一组端点;
其他 framework 用 DUFS WebDAV sidecar,NarraNexus 不想在镜像里多背一个
二进制,直接用 FastAPI 实现读路径。`_resolve_workspace_root` 按
`agents.created_by` 解析 owner 从而得到 workspace 目录;`_safe_resolve`
是全部端点共用的路径安全闸(resolve 后 strict prefix check,防符号链
接逃逸)。仅 `ENABLE_MANYFOLD_API=1` 注册,同网关 token 中间件。

## Gotcha

- list 对不存在的 workspace 返回空 entries(200 而非 404)——首次打开
  文件树时 agent 可能还没写过任何文件。
- read 有 64MB 上限与 64KB 分块流式 + `X-Accel-Buffering: no`。
- `_require_manyfold_auth` 是有意的兄弟文件复制,避免同层互相依赖。
