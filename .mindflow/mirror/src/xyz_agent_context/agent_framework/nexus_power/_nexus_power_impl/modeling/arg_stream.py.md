---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/arg_stream.py
last_verified: 2026-07-29
stub: false
---
# modeling/arg_stream — 流式参数抽取(真 tokenizer)

「回复也流式」的技术核心:容器栈+键值位置+跨片段转义/\uXXXX 处理,只流根层声明字段(嵌套同名不泄漏;数组元素不腐蚀键追踪——曾修的两个坑)。finalize 校齐保证流出==最终值(锁定不变量)。pi 三纪律按构造满足。
