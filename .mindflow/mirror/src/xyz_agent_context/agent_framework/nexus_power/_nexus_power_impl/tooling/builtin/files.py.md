---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/builtin/files.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 文件工具 path 必填守卫

read/write/edit 进门先 _require_path:path 缺失明确报「missing required
argument: path」。_resolve 的 workspace 根缺省只属于目录工具(ls/glob/grep)。
dispatcher 已在派发前校验 required,但摸文件系统的工具不得假设别人查过
(与 glob 爬出工作区同一课,2026-07-29 review)。

# tooling/builtin/files — 文件六件套

spec+handler 同文件(单一事实源)。edit 唯一命中语义(CC 同款);grep 纯 python 带尺寸/数量帽与目录跳过;路径已由 confinement 层裁决。
