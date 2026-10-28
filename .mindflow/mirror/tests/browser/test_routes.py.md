---
code_file: tests/browser/test_routes.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

fixture 固定 `NARRANEXUS_DEPLOYMENT_MODE=local`：真实 auth 中间件只在本地模式接受裸 X-User-Id，而模式由环境变量
解析；全量运行时前面留下的环境会把这里所有请求变成 401（此前 6 个既有失败即源于此）。云端用例自行打桩 `_is_cloud_mode`。

## 2026-10-08（PR #410 review）

审批端点改为 notices；钉住旧审批端点 404、安装/取消安装在云端 403 且不触发安装。

# 浏览器路由边界

运行时接口及流连接继续验证身份与所有权、错误处理和跨进程连接。会话夹具使用空策略，
普通网页无需额外允许字段；高级权限模型的 HTTP 请求形状由 policy_management 覆盖。
流接口夹具实现单次补帧接口；真实缩放与画面一致性由浏览器集成测试负责。
