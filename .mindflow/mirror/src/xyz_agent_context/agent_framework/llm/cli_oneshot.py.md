---
code_file: src/xyz_agent_context/agent_framework/llm/cli_oneshot.py
last_verified: 2026-07-31
stub: false
---

# cli_oneshot.py — codex 一发调用的共享内核

## 为什么存在

「经注册的 codex agent-loop 驱动跑一轮 tool-free、把回话/错误收回来」
这套脚手架出现了第三份拷贝(helper 的 `_run_codex_oneshot_inner`、
verify_live 的验证一发),PR #224 review 第 5 条点名抽取。两个消费方
在此之上各做各的判定:helper 要 text+token 计数(空文本+错误 →
RuntimeError 供 #68 分类),验证要 verdict(错误事件 → dead)。

## 本模块拥有什么

- **事件契约**:codex 翻译器吐 `raw_response_event`,text 走
  `response.text.delta`,usage 落 `response.done`,失败是终态错误
  **事件**而非异常;error_type 与 error_message 必须都保留
  (unauthorized 的 message 单独不带凭证标记)。
- **一次性 per-uid cwd**(`oneshot_cwd`):绝不能用 backend 进程 cwd
  (codex 由 cwd 派生 writable_roots,prompt 注入的 helper 输入可能碰
  到应用树);共享主机上校验 `st_uid`(exist_ok=True 会静默收养他人
  预建的同名目录——PR #224 review 第 6 条),失败回落私有 mkdtemp。
  这同时补上了 cli_helper._HELPER_CWD 的同款缺口(那个目录仍在
  cli_helper 里,namespace 不同)。

## 本模块不拥有什么

环境 `_codex_ctx` 的 CodexConfig——装哪张卡的配置正是两个调用方的
本质区别(helper 装自己槽位的,验证装被测卡的),必须由调用方装/卸。

## 上下游

消费方:[[cli_helper]] `_run_codex_oneshot_inner`、
[[codex_oauth]] `verify_live`。
