---
code_file: tauri/src-tauri/src/commands/netmind_oauth.rs
last_verified: 2026-07-13
stub: false
---

# netmind_oauth.rs — 桌面端 NetMind(Power)OAuth 桥接

## Why it exists

Web 版 OAuth([[useNetmindAuth.ts]] `startOAuth`)靠浏览器弹窗打开 NetMind
`auth.html` + `window.opener.postMessage` 回传结果。这套在打包桌面 webview
(WKWebView)里不工作:弹窗被拦、无跨窗 opener 通道。本地桌面在 Power 登录落地前
从没跑过 NetMind OAuth,是新面。

## 捕获(两条独立路径,双保险)

Rust 建子 webview 加载 `auth.html`,`on_navigation` 里两路抓结果:
1. **URL 匹配(主,零依赖 opener)**:provider 认证完会把 webview 重定向回
   NetMind redirect_uri,带 `?code=&state=`。只有最终回调同时带这两个参(provider
   授权 URL 只有 state 无 code),故要求二者都在才命中,取出后合成
   `{type:'auth',code,state}` JSON。`return false` 取消该页加载——服务端换取由
   前端 `handleAuthCallback`→`/user/userCallBack` 完成,和 web 版等价。
2. **opener shim + 哨兵(兜底)**:初始化脚本合成 `window.opener`,其 postMessage
   跳到哨兵 URL `https://nmoauth.callback/#<encodeURIComponent(JSON)>`,on_navigation
   命中后取 fragment。

## 投递(不依赖事件监听)

命中后 `deliver()` 把 payload 存进 [[state.rs]] 的 `pending_netmind_oauth` 槽,
前端启动 OAuth 后**轮询** `take_netmind_oauth_result`(invoke)取走——这条投递不
依赖 `window.__TAURI__` 的实时事件(该全局在本项目未开 withGlobalTauri,`listen`
可能静默失效,正是首个 DMG 测试里"子窗关了但主窗还在登录页"的根因)。同时也
`emit("netmind-oauth-callback")` 一份作冗余,当前前端不监听。

## 关键决策 / 踩坑

- **payload 双格式统一**:URL-match 存纯 JSON,哨兵存 URI-encoded JSON;前端
  `decodeURIComponent`(对纯 JSON 是 no-op)+`JSON.parse` 两者通吃。
- **无 capability 改动**:子 webview 只导航,不 invoke、不 emit;缓存/emit 是
  Rust 特权。默认 capability 仅覆盖 "main"。
- **窗口/缓存复用**:每次开窗前先关旧窗、清空旧缓存,连点不叠、不串。
- 首个 DMG 实测结论:机制(建窗、跳转、关窗)通;Google 内嵌 webview 里
  **passkey/WebAuthn 用不了**(需真浏览器),用"其他方式→密码"可绕过 → 由 URL
  匹配路径接住 code/state。若将来要支持 passkey,只能走系统浏览器 + deep-link
  (需 NetMind 侧支持重定向到 narranexus://)。

## 未验证(编译需在有 Rust 的打包机)

`on_navigation`/`initialization_script`/`WebviewUrl` 写法在 tauri 2.11 已随首个
DMG 编译通过。运行时的 URL-match 能否在 provider 回调那一跳命中(取决于 NetMind
redirect_uri 把 code/state 放 query 还是 fragment),需实测;放 fragment 的话把
`query_pairs()` 换成解析 fragment 即可。
