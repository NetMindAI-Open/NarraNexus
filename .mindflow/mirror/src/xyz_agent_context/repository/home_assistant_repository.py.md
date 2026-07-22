---
code_file: src/xyz_agent_context/repository/home_assistant_repository.py
last_verified: 2026-07-15
stub: false
---

# home_assistant_repository.py — HA 绑定的纯 CRUD

## 为什么存在

`instance_homeassistant_bindings` 表的数据访问层(`BaseRepository` 子类)。**一 agent 一行**,连接配置
以 JSON 字符串存 `config_json`。key 选 `agent_id`(照 Lark 凭据模型),让绑定成为 agent 级配置、
不依赖 module instance 是否已创建。**仓库保持纯 CRUD**——把 config_json 解析成 `HAConfig` 是模块层的事。

## 关键点

- `HABindingRow` 是 dataclass entity(raw 列:agent_id / config_json / 时间戳),与 schema 里的
  Pydantic `HAConfig/HABinding` 分工不同(后者是配置/API 模型)。
- `upsert_config(agent_id, config_json)` 幂等(命名避开 `BaseRepository.upsert(entity)` 的签名冲突);
  `get_by_agent` / `delete_by_agent` 单 agent 操作。

## 上下游

- **被谁用**:`binding.resolve_client`(读)、`backend/routes/home_assistant`(读写)。
- **依赖**:`repository/base.BaseRepository`。表定义在 `utils/schema_registry`。
