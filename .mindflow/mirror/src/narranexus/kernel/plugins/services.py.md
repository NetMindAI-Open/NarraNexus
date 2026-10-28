---
code_file: src/narranexus/kernel/plugins/services.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `ServiceRef` 迁去 contracts，本文件 re-export（批 6c，A2-3）

ref 是契约数据（名字 + 类型），定位器才是内核机械。
`ServiceRef` 因此定义在 `narranexus.contracts.services`，本文件从那里 import 并 re-export
（`narranexus.sdk` 也从 SDK 侧 re-export 给插件作者）。
这么做的直接原因：三条业务 ref（skills / jobs）要搬去 contracts，
而 contracts 是叶子包，import-linter 第一条契约不许它 import 内核——
连函数体内的延迟 import 都会被 grimp 抓到，所以只能让类型跟着 ref 走。

## 2026-09-03（批 2b.3）— `ServiceRef` 依赖注入（Backstage 式）

插件间调用不走 `import nxplugins.<other>`：提供方 `expose(ref, impl)`，依赖方 `require(ref)`。root 作用域放宿主
自己的服务（`SECRET_STORE`/`EVENT_SINK`/`HOST_VERSION` 三个 ref 常量），插件作用域 `ScopedServices` 只能以
自己的 id expose。`require` 未知 ref 直接 `UnknownEntry` 并列出现有——静默 `None` 正是「装了但什么都不做」
的病根。`release_owner` 在插件停用时收回它暴露的门面。
