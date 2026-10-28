---
code_file: tests/browser/test_limits.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

工具说明一致性用例移到 fixture 所在的集成测试文件，本文件只留纯单元用例。

# 参数上限

刚好到上限可以、多一个字符拒绝；三个接受 selector 的工具上限一致；超长脚本在到达页面前被拒
（即使脚本权限已允许，只有长度能拒绝它）；工具说明里的数字与常量一致。
