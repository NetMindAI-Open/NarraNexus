---
code_file: frontend/src/components/settings/BrowserManualInstall.tsx
last_verified: 2026-09-23
stub: false
---

# 手动安装原文复制

只呈现安装器提供的命令，不推断平台、可执行文件路径或拼装 shell 内容。
空命令不生成替代说明；只读文本框保留换行和引号，复制失败时选中文本并明确
报告错误。命令更新会重建复制状态，避免新命令沿用旧的成功提示。

实际 GET /runtime 与安装响应提供 manual_install。安装、状态检查和取消选项
分别使用 command/status_command/cancel_command；shell 与 root 按原文显示，
提示命令应在运行 NarraNexus 的主机执行，避免远程客户端把服务端路径当成本地路径。
