---
code_file: backend/middleware/body_size.py
last_verified: 2026-09-24
stub: false
---
# body_size.py — declared-length 体积门的唯一有效层(#334 r3 I1)

## 2026-09-24 · 浏览器设置请求体积

浏览器 policy PUT、revoke POST 以及运行时 source/mode PUT 共用 4 KiB
声明长度上限，在框架解析 origin/capability/verdict 或运行偏好前拒绝超大请求。
单个枚举的设置也有请求体积限制，Pydantic 校验发生在请求缓冲之后，不能替代此门。
与既有中间件一致，这不是对缺失或
虚报 Content-Length 的流式累计限制。

## 2026-09-03（批 2e）— `/api/plugin-factory/*` 写路由统一 64 KiB 上限

安装/二分是几个标量字段；错误上报带 message（≤4000）+ stack（≤20000）加 JSON 外壳，64 KiB 足够。一个正则覆盖
该前缀下所有 POST，新加工场动词不必逐条登记。

## 2026-08-27 — apply-to-agents 真 cap

`BODY_CAPS` 加一条 `POST /api/providers/slots/apply-to-agents` →
`MAX_APPLY_TO_AGENTS_BYTES=4KB`。body 是 `{"slots":[...]}` 固定小形状；路由侧
的 Pydantic `max_length` 只封循环基数（缓冲后才跑），字节上界只有这里（中间件、
缓冲前）能封——所以它走真 cap 而非 `_NO_BODY_CAP_EXEMPT` 豁免。

## 为什么存在

FastAPI 在 solve_dependencies **之前**就 `await request.body()` /
`request.form()`(fastapi/routing.py:328-341)——凡带 body field 的
路由,路由级依赖只能在框架缓冲完之后拿到执行权,「快拒」是结构性
假门。HTTP 中间件包在 app 外面,是唯一先于框架碰流的层。

## 契约

- **按路由配上限,绝不做全局单值**:写入口横跨三个数量级(64KB 编辑
  命令 vs 25MB 文档),一个数字必错一边。BODY_CAPS 首匹配生效。
- Content-Length 可撒谎/缺失——这层只是快拒;流式累计第二道在各
  handler(能拿到流的那两个);UploadFile 路由的真实上界=本层+磁盘
  (框架 spool),诚实写在 artifacts.py 注释里。
- **新写入口必须来这里加一行**,漏配=只剩 handler 侧的门(对带
  body field 的路由=没有门)。

## 2026-08-20(二)— 上限单一驻地 + 漂移钉(#334 r4 I2)

MAX_OFFICE_EDIT_BYTES / MAX_OFFICE_ASSET_BYTES / PUT_CONTENT_MARGIN 的
权威在本文件,routes 反向 import(routes→middleware 合法,反向禁止);
MAX_ARTIFACT_BYTES 从 narranexus.platform.artifact 引入。「门还挂着吗」
不再靠读注释:test_body_size_gate.py 遍历真实路由表验证每条 BODY_CAPS
命中已注册路由 + 中间件确实挂载 + 顺序在 access_log 内。

「**新写入口必须来这里加一行**」这条契约也有执行者了(#334 r5 I2):
`test_every_write_route_has_a_cap_or_an_exemption` 反向遍历——每个已注册
POST/PUT/PATCH 路由要么被某条 BODY_CAPS 命中,要么在测试内的显式豁免
清单 `_NO_BODY_CAP_EXEMPT` 里(按路由模板逐条列,分组注明为何不设上限;
失效豁免**双向**报红:路由消失了报,路由后来拿到 cap 而豁免没删也报,
r6 M2)。新增写端点时 CI 会强制做一次「体积故事」的显式决定。
豁免清单里「小型定形 JSON」一组是否该有平台级默认上限,是一个尚未做的
独立决策——豁免记录的是现状,不是永久背书。

**env-gated 写入口是这道钉的盲区,单独建模**(#334 r6 I1):manyfold 的
4 个写路由只在 `ENABLE_MANYFOLD_API` 置位时注册,CI 不设该 flag,反向钉
看不见它们。测试里分两组显式声明:`_ENV_GATED_EXEMPT`(3 条 manyfold
路由,不进 stale 断言——CI 里无对应活路由)与 `_ENV_GATED_CAPPED`
(`/v1/chat/completions`,body field + 无界 messages 数组,吃真 cap
`MAX_CHAT_COMPLETIONS_BYTES` 8MB;pattern 无条件挂在 BODY_CAPS 里,
flag 关闭时该路径反正 404,条目惰性无害)。两侧各有锚:cap 允许命中
env-gated 模板采样,而 `test_every_env_gated_capped_template_has_a_cap`
反向保证每条声明的模板真有 cap(静态对静态,与 flag 无关)。
2026-08-20 实核:dev 与 prod 容器均未设 `ENABLE_MANYFOLD_API`,
该无上界入口不是现网敞口。

## 2026-09-04 · webhook transport (batch 4c)

Generic channel routes: 64 KiB for bind/test/unbind/set-active, 256 KiB for `/api/channels/{channel}/webhook/{agent_id}`.

## 2026-09-04 · capability switch cap (batch 5c)

`PUT /api/agents/{id}/capabilities/{module}` carries `{"enabled": bool}` — capped at the same 4 KiB as apply-to-agents.
