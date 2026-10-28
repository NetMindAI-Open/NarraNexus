---
code_file: backend/integrations/plugins/_installers/base.py
last_verified: 2026-08-28
stub: false
---

# base.py — PluginInstaller 策略契约 + 共享子进程流式执行

## 为什么存在

`PipTargetInstaller` 和 `NpmPrefixInstaller` 是两个完全独立的策略（一个走
pip、一个走 npm),但它们对"跑一个包管理器子进程、把每行输出当进度事件"
这件事的需求逐字相同。把这段子进程管道逻辑（`stream_subprocess`)和策略
契约（`PluginInstaller` ABC)放进同一个文件,是这两个策略之间唯一被允许
共享的耦合点——除此之外它们互不知道对方存在。

## 上下游关系

- **被谁用**：`pip_target.py` 和 `npm_prefix.py` 都从这里 import
  `PluginInstaller`（作为基类)、`InstalledState`（作为 `detect` 的返回
  类型)、`stream_subprocess`（复用子进程管道)。`errors.py` 从这里 import
  `PluginInstallSubprocessError` 去做失败分类。`service.py` 只 import
  `PluginInstaller` 的类型（给 `self._installers: dict[str, PluginInstaller]`
  标注类型)。
- **依赖谁**：只依赖标准库 `asyncio`/`abc`/`dataclasses`。

## 设计决策

- `stream_subprocess` 调用 `asyncio.create_subprocess_exec` 时**不传
  `cwd`/`env` 覆盖**——两个具体安装器都不需要改变工作目录或环境变量（pip
  用 `--target`、npm 用 `--prefix` 显式指定落点,不依赖 cwd),刻意不做
  参数化以保持这个共享助手足够小。
- 失败时把**捕获到的全部输出行**（不是最后一行、不是只有 stderr)塞进
  `PluginInstallSubprocessError.output`——`errors.classify_error` 需要在
  全文里找关键词（"registry timeout"可能出现在中间某一行,"EACCES" 可能
  出现在第一行),只留最后几行会漏判。

## Gotcha / 边界情况

- **触发**：测试里 monkeypatch
  `backend.integrations.plugins._installers.base.asyncio.
  create_subprocess_exec` 时,以为是在替换"这个模块自己的函数" → **症状**：
  其实是直接改写了 Python 进程里唯一那个 `asyncio` 模块对象的属性,对所有
  import 了 `asyncio` 的模块全局生效 → **根因**：`base.py` 顶部
  `import asyncio` 只是拿到同一个模块对象的引用,`base.asyncio` 和真正的
  `asyncio` 模块是同一个对象；测试结束后 pytest-monkeypatch 的 fixture
  会自动撤销这个 patch,但如果哪天改用手写 `unittest.mock.patch` 且忘记
  加 `autospec`/清理,这个全局副作用会泄漏到其他测试。

## 相关约束

- 无独立铁律,但这是 `_installers/` 子包内**唯一**允许存在的共享代码——
  新增第三个安装策略（比如未来的 `uv_target.py`)如果还需要更多共享逻辑,
  应该先问"这真的是所有策略通用的,还是只是巧合相似"。

## 2026-08-28 补(auto-review I3/N5) — 子进程收尸 + 显式 raise

PluginInstaller 三方法加 `target: Path` 参数(由 service 按 kind 计算:pip→每插件 pyenv 子目录,npm→共享 node prefix),让 pip 每插件独立目录成为可能。`stream_subprocess` 包 try/finally:任何退出路径(正常/异常/客户端断连的 GeneratorExit/CancelledError)都 `process.kill()+wait()` 收尸——否则子进程被孤儿化,在 service 释放 per-plugin 锁后还在写 target 目录,正是锁要防的竞态(工程教训#2)。`assert process.stdout is not None` 改成显式 raise(`-O` 下 assert 会被剥掉)。
