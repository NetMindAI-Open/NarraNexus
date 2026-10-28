---
code_file: backend/integrations/plugins/service.py
last_verified: 2026-09-07
stub: false
---

# service.py — 插件安装子系统对外的唯一门面

## 2026-09-03（批 1）— 默认插件表在构造时派生

`specs=None` 时调用 `registry.build_plugin_specs()`，不再读 import 期常量：启动后才注册的框架插件对
之后创建的 service 可见。

## 为什么存在

Phase 3 的安装/状态路由不应该知道"Claude Code 有两个安装动作、Codex 只有
一个"这种细节,也不该自己拼装 `asyncio.Lock` 防重入。`PluginService` 把
"列状态 / 装 / 卸载"收敛成三个方法,内部才去遍历 `PluginSpec.components`
派发给对应的 `_installers/` 策略。

## 上下游关系

- **被谁用**：Phase 3 路由（未实现,本次任务范围之外）会实例化一个
  `PluginService` 单例并调用 `list_plugins` / `install` / `uninstall`。
- **依赖谁**：`registry.build_plugin_specs`（默认插件表)、`_installers.pip_target
  .PipTargetInstaller` 和 `_installers.npm_prefix.NpmPrefixInstaller`
  （两个具体策略)、`errors.classify_error`（安装异常翻译)。

## 设计决策

- **每插件一把 `asyncio.Lock`,忙时直接拒绝而不是排队**：两个 pip/npm 子
  进程同时写同一个 target 目录会产生损坏的安装（半写的 dist-info、npm
  的 `node_modules` 并发写锁冲突)。排队看起来更"友好",但会让用户以为
  点了两次安装、界面卡住——直接拒绝 + 一句"已经在装了"比隐藏的排队更诚实。
- **`list_plugins` 返回的 `busy` 字段读同一个 `self._busy` set,不读锁本身
  是否 locked**：因为锁在整个 `async with lock:` 块内都是 locked 状态,
  而 `busy` 语义上specifically 指"这个插件正在走安装流程"——两者当前恰好
  重合,但拆开写是为了以后如果 uninstall 也要占用同一把锁时,busy 语义不会
  被悄悄改变。
- **`install` 是 async generator,不是"跑完再返回结果"**：Settings UI 需要
  实时看到 `pip install` / `npm install` 的输出行（这两步各自能跑几十秒
  甚至几分钟),不能等到全部装完才给用户任何反馈。

## Gotcha / 边界情况

- **触发**：调用方拿到 `service.install(plugin_id)` 返回的 async generator
  后没有立即开始迭代（`async for` / `__anext__`）→ **症状**：`busy` 状态
  和锁都还没生效,`list_plugins()` 仍然显示 `busy=False` → **根因**：
  Python async generator 函数体在第一次 `__anext__` 之前完全不执行（惰性
  求值),`async with lock:` 这一行也不例外——防重入保护只在"真正开始消费
  这个生成器"之后才生效,调用方必须马上迭代,不能先存着生成器对象再决定要
  不要消费。
- **触发**：`PluginSpec.user_version_source` 指向的那个 component kind
  在 `spec.components` 里不存在（拼错 kind 或 registry 配置错误）→
  **症状**：`_status` 里 `version_state` 为 `None`,`target_version` 返回
  空字符串,`version` 恒为 `None`,不会抛异常 → **根因**：`_status` 用
  `next(..., None)` 兜底,故意 fail-soft 而不是让一个配置疏漏直接搞挂整个
  状态列表接口——这是当前唯二两个 spec（Claude Code / Codex CLI)手工核对
  过的地方,新增插件时必须保证 `user_version_source` 对应的 kind 确实在
  `components` 里出现过。

## 相关约束

- 铁律 #21 —— 本文件是 routes 唯一应该 import 的入口,禁止 routes 直接碰
  `_installers/`

## 2026-08-28 补 — uninstall 也走共享锁

uninstall 原本裸奔;现改成与 install **共用同一把 per-plugin 锁 + busy 集**:锁被占(安装/卸载进行中)则抛 [[errors]] 的 `PluginBusyError`,不排队。install/uninstall 因此对同一插件互斥,杜绝"装到一半点卸载"两个包管理器/rm 抢同一目录。路由把 `PluginBusyError` 映射成 409。

## 2026-08-28 补(auto-review C2/I4/N1) — 按 kind 算 target;终态 busy=False

新增 `_target_for(spec,component)`(pip→plugin_pyenv(id),npm→node_prefix)与 `_detect`,install/uninstall/detect 都传 target。install **终态 status 在清 busy 之后计算**(原来在 finally 前算,`busy` 恒 True——类型承诺了却语义错)。install 重构成 try 里只跑各 component、except 记 ok/error、finally 清 busy、循环外才算 status+yield 终态(客户端断连的 GeneratorExit 不进 except、经 finally 释放锁与 busy 后向上传播,终态不 yield)。list_plugins 由路由层 run_in_threadpool 调用(见 [[routes]]),故仍同步。

## 2026-08-28 补(auto-review C2 第二轮) — install 终态 _status 挪出事件循环

上轮只在路由层两处 run_in_threadpool,漏了 install() 生成器内部的终态 `_status`(它会 fork `claude --version`),仍在 StreamingResponse body 的事件循环上跑。改成 `await asyncio.to_thread(self._status, spec)`。

## 2026-09-07 — 登录标记来自 PluginSpec.login_marker（B6）

_LOGIN_MARKERS 名字表删除；_logged_in 读 spec.login_marker（源自 FrameworkMeta.login_marker）。

## 2026-09-07 — specs 首次使用时派生（B7）

路由模块在导入期就建单例（锁/busy 集是实例态），而框架注册表的槽由 builtin.turn 在 boot 时声明，导入期还不存在；specs 改为首次访问时从注册表派生，请求一定在 boot 之后。
