---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/registry.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — 二轮 review 修复:hash-from-catalog 打断了按版本安装

上一条把 hash 只从 catalog detail 取时,`get_detail` 永远返回 latest,导致
「按旧版本号安装」下载到旧包却拿 latest 的 hash 校验 → 必然 tamper 报错。
修:`RegistryService.get_detail(skill_id, version=None)` 与
`RemoteMarketplaceSource.get_detail/resolve_and_download` 全程透传 version
(有则 `get_version`,无则 `get_latest`;remote 端把 version 作为查询参数带上),
校验的 entry 就是请求的那个版本。删掉了 remote 端 `entry_data|version` 覆盖行。

## 2026-07-22 — review 修复:hash 从 catalog 取 + client 不泄漏

RemoteMarketplaceSource:校验 hash 只从 catalog detail(entry_data)取,不再用下载响应自证的 X-Package-Hash 头;`_http()` 改为 asynccontextmanager,内部创建的 client 用完即关(每 service 调用一个,原先泄漏)。


## 2026-07-21 — manifest "default" -> catalog is_default(stage 9)

publish 时透传;RemoteMarketplaceSource 增加 `list_defaults()`(GET
/api/marketplace/skills/defaults,公开读)。


# registry.py

Marketplace registry: publish pipeline, catalog queries, and the two install
sources. The DB catalog is the ONLY directory truth — the v1.0 design's S3
`registry-index.json` was deliberately dropped (one directory source = no
index/DB drift, kills old "风险三").

## Publish flow

extract (reuses SkillModule.extract_skill_package with a dummy `__registry__`
instance — same zip-safety checks) → manifest.json authoritative, minimal
manifest synthesized from SKILL.md frontmatter when absent, version REQUIRED
→ scan gate (`PublishRejectedError` carries the report for the 422 body) →
artifact + manifest uploaded at `{id}/{version}/…` → catalog + scan rows.
Artifacts are immutable: republishing the same id@version updates catalog
metadata but the store object is overwritten with identical content only if
the hash matches what's recorded (enforced socially — hash check happens at
install).

## Install sources

`LocalMarketplaceSource` (cloud: repo + store in-process; bumps downloads on
record_install) vs `RemoteMarketplaceSource` (desktop: cloud HTTP API;
download counter is bumped server-side by /download, record_install no-ops).
Both expose `resolve_and_download` for the InstallPipeline. The MODE DECISION
lives in `skill_marketplace_service.py`, not here.

`RemoteMarketplaceSource` trusts `X-Skill-Version` / `X-Package-Hash`
response headers so the hash verify in the pipeline checks against what the
registry claims, not what the body happens to be.
