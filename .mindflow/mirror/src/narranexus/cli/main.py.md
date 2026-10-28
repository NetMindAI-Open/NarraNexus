---
code_file: src/narranexus/cli/main.py
last_verified: 2026-10-14
stub: false
---

## 2026-09-03（批 2e）— `narranexus plugin …` / `narranexus docs gen`

所有变更动词走与工场 API 相同的内核对象（`Installer`/`RegistryStore`/`Bisect`），CLI 与 UI 永远不会对
状态各执一词。`doctor` 只读：不 boot，用 `discover`+`plan_load` 打印会加载/被阻/被拒；`publish-check` 是
发布清单（bronze 门）；`new` 用 `scaffold` 拼模板；`enable --ack` 顺便确认权限。异常一律变成 `error:` 行
+ 退出码，不吐 traceback。`[project.scripts]` 里注册为 `narranexus`。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`plugin enable|disable <builtin.*>` toggles a builtin through registry.json `builtin_overrides` (unknown ids, protected builtins and builtins the running distribution excludes are refused with a non-zero exit — all through the kernel's shared `check_builtin_toggle`, the same rule set as the factory API; no dependants cascade here) instead of the user-plugin record path.

Batch 6c: `narranexus dist doctor [path] [--json]` (resolve against this engine, print plugin rows / excluded / size / problems, exit 1 on any problem) and `narranexus dist lock [path] [--out]` (write `narranexus-dist.lock.json`).

Batch 6c.3: `narranexus create-app <id> [--dir --display-name --base --auth --deployment]` and `narranexus build <path> --target desktop|docker|wheel [--out --dockerfile --dry-run]`.

Batch 6d: `plugin list` rows carry `quality` (read from each plugin's manifest).

2026-09-07: `narranexus slots [--domain --json --toml-template]`, `narranexus bind <slot> <provider…>`, `narranexus unbind <slot>`.

## 2026-09-07 — doctor exit code; link acknowledges; enable respects the permissions gate; TEMPLATES_DIR from scaffold

plugin doctor returns 1 whenever anything is rejected or blocked (the 'and not users' clause answered 0 with four of five plugins rejected). plugin link acknowledges the linked plugin's permissions (it is the developer's own tree). plugin enable refuses (exit 2) a plugin whose declared permissions are not acknowledged unless --ack is given, which records them through the shared installer helper. TEMPLATES_DIR is imported from scaffold (one definition).

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
