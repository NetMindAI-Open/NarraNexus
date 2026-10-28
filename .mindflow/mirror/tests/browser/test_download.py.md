---
code_file: tests/browser/test_download.py
last_verified: 2026-09-24
stub: false
---

# 浏览器下载流与续传

通过可控分块传输验证下载中断、取消和恢复，进度包含已有字节且保持单调。
短响应必须保留可续传文件并报告失败，阻塞传输也必须响应取消。镜像只能
使用 HTTPS，并保留原始下载路径，避免恢复时取到不同资源。
