---
code_file: src/xyz_agent_context/schema/home_assistant_schema.py
last_verified: 2026-07-15
stub: false
---

# home_assistant_schema.py — HA 集成的 Pydantic 模型

## 为什么存在

Home Assistant 集成的数据模型。`HAConfig` = 连到某个用户 HA 实例所需的连接信息
(`base_url + token + verify_tls`),以 JSON 存进 `instance_homeassistant_bindings.config_json`。
`HABinding` = 一条 per-agent 绑定记录的语义视图(key = `agent_id`,照 Lark)。

## 关键点

- **token 是敏感凭证**:随 bundle 导出时默认擦除、前端只回显掩码(和 skill-secret / channel-credential
  同款处理)。
- **部署无关**:`base_url` 既可是局域网 HA(本地/桌面),也可是暴露出来的 HA(云端 Nabu Casa/反代)——
  模块层只认 url+token,不关心云/本地。

## 上下游

- **被谁用**:`home_assistant_repository`(config_json 序列化)、`_home_assistant_impl/binding`(解析成
  HAClient)、`backend/routes/home_assistant`(绑定 CRUD)。
