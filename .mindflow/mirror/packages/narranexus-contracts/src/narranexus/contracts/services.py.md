---
code_file: packages/narranexus-contracts/src/narranexus/contracts/services.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r2 I-B）— 新 ServiceRef `JOB_RESUME_FOR_PRINCIPAL`（`jobs.resume_for_principal`）

类型 `async (db, user_id, paused_reasons: Iterable[str]) -> int`，由 builtin.job 提供：解封账户时恢复
封号暂停的 job。放在 contracts 而非 kernel 的理由同上（ref 是契约数据）；`__all__` 同步导出。


## 2026-09-07（批 1 四轮复审移植）— `SecretStore` 来源改口；返回标注也钉住

docstring 此前称 `SecretStore` 「按 marketplace `SecretBox` 抄」，但 `SecretBox` 是加解密 codec 不是按 owner+name
存取的 store——现在老实写「仓内今天没有同形实现，等第一个提供者落地再对照」。`test_services_shapes.py` 对 ABC 与
三个方言后端逐个比对参数名/默认值/**返回标注**（前缀规则：实现可多带有默认值的参数）。

## 2026-09-07（批 1 三轮复审移植）— 形状按真实实现抄，并写明来源

`DatabaseBackend` 与 `platform/utils/db/db_backend.DatabaseBackend`（三个后端的 ABC）同名不同形是复审的
Important：`execute` 关键字名（`sql` vs `query`）、默认值、`probe` 返回类型（`bool` vs `None`）都对不上。现在契约照抄
ABC 的 `execute/execute_write/probe/placeholder/dialect` 子集（同名是有意的——它就是那个服务经由 slot 的视图），
`tests/nx_kernel/contracts/test_services_shapes.py` 用 `inspect.signature` 钉住参数名与默认值一致、`SQLiteBackend`
结构化满足契约。`SecretStore`/`AuthProvider`/`EventSink` 的形状来源写进 docstring；`AuthProvider.authenticate`
改为 `async def` 与同文件其它 Protocol 一致。`ServiceRef` 部分不动。

## 2026-09-07 — 三条 ServiceRef 从内核搬来，`ServiceRef` 本身也搬来了（批 6c，A2-3）

`skills.workspaces` / `jobs.instances` / `jobs.run_once` 原来住在
`src/narranexus/kernel/plugins/service_refs.py`——那意味着**内核按名字认识两个业务插件**，
而 `kernel/` 恰恰是宪章 §2.9 里唯一「加实现不许改动」的目录：加第四个跨插件服务
就得改内核。三条 ref 的泛型参数原本还都是 `Any`，等于只有一个字符串键、没有契约。
现在它们带上真类型（`SkillWorkspace` / `JobRunOutcome`），本文件成为它们的家。

`ServiceRef` 这个 dataclass 也一并搬了过来。它是**契约数据**（一个名字加一个类型），
调用两侧（expose 的插件和 require 的插件）本来就都依赖 contracts；
留在内核会逼出一个死结——contracts 是叶子包，import-linter 第一条契约不许它 import
`narranexus.kernel`，连函数体内的延迟 import 也会被 grimp 抓到。
`narranexus.kernel.plugins.services` 现在从这里 re-export 它，
定位器（`ServiceLocator` / `ScopedServices`）仍是内核机械。

同批新增 `WEB_HOST`：插件 router 用的请求作用域宿主 API（见 [[web]]）。

## 2026-09-03（批 1）— `kernel.*` 位的宿主服务契约

`DatabaseBackend` / `SecretStore` / `AuthProvider` / `EventSink` 四个结构化 Protocol，分别是
`kernel.db` / `kernel.secrets` / `kernel.auth` / `kernel.events` 位的契约符号。它们是发行版级选择
（`distribution_only`，插件运行时永不绑定），批 1 只把形状立起来让扩展位树的每个符号真实可 import；
现有实现（`utils/db` 三个方言后端、`secret_box`、本地/NetMind 鉴权、`kernel.events.EventBus`）
在各自包迁入内核时成为默认提供者。方法集刻意最小（按今天的调用面抽象），alpha。
