---
code_file: src/xyz_agent_context/settings.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — helper-LLM one-shot 界值(Lark bug #2)

新增一组 4 个字段,专门约束 **helper_llm 一次性调用**(Instance Decision / job 分析 /
memory / social entity 等短、单轮、无工具的结构化提取)——它不是 agent_loop,故设界
不违反铁律 #14:

- `helper_cli_timeout_ms` (60000)、`helper_cli_max_retries` (1):CLI helper 子进程的
  每请求超时与重试。默认由 [[cli_helper_sdk]] 的 `_run_claude_oneshot` **覆盖**
  `to_cli_env` 注入的 agent-loop 值(~10min×10),否则坏/被劫持端点可挂近 100min。
- `helper_cli_total_timeout_seconds` (120):**单次 one-shot 的硬墙钟上界**
  (`asyncio.wait_for`)。三者刻意自洽:`60s×(1+1)=120s=total`,让配置的重试真能跑满
  而不是被墙钟提前砍掉(硬上界 vs 软预算的关系写在字段注释里)。
- `helper_json_repair_attempts` (3):Claude helper 结构化输出抠取/校验失败时的有界
  修复重试次数(见 [[anthropic_helper_sdk]] / [[cli_helper_sdk]])。

全部带默认值、无新必填 env,部署无影响。
## 2026-07-22 — skill_marketplace_local_registry 字段

新增 `skill_marketplace_local_registry: bool = False`(可从 .env 读)。为 true 时
本地/桌面实例自己当 skill/team registry(不 proxy 云端),用于 dev、离线演示、
cloud marketplace 上线前过渡。等价于 env `SKILL_MARKETPLACE_LOCAL_REGISTRY=1`,
但落 Settings 后 `make dev-backend` 无需前缀即可生效。


## 2026-07-09 — claude_oauth_config_path (OAuth config-dir isolation)

Added `claude_oauth_config_path` (default `~/.nexusagent/claude_oauth_config`),
a dir kept SEPARATE from both the host `~/.claude` and the keyed
`claude_cli_config_path` below. #72 (below) isolated only the keyed path and
left OAuth pointing at the real `~/.claude`, which re-exposed the same hijack
(personal `settings.json` `env` block overriding the OAuth run) AND raced the
user's own Claude Code on `~/.claude/.claude.json` (2026-07-09 incident). OAuth
now uses this isolated dir; `xyz_claude_agent_sdk._stage_claude_oauth_credentials`
stages ONLY `.credentials.json` into it before the spawn (never `settings.json`).
Consumed by `api_config.ClaudeConfig.to_cli_env()`.

## 2026-07-08 — claude_cli_config_path (agent_loop config-dir isolation)

Added `claude_cli_config_path` (default `~/.nexusagent/claude_config`). It
becomes the `CLAUDE_CONFIG_DIR` of the keyed agent_loop CLI subprocess so the
host user's personal `~/.claude/settings.json` — whose `env` block outranks the
subprocess env we inject — can no longer hijack the provider (2026-07-08
incident: personal relay in that `env` block returned `503 No available
accounts` for every message). Consumed by `api_config.ClaudeConfig.to_cli_env()`.
Same user-home-absolute-path style as `base_working_path`. (2026-07-09: OAuth no
longer "keeps the real ~/.claude" — see `claude_oauth_config_path` above.)

## 2026-07-07 — deploy env vars added by the NetMind billing integration (count + prod/local impact)

The NetMind subscription/billing feature (PRs #62 + #70) added **5** deployment
env vars (names only — values are per-environment, see `.env.cloud.example`):

- `BILLING_API_BASE`
- `BILLING_API_TIMEOUT_SECONDS`
- `NETMIND_KEY_API_BASE`
- `NETMIND_INFERENCE_BASE`
- `NETMIND_USE_SUBSCRIPTION_ENABLED`

`NETMIND_AUTH_API_URL` is NOT one of them — it predates this (the NetMind login
feature). The system free-tier vars (`SYSTEM_DEFAULT_NETMIND_*`) are also separate.

**Local/desktop mode: unaffected — needs none of these.** The billing and
use-subscription routes are cloud-gated (`is_cloud_mode()` → 404 in local), so
these settings are never read off the local path. Local behavior is identical
whether or not they are set.

**Prod-upgrade checklist (so nothing is missed):**
- The three base URLs (`BILLING_API_BASE` / `NETMIND_KEY_API_BASE` /
  `NETMIND_INFERENCE_BASE`) DEFAULT to prod, so a prod deploy needs **no change**
  to them — the defaults are already the prod hosts.
- The ONE deliberate prod action is `NETMIND_USE_SUBSCRIPTION_ENABLED`: keep
  **False** until the C1 billing contract is verified end-to-end on prod, then flip.
- Dev/staging must override all three base URLs (+ the pre-existing
  `NETMIND_AUTH_API_URL`) to the dev NetMind env AND set the flag True. Full
  dev↔prod mapping lives in `.env.cloud.example`.

## 2026-07-07 — netmind_inference_base

Added `netmind_inference_base` (default prod `https://api.netmind.ai/inference-api`;
dev sets `NETMIND_INFERENCE_BASE=https://test.api.netmind.ai/inference-api`). Used
ONLY by the use-subscription minted-key path; must match the same NetMind env as
NETMIND_KEY_API_BASE / BILLING_API_BASE / NETMIND_AUTH_API_URL. Manual key paste
stays on prod. See [[providers]] / [[user_provider_service]].



## 2026-07-06 — NetMind billing / subscription settings

Added the NetMind billing block (externalize-per-env, same pattern as
arena_api_base): `billing_api_base` (default prod `billing.api.netmind.ai`; dev
sets `BILLING_API_BASE=https://billing.api.protago-dev.com`),
`billing_api_timeout_seconds`, `netmind_key_api_base` (key-mint API, default
`platform-api.netmind.ai`), and `netmind_use_subscription_enabled` — the flag
gating the one-click "use my subscription" key-mint (default **False**, stays off
until the C1 billing contract is confirmed and a multi-worker distributed guard
lands; see [[providers]] / [[netmind_billing_client]] / [[netmind_key_client]]).

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
