---
code_file: src/xyz_agent_context/settings.py
last_verified: 2026-06-18
stub: false
---

## 2026-06-18 — arena_api_base (per-env Arena)

Added `arena_api_base` (default `https://api.arena42.ai`). Externalizes which
Arena environment auto-provisioning registers against, so the dev stack can set
`ARENA_API_BASE=https://arena-dev-api.protago-dev.com` and keep dev test agents
off the prod ladder. Read in the backend process by `ArenaProvisioningService`
(not the executor — the agent's own calls use ARENA_API_URL baked into the
workspace skill at provision time). Same externalize-per-env approach as
APP_DOMAIN; no _DOTENV_PASSTHROUGH entry needed (backend reads it directly).

## 2026-06-11 — invite env passthrough removed

INTERNAL_INVITE_SECRET / INVITE_AUTO_ISSUE_CAP dropped from _DOTENV_PASSTHROUGH (feature retired).

## 2026-05-22 — LLM runtime resilience knobs (#7)

Added `.env`-tunable fields: `llm_api_timeout_ms` (→ CLI `API_TIMEOUT_MS`),
`llm_max_retries` (→ CLI `CLAUDE_CODE_MAX_RETRIES`), `llm_stall_probe_after_seconds`,
`llm_stall_probe_timeout_seconds`. Consumed by `api_config.to_cli_env()` (timeout
+ retries injected into the CLI subprocess) and `xyz_claude_agent_sdk` (stall
health-probe cadence/timeout). Defaults chosen to bound a pathological hang
without cutting a legitimately long thinking pass (铁律 #14). Documented in
`.env.cloud.example`.

## 2026-05-18 — extend .env→os.environ passthrough whitelist

The bridge used to forward only the 4 LLM API keys from `.env` into
`os.environ`. Backend code that reads `os.environ.get()` directly (here:
`BUNDLE_FETCH_ALLOWED_HOSTS` in `backend/routes/bundle.py`'s
`/import/from-url` SSRF guard) was silently ignored — `bash run.sh` /
`make dev-backend` started without the value, the allowlist fell back to
`narra.nexus,www.narra.nexus`, and local dev couldn't fetch from
`localhost:3001`.

Added `_DOTENV_PASSTHROUGH` alongside `_API_KEY_FIELDS`. API keys keep
their "override shell env" semantic (operator wrote them in `.env` via
desktop app, must win); passthrough vars also forward (no separate
setdefault path — match the established pattern).

**When introducing a new backend config that's read via
`os.environ.get()` directly, add it to `_DOTENV_PASSTHROUGH`** —
otherwise `.env` silently has no effect and dev/ops will be confused.

## 2026-05-15 — extend dotenv→os.environ passthrough whitelist

The `.env → os.environ` bridge used to whitelist only the four LLM API keys.
Backend code that reads `os.environ.get()` directly (rather than through the
Settings object) — e.g. `backend/routes/invite.py` reading
`INTERNAL_INVITE_SECRET`, `backend/config.py` reading `INVITE_AUTO_ISSUE_CAP` —
got silently ignored: `.env` value never made it into `os.environ`, so
`bash run.sh` / `make dev-backend` would launch without seeing them.

Added `_DOTENV_PASSTHROUGH` alongside `_API_KEY_FIELDS`:

- API keys still get the original "override shell env" semantic (the
  desktop app writes them to `.env` and they must win)
- Passthrough vars also forward to `os.environ`, same write-unconditional
  behaviour (no separate setdefault path — match the established pattern)

Add new entries to `_DOTENV_PASSTHROUGH` whenever introducing a backend
config that's read via `os.environ.get()` and you want `.env` support.

# settings.py

Process-wide configuration object — reads `.env` and environment variables once at import time and exposes them as a typed singleton.

## Why it exists

Before this file, configuration was loaded through scattered `load_dotenv()` + `os.getenv()` calls across modules, making it impossible to see what was configurable from one place and causing subtle ordering issues (some modules loaded `.env` too late). `settings.py` centralizes every environment variable into a single `Settings` instance (built with `pydantic-settings`) that is created at module import time. Importing `from xyz_agent_context.settings import settings` gives any module access to typed, validated configuration without touching `os.environ` directly.

## Upstream / Downstream

**Reads from:** the `.env` file at `_PROJECT_ROOT/.env` (three levels up from the file itself) and system environment variables. For API key fields, `.env` values are injected into `os.environ` before pydantic-settings reads them, overriding any pre-existing shell variables.

**Consumed by:** `database.py` (`load_db_config`, `_ensure_pool`), `db_factory.py` (`get_db_client`), `agent_framework/` (LLM API keys), `narrative/`, `module/`, and the FastAPI backend. Essentially every module that needs an API key, database URL, or path configuration imports `settings`.

**Also writes to `os.environ`** at the bottom of the file for `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL`, so that third-party SDKs (like OpenAI Agents SDK) that read `os.environ` directly also see the correct values.

## Design decisions

**`.env` overrides shell env for API keys.** The standard `pydantic-settings` priority is "environment variable beats .env file." This is inverted for API key fields: the `.env` file is read raw with `_read_dotenv_raw()` and its values are injected into `os.environ` before pydantic-settings runs, so the user's explicitly configured keys always win over whatever was already in the shell. This matters for the Tauri desktop app, where the user sets keys through the UI and those values are written to `.env` — they must take precedence over any key that might be present in the launch environment.

**`model_validator` for path expansion.** `base_working_path`, `narrative_markdown_path`, and `trajectory_path` allow `~` in their values. The `_expand_user_paths` validator calls `Path.expanduser()` on them so callers never need to handle tilde expansion themselves.

**Empty-string cleanup for `ANTHROPIC_API_KEY`.** If `ANTHROPIC_API_KEY` is empty in `.env` (a blank line or explicit `ANTHROPIC_API_KEY=`), it is deleted from `os.environ` rather than set to `""`. An empty key makes the Claude CLI think an API key is configured and skips its OAuth fallback, breaking desktop authentication.

**`skip_module_decision_llm: bool = True`.** The LLM call that decides which module instances to activate was measured to take 2.5–3 seconds and always returned the same result. This flag lets the runtime skip it and load all capability modules directly. It is `True` by default.

## Gotchas

**`settings` is a module-level singleton created at import time.** If `DATABASE_URL` or an API key changes in the environment after the module is first imported (e.g., in a long-running process that reloads `.env`), `settings` does not update. Restart the process to pick up changes.

**`_PROJECT_ROOT` depends on the file's location.** The root is computed as `Path(__file__).resolve().parents[2]`. If the package is installed in a different directory structure (e.g., via a non-standard editable install), `_PROJECT_ROOT` may point to the wrong place and the `.env` file will not be found.

**`extra="ignore"` silently drops unknown variables.** Any environment variable that does not match a `Settings` field is silently ignored. If you mistype a variable name in `.env` (e.g., `ANTHROPIC_API_KEYS` instead of `ANTHROPIC_API_KEY`), pydantic-settings will not warn you.

**New-contributor trap.** The sync to `os.environ` at the bottom of the file only covers the four API key variables. Other settings (e.g., `DATABASE_URL`) are not written to `os.environ`. Code that tries to read `os.environ["DATABASE_URL"]` directly rather than `settings.database_url` will get nothing.
