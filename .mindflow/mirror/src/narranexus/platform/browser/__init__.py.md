---
code_file: src/narranexus/platform/browser/__init__.py
last_verified: 2026-09-22
stub: false
---

# browser/__init__.py — 应用内浏览器的公开面

## 为什么存在

方案三（应用内浏览器）的对外导出点。遵循仓库的 Service + 私有实现分层：具体逻辑全在
`_browser_impl/`，调用方只从这里 import，不直接伸手进私有包。

之所以是 `platform/browser/` 这个位置而不是插件里：运行时检测、会话生命周期、帧分发
是**平台能力**（与 `platform/artifact/` 同级同构）；而 agent 侧的 MCP 工具与 Hook 属于
`plugins/builtin.browser`。两者分开，插件关掉时平台侧不受影响（铁律 #3）。

## 上下游

- 下游：`plugins/builtin.browser`、`backend/routes` 的浏览器路由、`UrlRenderer` 的
  `stream` 分支所依赖的后端接口
- 设计意图：本目录各 mirror md（`.mindflow/mirror/src/narranexus/platform/browser/`）
