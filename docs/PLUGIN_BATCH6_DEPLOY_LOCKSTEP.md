# Plugin platform — deploy-repo lockstep

**The deploy repo MUST change in the same release window as the app release that
carries the plugin platform.** Merge order: deploy PR first (its Dockerfiles must
already copy the workspace members when the app image is built), pin the
submodule SHA in `stacks/narranexus-app/submodule-pin.lock`, then release the app.
Rolling back one side without the other breaks the build (see §1) — treat the
pair as one change.

Everything below was derived from an exhaustive grep of the deploy repo, not
from memory. Re-run it before acting; if it finds something this file does not
list, this file is wrong:

```bash
# in the deploy repo root
git grep -nE 'xyz_agent_context|src/xyz|skill_module/builtin_skills' -- stacks docker scripts Makefile .github
```

## 1. Image build (REQUIRED — the build fails without it)

`NarraNexus/pyproject.toml` declares a uv workspace
(`[tool.uv.workspace] members = ["packages/*", "plugins/*"]`) and the root
project depends on every member via `{ workspace = true }`. The dependency-only
layer therefore needs the members' manifests present or `uv sync --frozen`
fails with `Distribution not found at: file:///app/packages/narranexus-contracts`.

In **both** `docker/Dockerfile.python` and `docker/Dockerfile.executor`, before
`RUN uv sync --frozen --no-install-project --no-dev --extra plugins`:

```dockerfile
COPY NarraNexus/pyproject.toml NarraNexus/uv.lock ./
COPY NarraNexus/packages/ ./packages/
COPY NarraNexus/plugins/  ./plugins/
RUN uv sync --frozen --no-install-project --no-dev --extra plugins
```

`Dockerfile.broker` runs no `uv sync` — unchanged. The app repo's own
`docker/Dockerfile.manyfold` already does this (`COPY packages/ ./packages/`,
`COPY plugins/ ./plugins/`).

Trade-off: copying the member sources into the dependency layer means a plugin
source edit re-runs the sync. Acceptable; a manifests-only pre-stage can be
added later if the cache loss hurts.

`docker/Dockerfile.frontend` needs **no** change: `npm ci` with only
`package.json` + `package-lock.json` succeeds and creates the workspace
symlinks (`node_modules/@narranexus/{sdk,ui-kit}`) which resolve once the source
is copied; `npm run build` succeeds (verified on node 20.20 / npm 10.8). Copying
`frontend/packages/*/package.json` before `npm ci` is harmless and slightly
better for cache correctness, but not required.

## 2. Gate scripts (REQUIRED — `make app-build` runs them first)

| File | Line | Today | Change to |
|---|---|---|---|
| `scripts/check_executor_clis.sh` | 25 | `SKILLS_DIR=$ROOT/NarraNexus/src/xyz_agent_context/module/skill_module/builtin_skills` (**path no longer exists → gate fails closed**) | `$ROOT/NarraNexus/plugins/builtin.skills/src/narranexus_plugins/skill_module/builtin_skills` |
| `scripts/check_trigger_alignment.sh` | 24 | `MODULE_DIR=…/src/xyz_agent_context/module` | `…/src/narranexus/platform/module_system` |
| `scripts/check_trigger_alignment.sh` | 29 | `ENTRYPOINT="xyz_agent_context.module.run_worker_supervisor"` | `narranexus.platform.module_system.run_worker_supervisor` (must match compose, §3) |
| `scripts/check_trigger_alignment.sh` | 63-66 | legacy-entrypoint warning list uses `xyz_agent_context.*` names | `narranexus.platform.services.module_poller`, `narranexus.platform.message_bus.message_bus_trigger`, `narranexus.platform.module_system.run_channel_triggers` |

`check_trigger_alignment.sh` still passes against the alias shims this release
(`src/xyz_agent_context/module/{run_worker_supervisor,run_channel_triggers}.py`
exist and the supervisor shim contains `start_channel_triggers`), so lines
24/29/63-66 can move with §3; line 25 of `check_executor_clis.sh` **must** move
now.

## 3. Compose / Dockerfile entrypoints (works via the alias this release; switch now or next)

`python -m xyz_agent_context.<path>` works: the alias package's loader forwards
`get_code` to the platform module, so `runpy` executes the real code. Verified by
`tests/test_legacy_package_shim.py` (subprocess resolution of all five names +
`run_worker_supervisor --help`). The alias is removed at app version
`REMOVED_AT = 1.22.0` (`src/xyz_agent_context/__init__.py` raises `ImportError`
past it), so these references must be switched before that release:

| File | Line | Today | Switch to |
|---|---|---|---|
| `stacks/narranexus-app/compose.yml` | 322 | `src/xyz_agent_context/module/module_runner.py mcp` (by-path shim) | `src/narranexus/platform/module_system/module_runner.py mcp` |
| `stacks/narranexus-app/compose.yml` | 357 | `-m xyz_agent_context.module.run_worker_supervisor` | `-m narranexus.platform.module_system.run_worker_supervisor` |
| `stacks/narranexus-app/compose.yml` | 375 | `-m xyz_agent_context.services.model_sync_runner` | `-m narranexus.platform.services.model_sync_runner` |
| `docker/Dockerfile.executor` | 118 | `CMD [… "-m", "xyz_agent_context.agent_runtime.executor_service"]` | `narranexus.platform.agent_runtime.executor_service` |
| comments only: `compose.yml` 332, 364; `Dockerfile.executor` 3, 86 | | prose | update with the code |

Recommended: switch §3 together with §2 in the same deploy PR so the alias is
never load-bearing in production.

## 4. Environment

No `.env` change is required. New variables all default safely:
`NARRANEXUS_DIST` (unset = every builtin), `NX_BIND__*` (optional bindings),
`NEXUS_POWER_POOL_SIZE` (default 1), `MCP_PORT` (default 7801; the `mcp`
service publishes no ports). One thing to **verify on both EC2s** before the
release: `SKILL_SECRETS_KEY` must be set (and identical) for every container
that reads or writes IM channel credentials — the generic credential store now
encrypts every channel credential with it; with the variable unset each
container derives a file key under its own `base_working_path`, and a credential
written by `backend` cannot be read by `workers`.

```bash
scripts/ec2_inspect.sh dev env-keys | grep SKILL_SECRETS_KEY
scripts/ec2_inspect.sh prod env-keys | grep SKILL_SECRETS_KEY
```

## 5. After the switch (app side, release after)

Delete `src/xyz_agent_context/` and its `packages` entry in `pyproject.toml`,
the `pyrightconfig.json` include, and the import-linter `root_packages` entry.
Acceptance grep in the app repo before deleting (must be empty apart from the
alias package itself and its test):

```bash
git grep -nE 'xyz_agent_context' -- src backend scripts tests frontend tauri plugins packages \
  | grep -v '^src/xyz_agent_context/' | grep -v test_legacy_package_shim
```

## 6. Distributions (optional)

A host started with `NARRANEXUS_DIST=<dir>` boots that distribution; unset =
every builtin (today's behaviour). Official declarations live in
`distributions/`.
