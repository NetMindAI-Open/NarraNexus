---
code_file: src/narranexus/platform/bundle/security.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 — skill 归档准入校验收敛到这里

新增 `validate_skill_archive_bytes()` / `validate_skill_archive_path()`（共用
私有的 `_validate_skill_archive`），以及两个上限常量
`MAX_SKILL_ARCHIVE_ENTRIES=500` / `MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES=100MB`。

**为什么要收敛**：`skill_archives` 这张表有**两个写入口**——上传路由
（[[bundle.py]]）和工作区本地 zip 注册（[[skill_backup.py]] 的
`archive_local_zip`）——它们对"什么算一份合法归档"的判断本来是各写各的、还不
一致（后者要求含 SKILL.md，前者什么都不查）。现在两处都调同一个函数。

**上限本身住在 [[file_safety.py]]**（2026-08-18 三审移过去的）：这道准入闸和
真正解压的安装器 `skill_module._extract_zip_safely` 必须一致，否则"门口收下、
安装时才拒"——正是这对函数存在的目的所反对的形状。原先两处各写一份字面量、
只靠注释宣称相等。`file_safety` 是两边**本来就都依赖**的模块，放那儿不引入
新的依赖方向。

⚠️ 读法是 `from ... import file_safety as _file_safety` + **调用时**取属性，
不是 `from ... import MAX_...`。后者在 import 时把值绑死，等于又复制了一份、
换个地方腐烂——这个坑是被"patch 了源头、闸口却当没看见"的测试抓出来的。

**仍然故意不复用 `MAX_DECOMPRESSED_BYTES`（2GB）**：那个约束的是整份
`.nxbundle`，和单个 skill 的归档不是一个量级，保持两个名字。

**只读中央目录，绝不解压**（这条是硬约束，别"优化"掉）：

- `testzip()` 会把每个成员完整解压一遍校验 CRC。deflate 压缩比可达 ~1030:1，
  实测 199 KB 包解压出 200 MB；上传上限 50 MB ⇒ ~50 GB。路由是 `async def`
  且没有 `to_thread`，**事件循环会被独占**，任何登录用户一次上传就能拖停全站
  （铁律 #16：不要让我们自己的 bug 成为打断源）。
- 判据够用的理由：下游那个原本 500 的消费者 `scan_zip_for_sensitive` 也只调
  `infolist()`。

**明写的取舍**：不验 CRC，所以"中央目录完好 + 数据段损坏"的包会被放行，那种
失败在导入侧按 skill 单独兜住。`file_size` 是包自报的，恶意包可以谎报——这只
是便宜的第一道闸，真正按实际写入量计数的是 `extract_zip_safely`。想加 CRC 校
验，前提是**先过体积闸 + 挪进 `to_thread` + 接住 `testzip()` 的返回值**
（它遇到 CRC 错误 `return zinfo.filename`，**不抛异常**）。

加密包（general-purpose flag bit 0）也在这里挡掉：flag 从元数据就能读到，能
立刻给"这个包加密了，请传未加密的"，而不是让它在导入侧才失败。

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
