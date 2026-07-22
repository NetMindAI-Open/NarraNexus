---
code_file: src/xyz_agent_context/module/home_assistant_module/_home_assistant_impl/ha_client.py
last_verified: 2026-07-17
stub: false
---

# ha_client.py — HA REST 薄客户端

## 为什么存在

对接用户 HA 实例的最小异步 REST 客户端(aiohttp + Bearer Long-Lived Token)。**只碰 HA 的稳定 REST
API**(HA Core Apache-2.0),品牌无关(小米/Aqara/Hue 都是 HA entity)。每次调用开一个短连、带超时。

## 关键点

- 方法:`ping`(GET /api/,连通+鉴权探测)、`list_states`、`get_state`、`list_services`、`call_service`。
- `HAError` 统一封装网络/鉴权/非 2xx;401 特判为"检查 token"。**错误串绝不回显上游响应体**——否则
  盲 SSRF 变成可读 SSRF;只留状态码。
- **SSRF 护栏(`validate_base_url`,部署感知)**:恒拒非 http/https + 云元数据地址(169.254.169.254)+
  link-local。**本地/桌面**放行私网段(本地 HA 合法在 192.168.x / homeassistant.local)。**云端**
  (`is_cloud_mode()`)额外拒解析到 private/loopback/reserved 的 host——backend 容器和 broker/mcp 同网,
  用户自填私网 host 就是打进集群的 SSRF;云端用户本就自带公网 HA(Nabu Casa)。

## 上下游

- **被谁用**:`binding.resolve_client`(构造)、`backend/routes/home_assistant`(测连 ping)。
- **依赖**:aiohttp。
