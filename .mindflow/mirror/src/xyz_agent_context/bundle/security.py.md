---
code_file: src/xyz_agent_context/bundle/security.py
last_verified: 2026-05-08
stub: false
---

# security.py — bundle zip safety helpers (PRD §8.7, §8.12.9)

## 为什么存在

外部 `.nxbundle` 是用户上传的不信任输入，天然有 zip-bomb / path-traversal / symlink 三类攻击面。把所有相关 guards 集中在 security.py 让审计点单一。

同文件还放了**敏感路径 / 体积模式黑名单**，给 builder 在打包 workspace 时 default-skip 用。

## 上下游关系

- **被谁用**：
  - `bundle/importer.py` — `extract_zip_safely`
  - `bundle/builder.py` — `is_sensitive_path`, `is_volume_path`, `scan_zip_for_sensitive`
  - `bundle/skill_backup.py` — `bytes_sha256`, `file_sha256`
- **依赖谁**：stdlib only (`zipfile`, `hashlib`, `pathlib`, `fnmatch`)

## 设计决策

### Cap 数字

- 单 bundle ≤ 500MB（`MAX_BUNDLE_BYTES`）
- 解压总量 ≤ 2GB（`MAX_DECOMPRESSED_BYTES`）

任一超出立即拒。

### 黑名单两层

`SENSITIVE_*` 系列（`.env`, `.aws/`, `*.key`, `id_rsa*` 等）= 默认排除 + 警告。
`VOLUME_PATH_PATTERNS`（`node_modules/`, `.cache/` 等）= 默认排除但不警告。

清单**固化在代码里**，议题 6.4.b 决策不开放运行时自定义。

### Stream extract

`extract_zip_safely` 用 64KB chunk 流式写盘，不 load 整文件到内存。

## Gotcha

- symlink 检测看 `external_attr` 高位的 unix mode bits — Windows 创建的 zip 没这个信息，最坏情况是漏一个 symlink。Linux/macOS 创建的 zip 都正常。
- `scan_zip_for_sensitive` 只看路径不看内容（不扫文件内的 `sk-...` 字符串）。这是议题 6.5 决策（不做内容扫）。
