---
code_file: frontend/src/components/artifacts/renderers/__tests__/BrowserStreamPanel.test.tsx
last_verified: 2026-09-23
stub: false
---

# 浏览器面板基础行为

系统 Chrome 不可用时应展示对应恢复指引，不提供托管浏览器下载按钮。
运行时来源测试数据包含独立的 mode 字段；面板仍依赖实时会话，不按下次启动模式断流。
新会话运行时不可用时仍订阅已有会话，收到 hello 后显示现有浏览器，不再遮挡成安装卡。

保护用户可恢复的状态：缺少运行时与已安装但启动失败必须区分，安装错误必须可见，
未知下载总量不能伪造进度。观看不授予输入权，只有服务端确认本连接持有控制权才可操作。
WebSocket 和绘图替身只验证组件协议；真实 Chromium 与画面质量由独立端到端检查覆盖。
