---
code_file: frontend/src/lib/browserLook.ts
last_verified: 2026-09-24
stub: false
---

# browser_look 输出解析

同一个工具结果落库时有三种形状，因为三个执行器扁平化 MCP 结果的方式不同：NexusPower 原样保留
元数据 JSON；Claude Code 把元数据 JSON 和图片描述 JSON 首尾相接（无分隔符）；Codex 序列化整个 MCP
结果，元数据在 `content` 第一个 text 部件里。所以用「带字符串/转义感知的括号深度扫描」取开头第一个完整
对象，而不是整串 JSON.parse。图片字节在任何形状里都不存在（后端已换成描述）。认不出就返回 null，
让通用行接手——`acceptsBrowserLookOutput` 就是注册表的 accepts。整个视口时不给 region，避免冗余标签。
