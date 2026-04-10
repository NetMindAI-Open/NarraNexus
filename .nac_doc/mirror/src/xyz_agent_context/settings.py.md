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
