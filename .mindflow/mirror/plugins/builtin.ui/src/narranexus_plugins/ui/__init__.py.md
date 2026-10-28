---
code_file: plugins/builtin.ui/src/narranexus_plugins/ui/__init__.py
last_verified: 2026-09-07
stub: false
---

# plugins/builtin.ui/src/narranexus_plugins/ui/__init__.py

## 2026-09-07 — builtin.ui：manifest-only 的前端壳插件（B7）

Python 侧只有 manifest：declares 16 个 ui.* 槽（与 frontend/src/platform/registries/index.ts 的 REGISTRIES 一一对应，包测试钉住），hosts=[backend]，protected+distributionOnly 同 auth 插件；贡献在 TypeScript（builtin.ts 以 owner 'builtin.ui' 注册）。ui 根的 default 早已写着 builtin.ui，这里让它真实存在。四个官方发行版都列入它。
