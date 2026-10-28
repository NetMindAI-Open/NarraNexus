---
code_file: tests/browser/test_runtime.py
last_verified: 2026-09-24
stub: false
---

# 运行时就绪状态的可信语义

存在文件不足以证明浏览器可用，只有成功版本探测才能报告 ready；空输出、
探测异常和路径发现异常都必须转为不可用。状态不缓存，以便用户安装、删除或
修复运行时后立即反映实际情况，并为 API 提供稳定的可序列化结果。
