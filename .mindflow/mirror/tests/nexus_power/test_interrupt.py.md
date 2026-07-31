---
code_file: tests/nexus_power/test_interrupt.py
last_verified: 2026-07-30
stub: false
---
# tests/interrupt — 流内打断

provider 流被显式提前关闭(closed_early 经 finally 探测,锁 aclose 不是裸 break)、
end_reason=INTERRUPTED 而非 NO_MORE_ACTIONS、部分独白折叠进账本、未取消路径不变。
