---
code_file: src/xyz_agent_context/skill_marketplace_service.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — 二轮 review 修复:get_detail 透传 version

`get_detail(skill_id, version=None)` 把 version 一路透传给 registry / remote,
配合按版本号安装的 hash 校验(见 registry 侧同日条目)。纯透传,无新逻辑。

## 2026-07-22 — review 修复:installed 标注按 catalog id

_annotate_installed / check_updates 的 installed 键从 skill.name 改为 `meta.skill_id or skill.name`,配合 install 侧目录名锁 catalog id,修复 name≠id 时的永不安装显示。


## 2026-07-21 — Default Skills(stage 9)

`list_defaults()`(cloud=catalog.list_defaults;desktop=GET /defaults)与
`install_defaults(agent_id, user_id)`:逐个装 is_default 技能,单个失败只记
录不抛,registry 不可达返回 `registry_unreachable=True` 的空摘要——默认技能
安装绝不能破坏 agent 创建。


## 2026-07-21 — SKILL_MARKETPLACE_LOCAL_REGISTRY 开关

`_is_registry_host()` 新增 env 覆盖:`SKILL_MARKETPLACE_LOCAL_REGISTRY=1`
时本地/桌面实例自己当 registry(服务并浏览自己的 catalog),用于 dev、离线
演示、以及 cloud marketplace 上线前的过渡。默认仍是 cloud=registry、
local=proxy 到 NARRANEXUS_MARKETPLACE_URL。


# skill_marketplace_service.py

Service protocol layer (public façade) for the Skill Marketplace — the ONLY
entry point backend routes and MCP tools use. Hides the deployment split in
exactly one place: `_is_registry_host()` (cloud → in-process DB registry;
desktop → RemoteMarketplaceSource against the cloud API). Installs always
run locally through InstallPipeline against this host's workspace,
whichever side the catalog lives on.

## Design decisions

- **Mode decision is centralized here** — `install()` passes an explicit
  `marketplace_source` to the pipeline instead of letting the pipeline
  re-derive the mode (was a real bug caught by tests: two call sites
  deciding independently).
- `_annotate_installed` reads the workspace filesystem (SkillModule +
  `.skill_meta.json` versions), consistent with disk-is-truth; it does NOT
  consult the audit DB.
- `check_updates` (agent-scoped) builds the installed list from disk, then
  asks the registry (local or remote batch endpoint).
- Cheap to construct — one instance per request/tool call, no caching.
