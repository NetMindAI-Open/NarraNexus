---
code_file: src/narranexus/platform/channel/__init__.py
last_verified: 2026-08-27
stub: false
---

## 2026-08-27 — 导出 `IngressGuard` / `content_fingerprint`

ingress 分级熔断器进公共导出面，与 `ChannelDedupStore`、
`ChannelDebounceMerger` 平级——它的 docstring 就是这么定位自己的，此前却只有
它需要深路径 import。接线 PR 的四个挂载点会用这个导出面。

本次合入时零调用点（内核先合、接线后合，见 [[ingress_guard]]）。

# channel/__init__.py — channel 包的公开出口

## 为什么存在

`channel/` 下的东西分两类：给外部（trigger 实现、module、backend 路由）用的，和只在包内
互相调用的。这个文件划这条线 —— `__all__` 里的名字是契约，其余是实现细节。

它此前没有镜像，而 [[message_bus/__init__.py]] 有 —— 属于约定不一致而非有意省略。补上的
直接触发是 2026-08-18 出口表本身变了。

## 2026-08-18 — `ChannelInboxWriter` → `InboxRecorder`

出口从 `ChannelInboxWriter` 换成 `InboxRecorder`（见 [[inbox_recorder.py]]）。这不只是改名：
旧写入器往 **bus 的表**里写五行一束（伪 agent、频道、成员、两条消息），新记录层有自己的两张
表。这个出口是唯一让外部拿到写入器的地方，所以换名字就等于换契约 —— 任何还 import 旧名字的
调用方会在 import 时报错，而不是在运行时静默走上一条已经没人写的路径。这是有意的：铁律 #2
不留兼容 shim。

## Gotcha

- `__all__` 里的名字**不是**装饰：`from narranexus.platform.channel import X` 这种写法在仓里
  到处都有，从 `__all__` 删一个名字是破坏性改动。加名字之前先想清楚它是不是真的该被包外看见。
