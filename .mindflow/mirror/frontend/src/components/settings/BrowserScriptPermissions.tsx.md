---
code_file: frontend/src/components/settings/BrowserScriptPermissions.tsx
last_verified: 2026-09-23
stub: false
---

# 高级脚本设置

普通网址访问完全免授权，此组件仅管理 full_cdp_access，不显示访问状态、禁用或例外。
用户输入 origin 后只增加本地草稿，勾选并明确保存才写入脚本权限；关闭后保存独立撤销。
读取和保存复用认证 API，校验返回 agent 归属与能力字段。切换 agent 清空本地状态，
失效请求不能覆盖新对象；失败保留可重试错误和未保存草稿。不展示未实现的文件权限。
