---
code_file: src/narranexus/kernel/plugins/bisect.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.5）— 二分定位坏插件

安全模式之后的用户动作：`start()` 把嫌疑集（当前启用或被自动 disable 的用户插件）一半启用，用户重启后答
good/bad：good → 试验半清白、嫌疑缩到另一半；bad → 另一半清白、嫌疑缩到试验半。O(log N) 次重启收敛到唯一
嫌疑；`stop()` 把清白者全部重新启用、只留罪魁 disabled 并写 warning。状态持久化在 `registry.json.bisect`
（必须跨重启），改动只有 enabled 标志。参数化测试覆盖 2/5/8/9 个插件。
