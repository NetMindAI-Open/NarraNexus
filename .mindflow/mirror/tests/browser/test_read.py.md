---
code_file: tests/browser/test_read.py
last_verified: 2026-09-23
stub: false
---

# 可操作读取回归

网页快照及分页不要求访问授权；非网页 URL 仍不能读取。任意脚本单独要求 full_cdp_access，
接管期间等待。真实 DOM 用例覆盖 selector、表单信息、密码脱敏、Unicode 分页及固定动作，
通过 NARRANEXUS_RUN_BROWSER_E2E 开启，只使用隔离 HTTP fixture 和临时 profile。
