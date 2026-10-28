---
code_file: frontend/src/lib/scrollAnchor.ts
last_verified: 2026-08-19
stub: false
---

# scrollAnchor — prepend 场景的滚动保位

`capturePrependAnchor` / `restorePrependAnchor`:锚住 prepend 前最顶部的
已渲染元素,恢复时按它的实际像素位移修正 scrollTop。高度差值法只作
「此前一无所有」的回退——它会把 prepend 周边任何无关高度变化(loading 行、
图片)双算进去,正是「翻页跳到新内容顶部」的根因。接口用结构类型
(getBoundingClientRect/isConnected/scrollTop/scrollHeight),纯函数可测。
消费方:[[../components/chat/ChatPanel]]。
