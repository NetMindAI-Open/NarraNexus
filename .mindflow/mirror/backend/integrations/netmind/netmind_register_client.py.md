---
code_file: backend/integrations/netmind/netmind_register_client.py
last_verified: 2026-08-05
stub: false
---

# netmind_register_client.py — 自建注册页要的两个接口

## 为什么这两个调用走后端而不是浏览器

登录页确实是浏览器直连 NetMind 的,但注册是最需要中间有台服务器的地方:

- **`/register/sendCode` 会以我们的名义发邮件** —— 必须限流。而**写在页面里的限流不叫限流**。
- 上游的报错文案要能映射成用户看得懂、且能行动的东西。
- 不把上游域名暴露在页面的 network 面板里。

## base_url 绝不能写死

复用 `NETMIND_AUTH_API_URL` —— dev 是 `userauth.protago-dev.com`,prod 才是文档
里那个 `auth-api.netmind.ai`。写死等于 dev 上注册会去动生产账号系统。

## 密钥纪律(文档「注意事项」明确要求)

**密码和验证码绝不能进日志、埋点、错误上报。** 这里每一条日志只带 email;异常
类型携带的是上游的 **message**,永远不是请求体。

**不要**在这里加一行「调试用」的 payload 日志 —— 把 HTTP 调用包在这个 client 里
而不是让调用方内联,唯一的理由就是这个。

## subscribeFlag 为什么传 1

`2` 是订阅 newsletter,`1` 是不订阅。表单只收三个字段,里面没有订阅勾选框 ——
那么替用户勾上就是他从没给过的同意。要改成 `2` 之前,先加勾选框。

## 密码规则在两边都校验

前端有一份实时清单是为了**反馈**,这里这份是**保证**。客户端校验从来不是保证。

## 2026-08-05 — 上游拒绝/异常全部落结构化日志（[signup-funnel]）

8/1 活动 signup 400×17 无法分桶的直接修复：`_post` 现在对三类非成功路径各落
一条 `[signup-funnel]` warning——refusal（带上游 msg）、5xx（带响应片段）、
非 JSON（带响应片段）。`_post` 新增 keyword `email` 仅作日志上下文。

密钥纪律不变且被测试钉住：日志携带的是**响应**（上游 msg/响应片段），永远
不是请求体——密码和验证码在响应里不可能出现,在日志断言里被显式排除
（tests/backend/test_auth_funnel_observability.py）。
