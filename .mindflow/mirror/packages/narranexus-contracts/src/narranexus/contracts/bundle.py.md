---
code_file: packages/narranexus-contracts/src/narranexus/contracts/bundle.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `content.bundles` 位的契约

插件随包携带 `.nxbundle`，市场把它们当可安装模板走同一条 preflight→confirm 流程；`sha256` 必填（64 hex）
让升级时能展示变化。
