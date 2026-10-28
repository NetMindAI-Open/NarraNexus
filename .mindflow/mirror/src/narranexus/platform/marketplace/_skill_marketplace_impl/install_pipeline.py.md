---
code_file: src/narranexus/platform/marketplace/_skill_marketplace_impl/install_pipeline.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 安装侧与删除侧用同一道名字栅栏

落盘目录名在 `sanitize_filename` 之后再过 `safe_id_segment`：删除/禁用/启用侧（skill_module）
拒绝点开头的名字，若安装侧放行，点开头的 skill 就会"装得进、删不掉"。`.disabled` 是
skills_dir 下的容器目录、不是 skill 名，不经过这里。

## 2026-09-10（PR #388 round-2 M2）— 逐 skill 失败文本带类名并封顶

`_failure_text(exc)` = `"<Class>: <msg>"` 压平换行后截到 `INSTALL_ERROR_MAX_CHARS=500`，给 `InstallResult.error`
与 warning 日志。`except Exception` 放宽后 `str(KeyError("env"))` 只是 `'env'`、空 RuntimeError 是空串——agent 通过
`skill_install` 看到的必须能判断重试/放弃。不做 URL/token 掩码：这条路径唯一的 URL 是用户自己给的仓库地址。
测试 `test_per_skill_failure_text_names_the_class_and_is_capped`。

## 2026-09-10（PR #388 review M4）— 逐 skill 隔离覆盖任何异常

安装循环改为 `except Exception`：某个根内部的 RuntimeError/KeyError 也只标记该根 failed，兄弟结果保留
（磁盘是真相，不回滚）。仓级错误（fetch / 布局 / 超上限）在循环外仍直接 raise → 路由 400。
测试 `test_github_multi_skill_repo_isolates_an_unexpected_exception`。

## 2026-09-10（复审 M2）— 逐 skill 隔离也覆盖 OSError

安装循环 `except (ValueError, OSError)`：磁盘满/权限错误落在某个根时同样只标记该根 failed、已装的 sibling
照常上报，不再整体抛出复刻"部分落盘 + 谎报整体失败"。仍不放宽到 `except Exception`。

## 2026-09-09 — `install_from_github` 返回 `List[InstallResult]`，逐 skill 隔离失败（GitHub #95，复审 C3）

`fetch_github_repo` 返回一个仓里全部 skill 根（根 / `<name>/` / `skills/<name>/` 布局，上限
`SkillModule.MAX_SKILLS_PER_REPO`），pipeline 先按**同仓依赖**排序（`_order_roots_by_dependency`：
manifest `dependencies` 指向同仓兄弟的先装，其余保持名序；仓外依赖仍交 `_check_dependencies`），再对每个
根独立跑 `_install_staged`。任何一个根的 `ValueError`（扫描门 rejected / 缺依赖 / 版本不兼容 / manifest 坏）
被捕获为 `InstallResult(status="failed", skill=None, skill_name=…, error=…)`，其它根照常安装——"安全拒绝了 X"
不再把"Y、Z 已经落盘并进了审计表"伪装成整体失败。`InstallResult.ok` 属性给消费方分流。单 skill 仓就是长度 1；
zip / marketplace 入口仍是单结果且仍抛异常。clone 失败 / 没有任何 SKILL.md 不是 per-skill 失败，仍抛 ValueError。
调用方（routes install、MCP skill_install、SkillMarketplaceService.install_from_url）分别汇报成功与失败。

## 2026-08-04 — 装/卸技能后刷新同伴发现行

`_audit` 末尾调 [[agent_discovery_sync]]。技能就是能力，而能力正是同伴搜索的
匹配面（`bus_search_agents` 匹配 `capabilities LIKE ?`）——装完不刷新，名录要等到
这个 agent 下一轮才反映它新会的事（P1 段02 目标 1）。仍在原来的 try 里：磁盘是
真相，元数据写失败不许回滚文件操作。

## 2026-07-22 — review 修复:目录名锁 catalog id

`_install_staged` 加 `catalog_skill_id`:marketplace 安装用 catalog id 作目录名 + 写进 .skill_meta.json `skill_id`,避免 SKILL.md name≠catalog id 时永远显示未安装/依赖不满足。


# install_pipeline.py

The unified 7-step install/uninstall engine (spec §5). Every entrance — UI
zip/GitHub, URL, agent MCP tools, future marketplace source — converges here
so the scan gate, conflict/config migration, meta hash fields, audit trail
and auto-archive can never be skipped by taking a different door.

## Step order (and why)

Scan runs on the STAGED package (temp dir) before dependencies/compat/conflict
— a rejected package must never touch `skills/`, and the old version of a
skill must survive a rejected upgrade attempt. Same-version conflict returns
`already_installed` without touching disk. Replace captures the old
`env_config` first and merges it back after `install_from_dir` (which rmtree's
the target, destroying the old meta) — that is the "same-key config
auto-migration" from Phase 2 comment #3.

## Design decisions

- **Wraps SkillModule's public primitives** (`extract_skill_package` /
  `fetch_github_repo` / `install_from_dir` / `merge_skill_meta` /
  `read_skill_meta` / `parse_skill_package`) — added in the same commit
  precisely so this file needs no private access.
- **Disk is truth**: `_audit` and `_backup` swallow + log failures; a DB
  outage cannot fail or roll back a filesystem install. The reconciler
  (stage ⑥) heals the audit table later.
- `hash` = sha256 of the source zip (absent for github clones);
  `content_hash` = deterministic dir hash excluding `.skill_meta.json`
  (stable across meta rewrites — the reconciler's "modified" detector).
- Conflict lookup uses `sanitize_filename(incoming.name)` because
  `install_from_dir` lands the skill under the sanitized name.
- Compatibility check no-ops when `importlib.metadata` can't resolve the
  app version (editable installs) — availability over strictness for MVP.

## Gotcha

- `install_from_zip` re-extracts nothing: staging IS the only extraction;
  `install_from_dir` moves the staged tree. Both entrances clean their temp
  dir in `finally` (move makes `exists()` False on success).

## 2026-07-21 — marketplace 源接入(stage 4)

新增 `install_from_marketplace(skill_id, version, marketplace_source)`:
下载 → **hash 实时校验**(与 catalog 记录不符即中止,防篡改)→ 依赖递归安装
(深度 ≤3、环检测)→ `_install_staged(skip_scan=True)`(发布时已扫过,装机
只验 hash 不重扫)→ source.record_install 计数。mode 决策(Local vs Remote
source)由 skill_marketplace_service 统一传入;`_default_marketplace_source`
仅作直接调用时的兜底。

## 2026-09-04 · services + host hooks (batch 3c.6)

`skill_module` is typed as the `SkillWorkspace` Protocol and defaults to the `skills.workspaces` service.

Batch 6d: `_current_app_version()` reads `narranexus.kernel.plugins.compat.host_version()` (the engine package is `narranexus`; the old name is its fallback).
