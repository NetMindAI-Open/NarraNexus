---
code_file: src/xyz_agent_context/agent_framework/llm/transcription/backends/_audio_url.py
last_verified: 2026-07-28
stub: false
---

# _audio_url.py — 把附件变成 NetMind 能拉走的 URL

## 为什么被抽出来

两条 NetMind 路径(直连 backend、经代理的 gateway backend)现在**只在拿到 URL
之后做什么上不同**。转码规则和签名那套如果各写一份,就要维护两遍。

## 两条硬约束驱动了这里的一切

1. **NetMind 的 worker 用 Python `soundfile` 解码** —— 它吃 wav/flac/ogg/aiff,
   **不吃 webm**,而 webm 正是所有 Chromium 的 MediaRecorder 产出的格式。所以
   非原生格式一律先转 mp3,缓存在原文件旁边。
2. **NetMind 是去拉音频的**。我们的附件在 JWT 后面,它的 worker 没法带凭据,
   所以要签一个短 TTL 的 HMAC URL,由公开转录路由校验(不过 auth_middleware)。
   **这条约束穿过代理依然成立** —— 代理转发的是 URL,不是字节。

## 永不抛异常

每条失败路径(扩展名不支持、没有 ffmpeg、转码失败、部署没配签名密钥、文件超限)
都返回 `None`,调用方接着试下一个候选。
