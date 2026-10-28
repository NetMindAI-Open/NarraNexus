"""
@file_name: settings.py
@author: NetMind.AI
@date: 2026-02-09
@description: Unified configuration management

Uses pydantic-settings to centrally manage all environment variables, replacing
scattered load_dotenv() + os.getenv() calls throughout the codebase.

Priority: .env file > system environment variables.
When users configure API keys through the desktop app or run.sh, those values
are written to .env and MUST take precedence over pre-existing shell env vars.

Usage:
    from narranexus.platform.settings import settings

    api_key = settings.google_api_key
    db_host = settings.db_host
"""

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (3 levels up from src/narranexus/platform/settings.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/narranexus/platform/settings.py → repo root (batch 6a moved this one level deeper)


def _read_dotenv_raw(env_file: Path) -> dict[str, str]:
    """Read .env file and return raw key-value pairs (no variable expansion).

    This is used to determine which values the user explicitly configured,
    so we can give .env priority over pre-existing shell environment variables.
    """
    result: dict[str, str] = {}
    if not env_file.is_file():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip optional surrounding quotes
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


# Pre-load .env values and inject them into os.environ BEFORE pydantic-settings
# reads them. pydantic-settings' default priority is env_var > .env file, but
# we want the opposite for API keys: the user explicitly configured these in .env
# (via desktop app or run.sh), so they should override any pre-existing shell vars.
#
# Two whitelists drive the injection:
#   _API_KEY_FIELDS     — LLM provider keys, original use case
#   _DOTENV_PASSTHROUGH — other .env-only service secrets / tuning knobs that
#                         backend code reads via `os.environ.get()` directly
#                         (rather than through the Settings object). Add a
#                         var here whenever you introduce one, otherwise it
#                         silently has no effect on os.environ and
#                         `bash run.sh` / `make dev-backend` won't pick it up.
_dotenv_values = _read_dotenv_raw(_PROJECT_ROOT / ".env")
_API_KEY_FIELDS = {"OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
_DOTENV_PASSTHROUGH = {
    "BUNDLE_FETCH_ALLOWED_HOSTS",  # backend/routes/bundle.py — /import/from-url SSRF guard
    # Free tier (cloud-only). Read via os.environ.get() by
    # providers/free_tier.py + integrations/free_tier/wallet_client.py. Cloud
    # sets these as real container env; listed here so a local .env can too.
    # NOTE the gateway's own admin key is deliberately ABSENT: the backend must
    # never hold it — it talks to quota-api, which does.
    "FREE_TIER_ENABLED",
    "FREE_TIER_WALLET_API_URL",
    "FREE_TIER_WALLET_API_TOKEN",
    "FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL",
    "FREE_TIER_GATEWAY_OPENAI_BASE_URL",
    "FREE_TIER_AGENT_MODEL",
    "FREE_TIER_HELPER_MODEL",
    "FREE_TIER_AGENT_THINKING",
    # Where transcription goes for a free-tier user. The proxy holds the
    # operator's STT credential; we only ever send the user's wallet key.
    "FREE_TIER_STT_PROXY_URL",
}
for _k, _v in _dotenv_values.items():
    if not _v:
        continue
    if _k in _API_KEY_FIELDS or _k in _DOTENV_PASSTHROUGH:
        os.environ[_k] = _v


class Settings(BaseSettings):
    """Application global configuration, automatically loaded from .env file and environment variables"""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== LLM API Keys =====
    openai_api_key: str = ""
    google_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = ""  # Empty = let Claude Code CLI use its default model

    # ===== LLM runtime resilience (#7) =====
    # All env-tunable so ops can adjust without a code change. These address
    # "agent run hangs on an API problem with no retry, occupying a runtime
    # slot" WITHOUT violating 铁律 #14 (no agent_loop force-stop / total cap) or
    # #15 (no governing the user's model choice).
    #
    # Injected into the Claude Code CLI subprocess env by `to_cli_env()`:
    #   API_TIMEOUT_MS         — per-REQUEST timeout (NOT a run total). A stalled
    #                            request errors after this and the CLI auto-
    #                            retries. 10 min is generous enough that a
    #                            legitimately-streaming long thinking pass (which
    #                            keeps emitting tokens) is not cut.
    #   CLAUDE_CODE_MAX_RETRIES — CLI's built-in retry count for transient
    #                            errors (429 / 5xx / connection). Same provider,
    #                            count-bounded (not time-bounded).
    llm_api_timeout_ms: int = 600000          # 10 min per request
    llm_max_retries: int = 10                 # CLI default; explicit = deterministic
    # Health-probe diagnostic: when a run produces NO events at all for this
    # long (true silence, subprocess still alive), probe the provider base_url
    # out-of-band and log whether it's reachable — distinguishing "model is
    # thinking" (provider up) from "connection is dead" (provider down). This is
    # diagnostic only; it never force-stops the run (铁律 #14).
    llm_stall_probe_after_seconds: int = 600
    llm_stall_probe_timeout_seconds: int = 10

    # ===== Message-bus worker pool =====
    # How many bus turns may run concurrently in the trigger process. This is
    # OUR resource decision, not a policy on how long an agent may run (binding
    # rule #14) — the pool caps how many rooms we can serve at once, and a pool
    # too small shows up to the user as "the group chat is dead", which is the
    # one failure mode the platform is responsible for avoiding.
    #
    # Was hard-coded at 3, which made a slot shortage both invisible and
    # unfixable without a code change. 8 is the new floor-of-comfort: a bus turn
    # is almost entirely await (LLM + DB), so slots are cheap, and a single team
    # room relaying between members can occupy several at once.
    #
    # Slot wait is inside the `queue_wait_s` that `[bus-timing]` reports, so
    # raising this and re-reading `make latency-report` is a measurable change,
    # not a guess.
    #
    # CROSS-REPO DEPENDENCY — read before raising this again.
    # Production runs `run_worker_supervisor`: poller + jobs + bus + every
    # channel trigger share ONE asyncio loop and therefore ONE MySQL pool. That
    # pool is sized in the deploy repo, `stacks/narranexus-app/compose.yml`
    # (service `workers`, `MYSQL_POOL_SIZE`), whose comment derives the number
    # from "poller(3) + jobs(5) + bus + every channel worker/subscriber". This
    # value is the `bus` term in that arithmetic; changing it here without
    # revisiting that number there is how a bus change turns into "the database
    # got slow" for poller, jobs and every IM channel at once.
    #
    # Why raising it is nonetheless cheap: a connection is borrowed per QUERY,
    # not held for the turn. A bus turn spends ~20 of its ~24 seconds waiting on
    # an LLM and holds nothing during that time — which is also why the pool has
    # always been sized for the typical concurrent QUERY mix rather than the
    # theoretical task count (channel triggers alone allow 50 workers each).
    #
    # If this ever needs to go higher, get evidence first: a `worker_starvation`
    # row in `service_audit` means the pool really is the bottleneck (see
    # `_check_worker_starvation`); its absence means it is not.
    #
    # What changed on 2026-08-14, and why `worker_starvation` is now ambiguous:
    # a team-room reply is posted from INSIDE the turn, so the next hop of a
    # relay is dispatched while the previous agent's turn is STILL RUNNING (an
    # agent may legitimately keep working for a long time after replying —
    # binding rule #14). A slot is released at the end of the turn, not at the
    # reply. So one room's D-hop relay now holds up to D slots at once, where
    # it used to hold 1, and "slots are cheap because a bus turn is mostly
    # await" is only half true: the waiting is cheap, the HOLDING is not.
    # A `worker_starvation` row during a busy team relay is therefore an
    # expected shape, not evidence that someone's agent is wedged — read it
    # together with how many rooms were relaying at the time.
    bus_max_workers: int = 8



    # ===== Turn-context relocation (token optimization phase 3, R4) =====
    # Kill-switch for relocating per-turn volatile content (temporal block,
    # narrative updated_at / current_summary, recent background activity,
    # module contribute_turn_context blocks) out of the system prompt into a
    # "[Turn context]" block prepended to the CURRENT user message. This
    # keeps the system prompt byte-stable across turns so provider prefix
    # caches (Anthropic byte-prefix, DeepSeek/vLLM block-hash) can hit.
    # Relocation moves bytes, it never drops them — the model still sees
    # every relocated section each turn. Off = context assembly is
    # functionally equivalent to the pre-R4 layout — NOT byte-identical, which
    # earlier wording claimed: the OFF path still applies three unconditional
    # determinism normalisations (narrative timestamp canonicalisation,
    # module-block (priority, name) total order, mcp_servers sort). Those move
    # no content and drop none, but they do change bytes, so "off" restores the
    # old STRUCTURE, not the old byte stream. Independent from
    # the synthetic transcript: relocation benefits every framework and
    # every turn; resume is claude_code-only. This is a fail-open ops gate,
    # not a backwards-compatibility shim.
    # Env: PROMPT_TURN_CONTEXT_RELOCATION_ENABLED.
    prompt_turn_context_relocation_enabled: bool = True

    # ===== Helper-LLM one-shot bounds =====
    # The helper_llm slot runs SHORT, tool-free, single-turn structured-output
    # calls (Instance Decision, job analysis, memory consolidation, ...). It is
    # NOT the agent_loop, so bounding it does not violate 铁律 #14 — a helper
    # extraction that never returns is a defect, not a "user waiting" cost.
    #
    # These matter most for the CLI-backed helper (subscription/OAuth), which
    # spawns a `claude` subprocess per call. Without an override it would
    # inherit the agent-loop env from ClaudeConfig.to_cli_env
    # (API_TIMEOUT_MS=llm_api_timeout_ms, retries=llm_max_retries) — i.e. up to
    # 10 min/request × 10 retries ≈ 100 min hang on a bad/hijacked endpoint,
    # which surfaced as "Job stuck at 正在创建" when helper_llm was set to Claude.
    # Kept self-consistent: the wall-clock total is the HARD cap on one
    # one-shot; the per-request timeout × (1 + retries) is the SOFT budget the
    # CLI's own retries spend inside it. 60_000ms × (1 + 1) = 120s = the total,
    # so a configured retry can actually run instead of being cut off early.
    helper_cli_timeout_ms: int = 60000            # per-request cap for the helper CLI subprocess (1 min)
    helper_cli_max_retries: int = 1               # helper CLI transient-retry count (not agent-loop's 10)
    helper_cli_total_timeout_seconds: int = 120   # HARD wall-clock bound for ONE helper one-shot
    # Same-session retry of the Claude Code agent loop after a transient CLI
    # error (rate_limit / server_error) on a SUBSCRIPTION account. The CLI
    # itself retries a 429 only for keyed auth (its predicate is
    # `status === 429 → !isSubscriber()`), so for a claude.ai login one
    # "Opus is experiencing high load" used to end the whole turn. The adapter
    # resumes the same CLI session instead — the CLI transcript already holds
    # the turn's tool calls, so nothing re-executes. Keyed auth is untouched
    # (CLAUDE_CODE_MAX_RETRIES above already covers it). This bounds RETRIES OF
    # A FAILING CALL, not turn length (铁律 #14). 0 disables.
    # Env: CLAUDE_TRANSIENT_RETRY_ATTEMPTS / CLAUDE_TRANSIENT_RETRY_BACKOFF_SECONDS.
    # SCOPE: the env override reaches this knob only where agent_loop runs in
    # the backend process (local / `bash run.sh` / DMG). On cloud the loop
    # runs in the per-user executor, whose env is the broker's strict
    # allowlist (deploy broker/broker.py) — the code defaults apply there
    # until a NEXUS_POWER_POOL_SIZE-style passthrough is added on the deploy
    # side.
    claude_transient_retry_attempts: int = 3
    # Comma-separated seconds before retry 1, 2, 3, …; a short list pads with
    # its last value. Tens of seconds on purpose: capacity shedding does not
    # clear in milliseconds.
    claude_transient_retry_backoff_seconds: str = "15,30,60"
    # Claude Code agent loop, SUBSCRIPTION auth only (oauth / oauth_token): how
    # many concurrency-safe tool calls of ONE assistant message the CLI runs in
    # parallel — parallel Read/Grep/MCP calls and every sub-agent (Task) launch
    # alike; the CLI's own default is 10 (CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY).
    # Each sub-agent is a model loop against the same quota and the CLI never
    # retries a subscriber's 429, so an unbounded fan-out is a 429 storm. Keyed
    # auth is untouched (the CLI retries 429s there itself). A CONCURRENCY cap
    # (how many run at once — every call still runs, later), never an
    # iteration / time ceiling on the loop (铁律 #14). 0 = keep the CLI default.
    # Env: CLAUDE_MAX_TOOL_USE_CONCURRENCY. Same scope caveat as the retry
    # knobs above (cloud executors take the code default). The default 6 is a
    # judgement call, not a measurement: below the CLI's 10 so a message that
    # launches many sub-agents cannot burst the full ten, above the first cut
    # of 4 so a parallel-read batch loses less. Tune via the env when data
    # says otherwise.
    claude_max_tool_use_concurrency: int = 6
    # How many times a Claude helper structured-output call re-prompts for
    # valid JSON before giving up. Prompt-engineered structured output (schema
    # in the prompt + client-side JSON extraction) sometimes returns prose /
    # schema-divergent JSON on the first try, especially for complex nested
    # schemas on Haiku; a bounded repair retry recovers most of these.
    helper_json_repair_attempts: int = 3
    # Log the SHAPE of every helper prompt — lengths plus leading-slice hashes,
    # no content. Off by default; this sits on the hot path of a call that runs
    # ~6 times per turn. Turned on to answer whether those calls share a
    # byte-identical head long enough to cache (claude-haiku-4-5 will not cache
    # a prefix under 4096 tokens, so "some repetition" is not sufficient).
    # Pair with HELPER_PROMPT_DUMP_DIR to also write the exact payloads, which
    # upgrades the bracketed answer to an exact longest-common-prefix — that
    # one writes conversation content to disk, so it is a separate opt-in.
    helper_prompt_probe_enabled: bool = False

    # ===== Database =====
    database_url: Optional[str] = None
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""

    # SSL (optional)
    db_ssl_ca: Optional[str] = None
    db_ssl_cert: Optional[str] = None
    db_ssl_key: Optional[str] = None
    db_ssl_verify_cert: Optional[str] = None

    # ===== Workspace =====
    # Absolute path under user home; immune to cwd differences between
    # dev server, Electron bundle, and CLI scripts.
    base_working_path: str = str(Path.home() / ".nexusagent" / "workspaces")

    # ===== Skill Marketplace =====
    # When true, this instance hosts its OWN skill registry (catalog in the
    # local DB, artifacts in the local store) instead of proxying browse/
    # install to NARRANEXUS_MARKETPLACE_URL. For dev, offline demos, and the
    # window before the cloud marketplace is live. Settable from .env so
    # `make dev-backend` needs no env prefix.
    skill_marketplace_local_registry: bool = False

    # Dedicated CLAUDE_CONFIG_DIR for the agent_loop CLI subprocess, kept
    # OUT of the host user's ~/.claude. Claude Code applies that file's `env`
    # block above the subprocess env we inject, so a developer's personal
    # config (custom ANTHROPIC_BASE_URL/AUTH_TOKEN/model) would otherwise
    # silently redirect the agent_loop off its configured provider. The keyed
    # auth paths use this dir. See api_config.ClaudeConfig.to_cli_env.
    claude_cli_config_path: str = str(Path.home() / ".nexusagent" / "claude_config")

    # Dedicated CLAUDE_CONFIG_DIR for the OAuth agent_loop path. Kept SEPARATE
    # from both the host ~/.claude and the keyed dir above: OAuth used to point
    # straight at ~/.claude so the CLI could read `.credentials.json`, but that
    # re-opened the same hijack (personal settings.json env block overrides the
    # OAuth run) AND raced the user's own Claude Code on ~/.claude/.claude.json
    # (2026-07-09 incident). Now the credential file is STAGED into this
    # isolated dir by _stage_claude_oauth_credentials; the personal settings.json
    # is never copied, so it can't leak. See api_config.ClaudeConfig.to_cli_env.
    claude_oauth_config_path: str = str(Path.home() / ".nexusagent" / "claude_oauth_config")

    # Prefer the version-pinned `claude` on PATH over the binary bundled inside
    # the claude-agent-sdk wheel. The bundled one wins by default in the SDK
    # (`_find_cli` checks it first), and SDK 0.1.43 bundles CLI 2.1.56 — a
    # version that does NOT normalize the request's `tools` array, so the array
    # permutes every run and voids the whole cache prefix behind it (measured:
    # experiments E3/E3c). See adapters/claude/cli_binary.py for the mechanism
    # and the fail-open rules. Turning this off restores the pre-2026-07-29
    # behavior (always bundled) — an ops gate, not a compatibility shim.
    # Env: CLAUDE_CLI_PREFER_PINNED.
    claude_cli_prefer_pinned: bool = True

    # Explicit path to the `claude` binary the agent loop should launch,
    # outranking both the pin lookup and the SDK's bundled copy. Empty = use
    # the resolution above. For environments that install the CLI somewhere
    # off PATH (and as the escape hatch when a pin bump is mid-rollout).
    # A path that does not exist is IGNORED rather than honoured, so a typo
    # degrades to the bundled binary instead of failing every turn.
    # Env: CLAUDE_CLI_PATH.
    claude_cli_path: str = ""

    # Author the CLI's resume transcript ourselves every turn, instead of
    # relying on a stored CLI session handle. When on, the adapter writes the
    # conversation history into
    # ``<CLAUDE_CONFIG_DIR>/projects/<cwd-slug>/<session_id>.jsonl``, resumes
    # it, and deletes the file when the turn ends.
    #
    # Why it matters: the prompt cache matches a strict byte prefix ordered
    # tools → system → messages, so history in the system prompt sits INSIDE
    # the prefix. Handle-based resume already moved history out on RESUME turns,
    # but a cold turn still carries it, so the two prompts differ and the first
    # resume turn after any cold turn misses from ``system`` onward (~49K
    # full-price tokens, measured). Writing the transcript ourselves makes every
    # turn a resume turn, so the prompt is byte-identical from turn one.
    #
    # Fail-open at every step: nothing to resume, or the file cannot be written,
    # and the turn runs exactly as it does today with history in the prompt.
    # This is an ops gate, not a compatibility shim.
    # Env: CLAUDE_SYNTHETIC_TRANSCRIPT_ENABLED.
    claude_synthetic_transcript_enabled: bool = True


    # ===== Export Paths =====
    narrative_markdown_path: str = str(Path.home() / ".nexusagent" / "data" / "narratives")
    trajectory_path: str = str(Path.home() / ".nexusagent" / "data" / "trajectories")

    # ===== Auth =====
    admin_secret_key: str = ""

    # ===== Arena (NetMind Agent Arena) =====
    # Base URL of the Arena API that auto-provisioned competitor agents register
    # against and call at runtime. Externalized per deployment so dev does not
    # pollute the live competitive ladder: prod keeps the default (api.arena42.ai),
    # the dev stack sets ARENA_API_BASE=https://arena-dev-api.protago-dev.com in
    # its ops .env (same mechanism as APP_DOMAIN). The value flows into both the
    # registration call and the agent's installed skill env (ARENA_API_URL), so
    # the agent's own HTTP calls target the same environment automatically.
    arena_api_base: str = "https://api.arena42.ai"

    # ===== NetMind Billing / Subscription =====
    # Base URL of NetMind's billing+subscription API (balance / plan / subscription / recharge).
    # Externalized per deployment the same way as arena_api_base: prod keeps the
    # default (billing.api.netmind.ai); the dev stack sets
    # BILLING_API_BASE=https://billing.api.protago-dev.com in its ops .env.
    # The NarraNexus backend proxies the user's NetMind loginToken to this host
    # (see backend/routes/billing.py) — we never store the token, only forward.
    billing_api_base: str = "https://billing.api.netmind.ai"
    billing_api_timeout_seconds: float = 10.0

    # Which Stripe account collects. NarraNexus IS the "nexus" scenario, and
    # only that account has Alipay + WeChat Pay enabled, so it is the default
    # rather than something a caller opts into. Upstream reads an absent value
    # as "power" (the original shared account); we always send ours explicitly
    # so the body says what it means.
    #
    # It is a SETTING and not a constant for one reason: if the nexus account
    # has an incident, flipping this to "power" restores the previous payment
    # path in one deploy. It must never be reachable from client input — the
    # merchant that collects a payment is exactly as attacker-interesting as
    # the post-payment redirect target, which backend/routes/billing.py already
    # refuses to take from a request body (see `_return_urls`).
    #
    # Consequence of the default, stated so nobody rediscovers it in support:
    # a user who previously paid through "power" is a DIFFERENT Stripe customer
    # here, so their saved card is not offered on the first nexus checkout.
    # Free credit and subscription state land on the same NetMind ledger either
    # way. Cancel and reactivate now SEND this channel rather than relying on
    # upstream to route them by the subscription's own account — that routing
    # claim comes from the integration doc, was never measured, and directly
    # contradicted this client's own "an absent channel reads as power". Under
    # the pessimistic reading, omitting it means a card subscription created
    # here cannot be cancelled at all; sending it is inert under the optimistic
    # one. Both endpoints accept the field (measured 2026-08-19).
    # Literal, not str: this field exists to be edited under pressure during a
    # payment incident, and `BILLING_CHANNEL=nexux` in a deploy .env would start
    # both boxes cleanly and only surface as an upstream 400 at the first real
    # payment — found by a paying user rather than by the release.
    billing_channel: Literal["nexus", "power"] = "nexus"

    # NetMind Key-management API (generate/list inference API keys). This is a
    # DIFFERENT host + auth from billing: header is `token` (not `loginToken`),
    # body is form-encoded, envelope is HTTP 200 + {success:false} on error.
    # Host migrated 2026-08-28 from the legacy Java gateway to the Python
    # inference gateway; path/auth/params/response envelope are unchanged.
    # prod inference.api.netmind.ai; dev sets
    # NETMIND_KEY_API_BASE=https://inference.api.protago-dev.com.
    netmind_key_api_base: str = "https://inference.api.netmind.ai"

    # NetMind inference base (chat/completions + messages). ONLY the
    # use-subscription (minted-key) path uses this: a key we mint via
    # NETMIND_KEY_API_BASE must hit the MATCHING inference env, so this must be
    # set to the same NetMind environment as the key/auth/billing hosts.
    # Manual "paste your own key" (OneKeyOnboard) intentionally does NOT use it —
    # a user's own key is a public prod NetMind key, so that path stays on prod.
    # prod api.netmind.ai; dev sets
    # NETMIND_INFERENCE_BASE=https://test.api.netmind.ai/inference-api.
    netmind_inference_base: str = "https://api.netmind.ai/inference-api"

    # Module F gate (Phase 5): "use this subscription" auto-generates a NetMind
    # inference key and wires it to the agent/helper slots. Kept OFF until the
    # C1 contract is confirmed with NetMind — i.e. that the generated key's
    # consumption bills against the subscription grant / account balance (and
    # is reflected in user-fee-info). Flip to True once confirmed.
    netmind_use_subscription_enabled: bool = False

    # ===== Speed Optimization =====
    # When True, skip the LLM instance decision call in Step 2 and always load
    # all capability modules directly.  This saves ~2.5-3s per turn since the
    # LLM call currently always returns the same 4 modules.
    skip_module_decision_llm: bool = True

    # ===== Transcription (audio → text) =====
    # Externally-reachable base URL for this NarraNexus deployment. Used by
    # the NetMind transcription backend to mint signed audio URLs that
    # NetMind's worker can fetch. Empty disables system-default NetMind
    # transcription (the resolver downgrades to "unavailable" instead of
    # minting URLs that NetMind can't reach).
    public_base_url: str = ""

    # HMAC-SHA256 secret used to sign transcription audio URLs. In cloud
    # mode this MUST be set explicitly — we refuse to derive a secret in
    # production. In local mode an unset value falls back to admin_secret_key.
    transcription_hmac_secret: str = ""

    # NOTE: the operator's NetMind STT credential is deliberately NOT here any
    # more (2026-07-28). Transcription for free-tier users goes through the
    # deploy-side STT proxy authenticated by the user's own wallet key, so the
    # operator credential lives only in that proxy's container — it used to
    # have to be present in backend, mcp and workers for STT to work at all.

    @property
    def is_cloud_mode(self) -> bool:
        """Cloud (MySQL) vs local (SQLite), via the kernel's single resolver.

        ``narranexus.kernel.deployment`` reads the same environment this
        Settings instance was built from (``.env`` is pre-injected into
        ``os.environ`` above), so the answer matches ``backend.auth`` and
        ``utils.deployment_mode`` by construction.
        """
        from narranexus.kernel.deployment import is_cloud_mode

        return is_cloud_mode()

    @model_validator(mode="after")
    def _expand_user_paths(self) -> "Settings":
        """Expand ~ in path settings so callers don't need to handle it."""
        for field in ("base_working_path", "narrative_markdown_path", "trajectory_path"):
            raw = getattr(self, field)
            expanded = str(Path(raw).expanduser())
            if expanded != raw:
                object.__setattr__(self, field, expanded)
        return self


settings = Settings()

# Sync key variables to os.environ for direct use by third-party SDKs (e.g., OpenAI Agents SDK).
# pydantic-settings only loads values into the Settings object and does not automatically write to os.environ.
_ENV_SYNC = {
    "OPENAI_API_KEY": settings.openai_api_key,
    "GOOGLE_API_KEY": settings.google_api_key,
    "ANTHROPIC_API_KEY": settings.anthropic_api_key,
    "ANTHROPIC_BASE_URL": settings.anthropic_base_url,
}
for _key, _val in _ENV_SYNC.items():
    if _val:
        # Unconditionally write: settings already reflects .env > shell priority
        # (pre-injection above ensures .env API keys override shell env vars).
        os.environ[_key] = _val
    elif _key in os.environ and not os.environ[_key]:
        # Clean up empty values in os.environ (may come from .env blank lines
        # or desktop getExecEnv). An empty ANTHROPIC_API_KEY would make
        # Claude CLI think an API key is configured and skip OAuth fallback.
        del os.environ[_key]
