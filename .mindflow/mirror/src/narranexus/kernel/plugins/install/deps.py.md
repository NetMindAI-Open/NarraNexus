---
code_file: src/narranexus/kernel/plugins/install/deps.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2c）— 插件 pip 依赖装进私有目录

`uv pip install --target`（有 uv）否则 `python -m pip install --target`，一律 `--only-binary=:all:`：没有 sdist
构建就没有 `setup.py` 执行（spec §9.3「不执行任何生命周期脚本」）；索引限 PyPI + manifest 声明的 https 索引；
120s 上限；requirement 里禁止选项与空格。子进程 runner 可注入，测试不碰网络与包管理器。

## 2026-09-07 — installer subprocess env is an allow-list

subprocess_env() passes PATH/HOME/SYSTEMROOT/APPDATA/TEMP, the proxy and CA-bundle variables and UV_*/PIP_* through, and never PYTHONPATH/VIRTUAL_ENV/PIP_TARGET (the reason the env was cleared: pip must not install into the host environment). A fully empty env made every install fail behind a corporate proxy and could not even start a process on Windows.
