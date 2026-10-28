---
code_file: src/narranexus/platform/marketplace/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 从「零 re-export」改为四个惰性公开名（批 6c，A2-1）

原本的规矩是「不 re-export，消费者显式 import 服务模块」，而 docstring 自己承认
「若干外部消费者仍在直接 import `_skill_marketplace_impl`，属历史欠债」。
批 6b 之后这批消费者变成了 `builtin.skills` / `builtin.teams` 两个独立 wheel，
欠债就从「不好看」升级成「插件在 import 另一个包的私有模块」，被新的 import-linter
契约挡住。四个名字（`ArtifactStore` / `get_template_store` / `InstallPipeline` /
`get_secret_box`）因此成为公开门面——它们正是那两个插件真正需要的 seam，
而服务模块并不暴露它们。

**惰性**（PEP 562）而不是 eager import：`install_pipeline` 会拖起整个 marketplace registry，
`secret_box` 会碰密钥文件；为了拿到 `skill_marketplace_service` 而 import 本包的人
不该为这两样买单，同包内的 import 顺序也不必被打乱。
`TYPE_CHECKING` 分支让 pyright 仍能看到真类型。
平台**内部**消费者继续直接 import `_skill_marketplace_impl`，那是包内私有的正常用法。

# marketplace/__init__.py — marketplace domain subpackage anchor

## Why it exists

The skill/team marketplace shipped (PR #143) with its two service files and
`_skill_marketplace_impl/` lying loose at the package root, and the vendored
first-party skills in a repo-root `marketplace_skills/` directory. That broke
the repo's domain-subpackage convention (`artifact/`, `memory/`,
`message_bus/` — a `<domain>/` package holding `*_service.py` + `_*_impl/`).
This package regroups the whole marketplace feature area:
services + private impl + `resources/marketplace_skills/` (the vendored
skills now travel with the package instead of relying on a repo-root path).

## Design decisions

- **No re-exports.** All existing consumers already import the service
  modules directly (`narranexus.platform.marketplace.skill_marketplace_service`);
  keeping the `__init__` inert made the regrouping a pure move with zero
  import-style churn. If a public seam is wanted later, follow
  `artifact/__init__.py`'s re-export pattern.
- **`_skill_marketplace_impl/` is not yet a sealed boundary.** Six call
  sites outside the package (backend/routes/skills.py, skill_module,
  skill_sync_service, both seeds) import the private impl directly —
  pre-existing debt this move did not introduce or worsen. Renaming
  impl symbols WILL break external callers until they converge on the
  service seam (todo recorded).
- **Seed path** lives in `marketplace/_skill_marketplace_seed.py` (moved
  here from `repository/` 2026-07-24 — it is marketplace bootstrap, and
  repository/ is the bottom layer), which resolves `resources/
  marketplace_skills/` next to itself (env-overridable via
  `MARKETPLACE_SKILLS_DIR`).
