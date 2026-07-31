---
code_file: src/xyz_agent_context/agent_framework/llm/litellm_client.py
last_verified: 2026-07-29
stub: false
---
# llm/litellm_client — 全仓唯一 litellm import 点

只管连接与透传(一次流式调用、原始 chunk、超时);不做语义/分类/选模。懒加载(内存纪律:不触模型的进程不付 litellm 足迹);drop_params 防方言参数硬失败;num_retries=0(重试是 loop 的策略)。其他文件 import litellm=架构违规(可 grep)。
