---
code_file: src/narranexus/cli/scaffold.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2e）— 从 `templates/<kind>/` 拼一个插件

manifest 片段深合并（列表并集、字典合并、标量后者赢）；各 kind 的 `backend/__init__.py` 片段拼接成一个；
其余文件复制并替换 `__PLUGIN_ID__/__PLUGIN_PKG__/__DISPLAY_NAME__/__TABLE_PREFIX__`；补 README/CHANGELOG/
versions.json/tests。测试逐 kind 生成后在子进程里跑生成物自己的测试。

## 2026-09-04 · artifact filter

Template directories accumulate `__pycache__`/`.pyc` and test-run leftovers when their own tests run in place; `_is_artifact` keeps them out of scaffolded plugins (a `.pyc` copied as text raised UnicodeDecodeError).

Batch 6d: `scaffold()` also writes `.github/workflows/plugin-ci.yml` (`PLUGIN_CI_WORKFLOW`): tests + `publish-check` on every push, and on a tag equal to the manifest version a GitHub release with the install assets.

## 2026-09-07 — templates resolve from the wheel first

resolve_templates_dir() prefers the packaged copy (narranexus/cli/resources/templates, hatch force-include of the repo's templates/), then the source checkout, and otherwise raises a plain 'reinstall narranexus' error — 'narranexus plugin new' used to climb parents[3] from the package and fail with an opaque FileNotFoundError from a wheel.

## 2026-09-07 — one activate per plugin; same-file collisions refused

Several backend kinds produce _activate_<kind>(ctx) helpers and one activate(ctx) calling them in order (N same-named functions left only the last alive). Two kinds that generate the same file (ui_page + ui_panel → frontend/src/index.ts) are refused with the exact conflict instead of the second overwriting the first behind a manifest that declared both.
