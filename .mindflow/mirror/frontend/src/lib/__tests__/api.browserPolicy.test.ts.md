---
code_file: frontend/src/lib/__tests__/api.browserPolicy.test.ts
last_verified: 2026-09-23
stub: false
---

# 权限 API 请求合同

附带运行时来源请求验证：统一身份头、PUT 方法、仅含 source 的请求体，不接收路径。
模式请求同样验证统一身份头与 PUT，且只发送 mode。

验证 agent 路径编码、身份头、单项权限更新和撤销请求体，以及服务端错误能够
通过统一 ApiError 返回。请求不包含完整策略或客户端生成的上下文授权标识。
管理及撤销只接受 full_cdp_access，返回视图不再带网址访问默认值或临时授权计数。
