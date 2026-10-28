---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/marketplace_service.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

# team_marketplace_service.py

## 2026-09-03（批 2a.5）— `content.bundles` 插件模板

`_plugin_templates()` 把插件随包的 `.nxbundle` 变成模板字典（`source="plugin"`、`author=插件 id`、
`bundle_sha256=spec.sha256`、`store_key=""`）：`list_templates` 在注册表/云端列表后追加（同 id 已存在则不加），
`expected_sha256` / `resolve_bundle` 先看插件模板（注册表宿主上若同 id 已发布则以注册表为准），本地文件直接
复制到工作目录，之后走同一 preflight → confirm。

Service protocol layer — the only entry point for the Team Marketplace routes.
Encapsulates the deployment split (spec §5, decision 1): browse/detail read
the DB catalog on the registry host, proxy the cloud API on desktop; INSTALL
always runs the LOCAL importer against the LOCAL DB (fork lands in this
backend's own agents/teams), and only the "get the .nxbundle bytes" step
diverges — `resolve_bundle` reads the store directly on the registry host, or
HTTP-downloads the cloud `/download` endpoint on a desktop client. This is the
exact Local/Remote pattern from the skill marketplace, applied to bundles.

## Key methods
- `install_preflight`: resolve bytes → verify sha256 (tamper abort) →
  importer.preflight → standard preflight payload (frontend confirms via the
  existing /api/bundle/import/confirm). Bumps downloads on the registry host.
- `get_bundle_bytes`: registry-host-only, backs the /download endpoint that
  desktop clients pull through.
- `publish`: sha256 the bundle → `store_key_for(id, sha)` → put in template
  store → save catalog row. Blob lives in get_template_store() (own prefix,
  separate from skills).

Batch 6b.3: moved from `platform/marketplace/team_marketplace_service.py`; `_is_registry_host` delegates to the platform's `is_registry_host()`.
