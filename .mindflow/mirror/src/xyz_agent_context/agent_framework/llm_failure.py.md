---
code_file: src/xyz_agent_context/agent_framework/llm_failure.py
last_verified: 2026-07-07
stub: false
---
# llm_failure.py — LLM 失败的统一分类 + 密钥脱敏

## 为什么存在

每条后台 LLM 路径都要回答同样两个问题：**"这是不是凭据/鉴权失败？"** 和
**"怎么把这个错误给用户看又不泄露他的 key？"** 在此文件之前，这套逻辑只存在于
`message_bus_trigger`，其余路径（narrative updater、Step-5 entity/memory hooks）
直接把 401 静默吞掉。2026-07 事故——平台 OpenAI key 过期，长记忆退化约两周无告警——
的根因之一就是这套判断没有被复用。本文件把它收敛成单一真源。

- `is_credential_error(err)`：对**原始**错误串做粗粒度子串匹配（`CREDENTIAL_ERROR_MARKERS`）。
  只用于决定 owner 提示文案 + 审计分类，绝不改变重试/投递行为。接受 str 或异常。
- `redact_secrets(text, max_len)`：给**要展示**的错误串脱敏（`sk-...` / `key=...` /
  `Bearer ...`）并截断。不是安全边界，只覆盖 SDK 常见回显形态。

分类读原文、脱敏产出展示文——两者刻意分开：分类必须看未脱敏的文本。

## 下游

- `message_bus_trigger._classify_error` / `_redact_error_for_owner` 委托到这里。
- `services/background_llm_alerts` 用它给后台失败分类 + 脱敏。
- narrative updater / Step-5 hooks 用 `is_credential_error` 判断是否要告警。
