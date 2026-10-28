---
code_file: src/narranexus/platform/agent_framework/plugin_paths.py
last_verified: 2026-09-07
stub: false
---
# plugin_paths.py — 可选框架插件的落点与"是否已装"的单一真值

## 2026-09-03（批 2b.1）— `plugin_home()` 委托内核 `kernel/plugins/paths.py`

布局的唯一真源搬到内核（用户插件目录、registry.json、LKG、启动标记都在同一棵树），本模块只保留框架安装器
专用的 `nodejs/pyenv` 子路径与 `framework_installed/activate_pyenv`。`ENV_PLUGIN_HOME` 同一个名字。

## 为什么存在

本地版（`bash run.sh` + 桌面 DMG）不再自带 Claude Code / Codex 两个重家伙，
改由用户在 设置 → 插件 里按需安装。两者都装进**同一棵用户可写目录树**
`~/.narranexus/plugins/`（绝不写进只读、已公证的 `.app`）：

```
~/.narranexus/plugins/
├── nodejs/  node_modules/.bin/claude   ← npm --prefix，Claude CLI 2.1.220
└── pyenv/   claude_code/ codex_cli/  ← pip --target,每插件一个子目录(见下 2026-08-28 节)
```

本模块是**纯**的：只做路径拼装 + 文件系统/`find_spec` 探测。不含版本 pin
（pin 跟安装器走）、不 import `backend`（铁律 #21）。三个消费方必须对这套
布局达成一致：惰性 driver 工厂（`agent_framework/__init__`）、Claude 二进制
解析器（`adapters/claude/cli_binary`）、后端安装/状态路由。

## 可用性 ≠ import

`framework_installed(name)` 回答"这个框架的代码在不在"而**不 import 它**：
查插件 `pyenv` 目录（文件系统）**或** 在普通环境里 `find_spec`（这样云端按
常规方式预装 SDK 时也报"已装"）。`nexus_power` 内置恒真；未知名恒假。

`_present_in_base` 单独抽成函数，是为了让测试能强制"未命中普通环境"分支，
只验 pyenv 那条路径。`find_spec` 只定位不执行，故后端进程调用它不会把 ~186MB
的 SDK 拉进内存。

## activate_pyenv 的接缝语义

本地/桌面模式 driver 在后端进程内 in-process 运行（`AGENT_EXECUTOR_URL` 未设），
所以 `import claude_agent_sdk` / `openai_codex` 发生在后端进程。`activate_pyenv()`
把 `pyenv` **append** 进 `sys.path`：

- **append 而非 insert(0)**：base 赢共享依赖（anthropic/openai 等按 base 版本），
  pyenv 只补插件独有的 wheel，避免版本影子。
- 幂等；pyenv 不存在时 no-op。

惰性工厂在 `import` SDK **之前**调它 → 本地模式**装完免重启**即可用。云端走
remote executor + 镜像预装，本模块不参与该路径。

## 测试

`tests/agent_framework/test_plugin_paths.py`：env 覆盖、路径拼装、nexus_power
恒真、未知名恒假、pyenv/base 两条命中路径、append 语义与幂等。删掉目标逻辑
任一条即变红。

## 2026-08-28 补(auto-review I4) — pyenv 改每插件子目录

pip 树从扁平 `pyenv/<package>` 改成**每插件一个子目录** `pyenv/<plugin_id>/`：卸载=rmtree 该子目录,带走整个依赖闭包(否则 openai_codex_cli_bin ~90MB + 共享依赖会残留,打脸'卸载干净';两插件也不再争同一份共享依赖版本)。新增 `plugin_pyenv(plugin_id)`;`_present_in_pyenv(name,package)` 现两参、查 `pyenv/<name>/<package>`;`activate_pyenv` 遍历 append 每个存在的子目录(不再 append pyenv 根)。npm 仍单一共享 `nodejs` prefix(只 claude 用,按包 uninstall)。

## 2026-09-07 — dead import removed

import os had no remaining use.

## 2026-09-07 — 不再持有框架名（B6）

_FRAMEWORK_PACKAGE/PLUGIN_FRAMEWORKS/framework_installed 删除；只留纯文件系统/importlib 探针 package_installed(framework, package)。哪个包证明哪个框架已装是注册表（FrameworkMeta.install.probe_package）的知识，由 loop.driver.framework_installed 读取。
