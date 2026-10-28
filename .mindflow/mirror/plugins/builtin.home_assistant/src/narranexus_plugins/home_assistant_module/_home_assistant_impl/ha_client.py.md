---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/_home_assistant_impl/ha_client.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（三）— 部署判定与共享包装读同一个符号

PR#418 增量 review I4：`validate_base_url` 改调 `deployment_mode.is_cloud_mode()`（模块属性），
不再 `from … import is_cloud_mode` 绑本模块副本——与 [[url_safety]] 包装、[[registry]] 同一写法。
否则同一次调用里有两个判定点：谁 patch 了本模块的副本，HA 就进 cloud 分支，而包装判 local 直接返回，
**闸口一点都没跑**、测试却是绿的。local 分支的 `_assert_not_link_local` 保持不动（局域网 HA 合法）。
测试 `test_validate_base_url_reads_the_same_mode_source_as_the_egress_wrapper`：env=local 但
patch `deployment_mode.is_cloud_mode → True`，私网地址必须被拒；回退成绑定副本即变红。
HA 测试继续用 setenv，**不要**回退成 `monkeypatch.setattr(hc, "is_cloud_mode", …)`（现已无此属性）。

## 2026-10-28（二）— cloud 分支改调共享包装，本地策略不动

PR#418 review I1：`validate_base_url` 的 cloud 分支改调 `url_safety.assert_public_http_url_in_cloud`
（部署感知判定只在 [[url_safety]] 一处），失败仍翻译成本层的 `HAError(_CLOUD_PUBLIC_ONLY)`。
**local 分支的 `_assert_not_link_local` 刻意不并入**：本地 HA 合法在私网，只拒 link-local，
是另一种策略；并进包装会把局域网 HA 一起拒掉。所以本文件仍自己按 `deployment_mode.is_cloud_mode()` 分流（「（三）」起读模块属性）。

- **触发**：测试只 patch `ha_client.is_cloud_mode` → **症状**：走了 cloud 分支但包装按 env 判成
  local，内网 host 被放行 → **根因**：cloud 判定实际由包装再做一次。HA 测试已改为
  `monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", ...)`，两处判定读同一个 env。
- 未做（review M2/M3，留后续）：`PUT /binding` 保存时不校验 base_url（请求时每次都过门，无 SSRF）；
  每次请求都重新解析 DNS。

## 2026-10-28 — 自写的 URL 检查换成共享出网门；DNS 失败改为拒

- 旧 `validate_base_url` 自己 `socket.getaddrinfo` 判段，且 **DNS 失败直接放行**（注释写着
  "surfaces later as a connection error"）——云端等于给 rebinding / 解析抖动留了口子。
  现在：`_check_url_shape`（同步、无 DNS：http/https、有 host、拒元数据主机名和字面
  link-local IP）在构造时跑；`validate_base_url` 改成 **async**，云端再走共享
  `assert_public_http_url`（解析后判定，DNS 失败 = 拒），失败统一 `HAError` 固定文案，
  不回显解析出的 IP。
- 门在 **每次 `_request` 之前**跑，而不只是构造时：客户端活得比一次 DNS 应答久，每次重验
  缩小（不封死）rebinding 窗口。
- aiohttp 请求 `allow_redirects=False`；3xx 报 `HAError`（提示填最终 URL），不跟——公网 HA
  302 到内网就是绕门。
- `aiohttp.ClientError` 的异常文本只进日志，`HAError` 只说 "could not reach Home Assistant"。
- 本地模式：LAN 照常放行（192.168.x / homeassistant.local 是本地 HA 的正常形态），但仍
  **解析主机并拒任何 link-local 应答**（`_assert_not_link_local`，解析失败也拒）。只拒字面 IP
  不够：`http://2852039166`（169.254.169.254 的十进制写法）、`http://169.254.169.254.`（尾点）、
  `http://instance-data`（EC2 元数据名）都能绕过字面检查（review 指出）。这是与云端不同的策略
  （私网是本地 HA 的本意），所以不是 `assert_public_http_url`。形状检查也先去掉 host 尾点再比
  元数据主机名。

# ha_client.py — HA REST 薄客户端

## 为什么存在

对接用户 HA 实例的最小异步 REST 客户端(aiohttp + Bearer Long-Lived Token)。**只碰 HA 的稳定 REST
API**(HA Core Apache-2.0),品牌无关(小米/Aqara/Hue 都是 HA entity)。每次调用开一个短连、带超时。

## 关键点

- 方法:`ping`(GET /api/,连通+鉴权探测)、`list_states`、`get_state`、`list_services`、`call_service`。
- `HAError` 统一封装网络/鉴权/非 2xx;401 特判为"检查 token"。**错误串绝不回显上游响应体**——否则
  盲 SSRF 变成可读 SSRF;只留状态码。
- **SSRF 护栏(部署感知,2026-10-28 改)**:构造时 `_check_url_shape` 恒拒非 http/https + 元数据主机名 +
  字面 link-local IP;每次请求前 async `validate_base_url`——**云端**走共享 `assert_public_http_url`
  (解析后判定、DNS 失败即拒),**本地/桌面**放行私网段(本地 HA 合法在 192.168.x / homeassistant.local)
  但解析后拒 link-local 应答。
  backend 容器和 broker/mcp/llm-gateway 同网,用户自填内网 host 就是打进集群的 SSRF;云端用户本就自带
  公网 HA(Nabu Casa)。不跟重定向。

## 上下游

- **被谁用**:`binding.resolve_client`(构造)、`routes`(测连 ping)、MCP 工具(经 resolve_client)。
- **依赖**:aiohttp、`utils.url_safety`(云端出网门)、`utils.deployment_mode`。
