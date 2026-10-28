---
code_file: frontend/src/lib/replyLanguageSync.ts
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 (r3) — 守卫双寿命化

订阅按**页面**恰一次(N 订阅=每次切换 N 个 PUT);回填按**用户**各一次(`backfilledUsers` Set——logout/login 是纯 SPA,模块布尔会跳过同标签页的下一个用户)。`initReplyLanguageSync(userId)`:无身份直接 return(logout 空档绝不发 PUT,防写错身份/401 触发 auth-expired)。测试改 resetModules+动态 import 隔离(r3 抓的恒真空测试已换真断言)。

# replyLanguageSync — 回复语言同步(PR #284 review #2)

UI 语言多数是**被检测**的(localStorage nx_lang / navigator),不是被点出来的——只挂在 LanguageToggle 上的写透覆盖不到存量用户。本模块两件事:① 订阅 i18n `languageChanged`,任何改语言路径都 PUT 落库;② 认证后 boot 一次对账:服务端 null + 检测语言受支持 → 一次性回填 PUT。全程 fire-and-forget,失败静默(下次切换自然重写)。幂等 init 守卫防重复订阅。调用方:[[MainLayout.tsx]]。
