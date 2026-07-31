---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/prompt_cache.py
last_verified: 2026-07-29
stub: false
---
# modeling/prompt_cache — 缓存策略纯函数

loop 侧的 C2 半边:工具确定性排序(展开只尾部追加)、断点计划(前导 system 段+历史尾各一枚)。平台前缀字节稳定是平台侧里程碑,此处不越界。cache_hit_metrics 供成本透明(E5)。
