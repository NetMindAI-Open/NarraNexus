---
code_file: src/xyz_agent_context/agent_framework/llm/transcription/backends/gateway.py
last_verified: 2026-07-28
stub: false
---

# gateway.py — 转录走 deploy 侧的 STT 代理

## 为什么存在

在这之前,免费档用户要能转录,**运营方的 NetMind STT key 必须出现在 backend、
mcp、workers 三个进程的环境变量里** —— 因为是这些进程直接去调 NetMind。

改完之后:代理挂在 LLM 网关旁边、持有运营方 key,而本 backend 只带**用户自己
的钱包 key**(就是那张免费卡上的 api_key,和聊天用的是同一把)。运营方凭据从
三个进程里彻底消失。

## 契约

一次同步调用,轮询被代理吞掉了:

```
POST {base}/v1/audio/transcriptions
Authorization: Bearer <用户的钱包 key>
{"audio_url": "...", "language": "en"}
  -> {"text": "..."}
```

## 公开 URL 这个约束没有被代理消掉

NetMind 是**去拉音频**的,代理转发的也只是一个 URL、不是字节流。所以仍然要签
短时效的公开 URL,resolver 仍然要用 `public_base_url` 把没有公网入口的部署
(Tauri / NAT 后面)挡掉。

## 为什么超时给到 165s

代理在**一个请求里**替我们轮询完整个作业,所以我们的预算必须覆盖整个作业;
直连的 netmind backend 是自己在本地轮询、每跳都短,所以它是 55s 封顶。

## 计费:没有

代理只验 key 不计费(Owner 2026-07-28 拍板)。后果说清楚:钱包花光的用户仍然
能转录,烧的是运营方的 key。边界是 STT 便宜($0.00125/推理秒)且 key 必须仍然
有效 —— 被吊销的用户什么也拿不到。
