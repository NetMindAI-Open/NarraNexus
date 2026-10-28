# Distributions (`narranexus-dist.json`)

A distribution is a declaration, not a fork: engine range + plugin set + branding + auth + defaults + runtime shape + build targets (spec section 19). The official ones live in `distributions/` (`desktop`, `cloud`, `minimal`, `example-tob`).

## The file

| Field | Meaning |
|---|---|
| `id`, `displayName`, `description` | Identity; `id` is `<publisher>.<name>`. |
| `engine` | Version range this distribution supports (`>=1.15 <2`, `^1.0`, `*`). |
| `plugins` | Plugin id → version range (a builtin of the engine) or `{"path": "./plugins/<id>"}` (bundled with the distribution; boots in stage 1 like a builtin). |
| `excludes` | Builtins deliberately left out (documentation of intent; anything not in `plugins` is out anyway). |
| `auth` | The `authProviders` plugin filling `kernel.auth` — must be in `plugins`, provide `kernel.auth` and be `distributionOnly`. Builtins: `builtin.auth.local` (X-User-Id), `builtin.auth.netmind` (JWT). |
| `bindings` | Distribution-layer slot bindings (spec section 6.4); providers must be in the set. |
| `runtime.deployment` / `runtime.userPlugins` | `desktop` / `cloud` / `headless`; cloud forces `userPlugins: false` (D1). |
| `targets` | `desktop`, `docker`, `wheel`. |
| `branding`, `defaults` | Passed through to the shell and the first-agent defaults. |

## Tools

- `narranexus dist doctor <dir>` — resolves against this engine; exits non-zero on any problem (engine range, unknown plugin, range miss, bundled manifest errors, dependency outside the set, bad `auth`, foreign binding provider). `--json` for machines.
- `narranexus dist lock <dir>` — writes `narranexus-dist.lock.json`: the exact plugin set (version + source + relative path) a build bakes in.
- `narranexus create-app <publisher>.<name>` — scaffold a distribution repo (declaration from an official base, a bundled plugin, an auth choice or stub, branding, defaults, CI).
- `narranexus build <dir> --target desktop|docker|wheel [--dry-run]` — lock + `builtins.generated.json` + bundled plugins into `build/<id>/`, then the packaging command (`uv build` per package, the Tauri bundle, or `docker build --dockerfile <deploy repo Dockerfile>`).
- Headless: `from narranexus.engine import Engine; engine = Engine.load("distributions/minimal")`; `engine.run_turn(agent_id, user_id, text)` streams the turn, `engine.agents(user_id)`, `engine.events().subscribe(...)`.
- Run a host as a distribution: `NARRANEXUS_DIST=<dir or file>`; the boot drops the builtins outside the set, loads bundled plugins in stage 1 and gates runtime plugins by `runtime.userPlugins`. The resolved bindings are snapshotted to `<plugin home>/run/bindings.resolved.json`.

## Who answers "who is this request?"

The backend asks the plugin bound to `kernel.auth` (`backend/auth_provider.py`): the distribution's `auth`, or without a distribution the builtin matching the deployment mode. The provider contract is `AuthProvider.authenticate(request) -> identity | None`; an identity carries `user_id` and `role`; raising `backend.auth_errors.AuthError(code, detail, status_code)` reports that code to the client. The middleware keeps everything around it (exempt paths, marketplace public reads, the nx service bearer, account state, quota gating).

## Runtime install vs build-time composition

A plugin whose manifest says `distributionOnly: true` (an auth provider, anything filling `kernel.*` / `ui.shell`) is rejected by the plugin factory and `registry.json` discovery; it can only enter a build through `narranexus-dist.json`. Everything else may do both.
