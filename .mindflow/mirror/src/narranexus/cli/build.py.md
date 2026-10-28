---
code_file: src/narranexus/cli/build.py
last_verified: 2026-09-04
stub: false
---

# cli/build.py — narranexus build

`build(res, target, out, dry_run, dockerfile)`: the target must be declared by the distribution; writes `narranexus-dist.lock.json`, `builtins.generated.json` (the plugin set's manifests), copies `narranexus-dist.json` and every bundled plugin into `out/`, writes `build-plan.json`, then runs the plan (`uv build` per package for `wheel`, the Tauri bundle for `desktop`, `docker build` with the given Dockerfile for `docker` — without one nothing is built) with `NARRANEXUS_DIST` pointing at the copied declaration; `--dry-run` only prints. `package_name()` maps a builtin id to its workspace package name.
