---
code_file: tests/chat_module/test_blank_reply_guards.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — 空白回复落库守卫用例

钉住 2026-07-13 空气泡 bug 的落库侧修复：`"\n"`/`"  "` 回复必须落成
"(Agent decided no response needed)" 占位文案而非空白 assistant 行；
空白+interrupted 落 "(Interrupted by user)"（守卫不得压过打断分支）；
带首尾空白的真实回复原样保留（守卫只判空不 trim 内容）。harness 复用
test_interrupted_turn_persistence 的 _params/_reply/_rows 模式。
