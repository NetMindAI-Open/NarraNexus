---
code_file: frontend/src/components/settings/__tests__/ModelDefaultsSettings.test.tsx
last_verified: 2026-07-18
stub: false
---

# ModelDefaultsSettings.test.tsx

钉住 Model Defaults 编辑器的云端 netmind-only 前端行为（api / i18n /
configStore / runtimeConfig 全 mock）：云端普通用户两个 provider 下拉只剩
netmind 卡 + 底部"下载本地版"note + 框架下拉可交互但选到不同框架时弹
useConfirm 样式弹窗、值弹回、`setAgentFramework` 不被调用、点 OK 消失；
云端 staff 全量选项、无弹窗、正常切框架；本地全开无 note。i18n mock 返回
内联默认串，断言读真实文案。
