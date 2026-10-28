---
code_file: src/narranexus/platform/artifact/_artifact_impl/edit_bridge.py
last_verified: 2026-08-19
stub: false
---

# edit_bridge.py — html 逐元素编辑桥(注入脚本)

## 为什么存在

编辑手势发生在沙箱 iframe **内部**,父窗口够不着——raw 路由在
`?edit_bridge=1` 时把本脚本注入 ENTRY html(仅 entry、仅 text/html)。
桥是**纯传感器**:点击文本叶子元素(子节点仅文本/行内标签白名单)→
该元素 contentEditable;回车=软换行 `<br>`(结构拆分是 AI 的活,
不猜);Cmd/Ctrl+B/I 行内加粗斜体;失焦且 innerHTML 变了→postMessage
{innerBefore, innerAfter, outerBefore} 给父窗口。**不带写回逻辑、
不带秘密**;写回在 HtmlRenderer(锚定替换+PUT)。

## 坑

- entry 的 CSP 已含 script-src 'unsafe-inline',注入才可执行——收紧
  CSP 前先想到这里。
- BLOCKED 名单里有 A(链接点击别变编辑);postMessage 目标 '*' 安全性
  依赖父侧 event.source===iframe.contentWindow 校验(HtmlRenderer)。
- BRIDGE_MARKER 字符串同时是注入测试的钩子与消息 type 前缀,改名要
  三处同步(本文件/HtmlRenderer/测试)。
