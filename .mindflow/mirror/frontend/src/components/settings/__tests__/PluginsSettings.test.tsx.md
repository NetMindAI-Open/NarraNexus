---
code_file: frontend/src/components/settings/__tests__/PluginsSettings.test.tsx
last_verified: 2026-08-28
stub: false
---

# PluginsSettings.test.tsx

钉住插件面板的状态机：未安装只显示 Install（不显示 Uninstall）；已安装显示
版本号 + Uninstall（不显示 Install）；`cloud_managed: true` 时组件渲染为空
DOM（云端央管，本地装/卸载无意义）；点击 Install 后先看到流式进度行、
`installPlugin` 的 promise resolve 后再看到 Uninstall 按钮（用手动 resolve 的
deferred promise 模拟真实的"先流式 emit 再收尾"时序，避免 mock 同步 resolve
导致中间态被跳过）；安装失败时展示 `final.error` 且 Install 按钮保留可点。
`api` 全 mock，无网络。
