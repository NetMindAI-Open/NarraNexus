---
code_file: backend/routes/_office_watch_token.py
last_verified: 2026-07-13
stub: false
---

# _office_watch_token.py — office-watch 代理的 HMAC 签名 token

## 为什么存在

和 `_artifact_token.py` 同一个道理:`<iframe src>` 导航(以及 watch 页面自己的
EventSource/fetch 子请求)带不了 `X-User-Id`/Authorization 头,所以 session 鉴权的代理会
401。改为:authed 的 `/office-watch/open` 铸一个短 TTL token(载 `user_id` + `port`),
公共代理路由(`/api/public/office-watch-proxy/{token}/...`)校验它。token 即鉴权,放在 URL
路径里,页面的相对子请求自动带前缀。

## 关键点

- **复用 `_artifact_token` 的签名密钥 + base64 codec + 错误类型**(`_secret` /
  `_b64url_*` / `TokenExpired` / `TokenInvalid`),全应用一套签名姿态,不另起炉灶。
- 载荷 = `{user_id, port, exp}`。`port` 进 token → 公共代理校验路径里的 port 必须与 token
  的 port 一致,缩小爆炸半径。
- TTL 2h(同 artifact token):live 预览 tab 可能开很久,SSE 重连复用同一 token;比 officecli
  watch 自身的 idle 停更长,token 很少比它指向的服务活得久。

## 上下游

- **被谁用**:`office_watch_proxy`(`office_watch_open` 调 `mint`;公共代理调 `verify`)。
- **依赖谁**:`_artifact_token`(密钥/codec/错误类)。
