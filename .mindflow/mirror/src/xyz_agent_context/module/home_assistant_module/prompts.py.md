---
code_file: src/xyz_agent_context/module/home_assistant_module/prompts.py
last_verified: 2026-07-14
stub: false
---

# prompts.py — HomeAssistantModule 的 Layer-1 指令

## 为什么存在

注入 system prompt 的模块指令(通用能力描述):agent 能经 Home Assistant 查/控设备、怎么做、以及安全
规则(高影响动作先确认、未绑定时引导去配置页或 `home-assistant-setup` 技能)。

## 关键点

- **只放通用逻辑**(铁律 #4)——具体家居布局/习惯/联动在各 agent 的 Awareness,不在这。
- **不能有裸 `{}`**:`get_instructions` 走 `str.format(**ctx_data)`,花括号会 KeyError/崩。
