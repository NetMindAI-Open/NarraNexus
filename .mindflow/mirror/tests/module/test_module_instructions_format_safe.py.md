---
code_file: tests/module/test_module_instructions_format_safe.py
last_verified: 2026-09-23
stub: false
---

# 模块指令模板回归

所有能力共用字符串格式化的上下文接口。新增浏览器 JavaScript 示例曾把代码花括号
解释为模板字段，令整个回合失败。本测试从真实模块注册表读取指令，区分合法上下文字段
与未转义的代码块；同时保留故障样本，避免扫描规则退化到什么也检测不到。
