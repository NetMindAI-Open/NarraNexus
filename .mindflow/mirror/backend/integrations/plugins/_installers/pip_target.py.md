---
code_file: backend/integrations/plugins/_installers/pip_target.py
last_verified: 2026-08-28
stub: false
---

# pip_target.py — `sys.executable -m pip install --target <pyenv>` 策略

## 为什么存在

Claude Code 和 Codex CLI 都需要往用户可写的 `pyenv` 目录装一个 pip wheel
（`claude-agent-sdk` / `openai-codex`)。这个文件是唯一知道"怎么装一个 pip
wheel到隔离目录、怎么从磁盘反查它的版本"的地方。

## 上下游关系

- **被谁用**：`service.PluginService.__init__` 实例化一个
  `PipTargetInstaller` 挂在 `self._installers["pip"]`;`registry.py` 里
  两个插件的 pip `InstallComponent` 最终都由它处理。
- **依赖谁**：`narranexus.platform.agent_framework.plugin_paths.pyenv_dir`
  （落点单一真值,不自己拼路径)、`_installers.base` 的 `PluginInstaller`
  /`InstalledState`/`stream_subprocess`。

## 设计决策

- 用 `sys.executable -m pip` 而**不是** PATH 上的裸 `pip` 或 `uv pip`——
  装进 `pyenv_dir()` 的 wheel 之后要被同一个后端进程
  `sys.path.append`（`plugin_paths.activate_pyenv`)再 import,ABI 必须和
  当前解释器一致。裸 `pip`/`uv` 可能解析到系统里另一个 Python 版本,装出
  一个 import 得进来但 C 扩展边界会崩的 wheel——这类 bug 极难复现,宁可
  牺牲"用户系统已有更快的 uv"这点效率也要保证 ABI 对齐。
- `detect` 靠 glob `{dir_name}-*.dist-info` + `importlib.metadata.
  PathDistribution` 读版本,**不**尝试真的 `import` 这个包——探测已装状态
  的路径必须是纯文件系统操作,不能把 ~186MB 的 SDK 拉进后端进程内存,这条
  和 `plugin_paths._present_in_base` 的设计理由完全一致（同一份 intent,
  两个文件各自需要重申一次)。
- 卸载时同时删包目录**和** dist-info 目录——只删其中一个会让下次
  `detect` 出现"包目录在但读不到版本"或"能读到版本但包目录已经不能
  import"的自相矛盾状态。

## Gotcha / 边界情况

- **触发**：`registry.py` 给出的 requirement 用了 `>=`/`~=` 等范围操作符
  而不是精确的 `==` → **症状**：`_parse_requirement` 抛
  `ValueError`（unsupported pip requirement) → **根因**：本安装器只认
  精确 pin——`registry.py` 是唯一被信任会写出精确版本号的调用方,这个校验
  是刻意的 fail-closed,防止未来有人在 registry 里手滑写了范围约束却没人
  发现"用户装到的版本会漂"。

## 相关约束

- 详见 `plugin_paths.py.md` —— "可用性 ≠ import" 一节是本文件 `detect`
  纯文件系统探测这一设计的直接来源。

## 2026-08-28 补 — ensurepip 兜底(uv venv 无 pip)

真机实测发现:`bash run.sh` 的 uv 管理 venv **不带 pip**,`sys.executable -m pip` 直接挂,安装全败。修:install 前用 `importlib.util.find_spec("pip")`(我们就是 sys.executable,故直接反映目标解释器)判定,缺则先 `python -m ensurepip --default-pip` 引导再 pip install。DMG 打包 python 自带 pip→跳过。单一路径两模式通(铁律 #7),不依赖 uv/pip 在 PATH。真机验证:codex 装进临时 pyenv(openai_codex+codex_cli_bin 90MB)ok、卸载干净。

## 2026-08-28 补(auto-review I4) — 装/卸带 target;卸载 rmtree 整个子目录

install/detect/uninstall 接收 `target`(=`plugin_pyenv(plugin_id)`)。uninstall 从'按包名 glob 删 dist-info'改成 `rmtree(target)` 整个插件子目录——一次带走全部依赖(codex_cli_bin ~90MB 等),彻底干净、幂等。detect 在 target 内查包目录与 dist-info 版本。

## 2026-08-28 补(auto-review C2 第二轮) — uninstall 的 rmtree 挪出事件循环

I4 把 rmtree 范围扩到整棵依赖闭包(claude ~190MB/数万文件),同步 rmtree 在 async uninstall 里会冻事件循环(铁律#16)。改 `await asyncio.to_thread(shutil.rmtree, target, True)`。
