---
code_file: src/narranexus/platform/agent_framework/llm/call_tagging.py
last_verified: 2026-08-27
stub: false
---

# call_tagging.py — 计时器打 LLM 型号标的唯一入口

## 为什么存在

review 2026-08-27 round 2 I4:三处业务代码(continuity 计时/judge 计时/
合并计时)各自复制"函数内 import adapters.openai_agents → 读 contextvar
→ t.tag(**info)",每份都在 narrative 业务文件里持有一条具体 adapter 路径
——铁律 #9 要求框架可换,换 adapter 要找齐 N 处,漏一处该计时的型号标
静默变空(时序图上与"没跑"同形)。收敛为 `tag_last_llm_call(timer)`,
业务侧一行、零 adapter 路径。

## 坑

- **必须在 await 调用返回之后调**:型号/结构化输出模式在 SDK 调用内部
  才解析进 contextvar——不能折进 timed() 本体(它在调用前打开)。
- 换 adapter 时只改本文件的一处 import。
