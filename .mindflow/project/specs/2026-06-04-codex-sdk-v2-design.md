# Codex SDK v2 — migrate to OFFICIAL `openai-codex` Python SDK

**Date**: 2026-06-04
**Trigger branch**: `feat/codex-sdk-v2` (forked from `feat/codex-sdk-05-29` after
v1 squash-merges into dev)
**Status**: design — ready to implement on a fresh branch

---

## Why a v2 at all

`feat/codex-sdk-05-29` (v1, ~27 commits) wraps codex CLI via hand-rolled
`asyncio.create_subprocess_exec` + JSON-Lines stdout parsing. It works
end-to-end but pays continuous tax:

* codex CLI is releasing fast (0.135 → 0.137 in 3 days; 816 releases
  total at time of writing). Each release is a potential
  protocol/sandbox/config change we have to chase by reading source.
* Hand-rolled subprocess management buys nothing — every codex
  integration ends up doing the same thing.
* The reasoning-summary UX is bad: codex `exec --json` only emits
  `item.completed` with the full summary text, not deltas. The
  Thinking panel therefore appears as one big block rather than
  streaming.
* Cancellation is via subprocess SIGTERM + race-with-cancel — coarse
  and brittle.

Initial assumption was that "official Python SDK" didn't exist, so we
shouldn't migrate. That was wrong — see "What we got wrong" below.

## The official SDK (verified 2026-06-04)

* **Package**: `openai-codex` on PyPI, ships in `openai/codex` monorepo
  at `sdk/python/`.
* **Source of truth**: <https://developers.openai.com/codex/sdk> and
  <https://github.com/openai/codex>.
* **Version at writing**: 0.1.0b3 — official OpenAI, but still beta.
  PEP 440 admits breaking changes possible.
* **Dependency**: drags in `openai-codex-cli-bin` (currently 0.137.0a4)
  which **ships the codex CLI binary as a wheel** — no separate `npm
  install -g @openai/codex` required.

**Different package**: the PyPI name `openai-codex-sdk` (which we
accidentally installed earlier in the v1 work) is a community fork by
`tomasroda`, NOT the official one. Discovery scripts targeting it
produced a totally different API shape (`start_thread`, `TextInput(type='text', text=...)`,
JS-style AbortController). All of that is irrelevant to the official
SDK. Don't reference the community spike in v2.

## What the SDK exposes (Section 0 of the spike, run against the OFFICIAL package)

### Core classes

```python
from openai_codex import (
    Codex, AsyncCodex,
    Thread, AsyncThread,
    TurnHandle, AsyncTurnHandle, TurnResult,
    CodexConfig,
    Sandbox, ApprovalMode,
    TextInput, ImageInput, LocalImageInput, MentionInput, SkillInput,
    # auth handles
    ChatgptLoginHandle, AsyncChatgptLoginHandle,
    DeviceCodeLoginHandle, AsyncDeviceCodeLoginHandle,
    # errors
    CodexError, CodexRpcError, TransportClosedError, RetryLimitExceededError,
    ServerBusyError, JsonRpcError, ...
    # helpers
    is_retryable_error, retry_on_overload,
)
```

### Codex / AsyncCodex constructor

```python
Codex(config: CodexConfig | None = None)
AsyncCodex(config: CodexConfig | None = None)
```

`CodexConfig` shape (dataclass):

```python
@dataclass(slots=True)
class CodexConfig:
    codex_bin: str | None = None              # custom binary path
    launch_args_override: tuple[str, ...] | None = None  # bypass default args
    config_overrides: tuple[str, ...] = ()    # ← MCP CONFIG GOES HERE
    cwd: str | None = None
    env: dict[str, str] | None = None         # ← CODEX_HOME via this
    client_name: str = "codex_python_sdk"
    client_title: str = "Codex Python SDK"
    client_version: str = SDK_VERSION
    experimental_api: bool = True
```

`config_overrides` is a tuple of strings, each a `key=value` pair in
codex CLI's `--config` TOML-literal format. Examples:

```python
config_overrides=(
    'mcp_servers.lark_module.url="http://localhost:7831/mcp"',
    'sandbox_mode="danger-full-access"',
    'model_reasoning_summary="detailed"',
    'model="gpt-5.4-mini"',
)
```

This replaces our v1 `_codex_config_toml_builder` filesystem write.
No more `$CODEX_HOME/config.toml`.

### Thread / AsyncThread

```python
codex.thread_start(*,
    approval_mode: ApprovalMode = ApprovalMode.auto_review,
    base_instructions: str | None = None,      # ← our system prompt
    config: dict | None = None,                # per-thread TOML override
    cwd: str | None = None,
    developer_instructions: str | None = None,
    ephemeral: bool | None = None,             # session vs persistent
    model: str | None = None,
    model_provider: str | None = None,
    personality: Personality | None = None,
    sandbox: Sandbox | None = None,
    service_name: str | None = None,
    service_tier: str | None = None,
    session_start_source: ThreadStartSource | None = None,
    thread_source: ThreadSource | None = None,
) -> Thread
```

Note: **no `mcp_servers` param**. MCP must come through
`CodexConfig.config_overrides` at the Codex level (not thread level).

### Thread methods

```python
thread.run(input: RunInput, *, ...) -> TurnResult              # buffered
thread.turn(input: RunInput) -> TurnHandle                     # streaming entry
thread.compact()                                               # context compaction
thread.read()                                                  # state inspection
thread.set_name(name)                                          # naming
thread.id                                                      # property
```

**`Thread.run_streamed` does not exist** in the official SDK. Streaming
is via `thread.turn()` → `TurnHandle.stream()`. This is different from
the wrong-package spike that suggested `run_streamed`.

### TurnHandle (the streaming entry point)

```python
@dataclass(slots=True)
class TurnHandle:
    _client: CodexClient
    thread_id: str
    id: str

    def stream(self) -> Iterator[Notification]:
        """Yield notifications routed to this turn."""

    def run(self) -> TurnResult:
        """Consume stream, return final result."""

    def steer(self, input: RunInput) -> TurnSteerResponse:
        """Mid-turn: add more input WITHOUT cancelling the current turn."""

    def interrupt(self) -> TurnInterruptResponse:
        """← CANCELLATION PRIMITIVE. Server-side RPC interrupt."""
```

`interrupt()` is the cancellation path. **Not `asyncio.CancelledError`,
not `task.cancel()`**. This is a clean RPC call; the codex app-server
acknowledges and stops the turn at a sensible boundary. Our v1's
race-with-cancel + SIGTERM is replaced by this one method call.

### Notification types (from `openai_codex.models`)

The streaming events that flow through `TurnHandle.stream()`:

* Thread lifecycle: `ThreadStartedNotification`, `ThreadNameUpdatedNotification`
* Turn lifecycle: `TurnStartedNotification`, `TurnCompletedNotification`
* Item lifecycle: `ItemStartedNotification`, `ItemCompletedNotification`
* **Streaming text/reasoning deltas** (KEY UX WIN OVER v1):
  * `AgentMessageDeltaNotification` — assistant text streaming
  * `ReasoningTextDeltaNotification` — model reasoning streaming
  * `ReasoningSummaryTextDeltaNotification` — reasoning summary streaming
  * `ReasoningSummaryPartAddedNotification` — structured summary parts
  * `CommandExecutionOutputDeltaNotification` — bash output streaming
  * `FileChangeOutputDeltaNotification` — file change streaming
* MCP: `McpToolCallProgressNotification`, `McpServerOauthLoginCompletedNotification`
* Planning: `PlanDeltaNotification`, `TurnPlanUpdatedNotification`,
  `TurnDiffUpdatedNotification`
* Context mgmt: `ContextCompactedNotification`,
  `ThreadTokenUsageUpdatedNotification`
* Diagnostics: `ConfigWarningNotification`, `DeprecationNoticeNotification`,
  `WindowsWorldWritableWarningNotification`, `ErrorNotification`,
  `RawResponseItemCompletedNotification`, `TerminalInteractionNotification`
* Account: `AccountLoginCompletedNotification`,
  `AccountRateLimitsUpdatedNotification`, `AccountUpdatedNotification`
* App: `AppListUpdatedNotification`
* Unknown fallback: `UnknownNotification`

### Inputs

```python
TextInput(text: str)                # raw text — no type discriminator needed
ImageInput(...)                     # remote image URL
LocalImageInput(path: str)          # local file path
MentionInput(...)                   # @-mention?
SkillInput(...)                     # skill activation?
```

`thread.run(input)` and `thread.turn(input)` accept any single input
OR a `list[InputItem]`. Mixed text + image in one turn is supported.

---

## Design decisions for v2

### Coexistence (NOT replacement)

Register `CodexSDKv2` in `agent_loop_driver` registry alongside v1:

```python
# agent_framework/__init__.py
register_agent_loop_driver("codex_cli", CodexSDK)            # v1 stays default
register_agent_loop_driver("codex_cli_v2", CodexSDKv2)       # opt-in
register_agent_loop_driver("codex", CodexSDK)
register_agent_loop_driver("codex_official", CodexSDKv2)
```

User opts into v2 via `user_slots.agent_framework="codex_cli_v2"` or
env override `AGENT_LOOP_FRAMEWORK=codex_official`. Default stays v1
until v2 is proven stable in production.

### Module shape

New file: `src/xyz_agent_context/agent_framework/xyz_codex_official_sdk.py`

```python
class CodexSDKv2:
    """Codex CLI wrapper via the OFFICIAL openai-codex SDK.

    Subprocess + JSON-Lines parsing handed off to the SDK; we keep
    the NarraNexus-specific concerns:
      - per-run CODEX_HOME staging (for auth.json copy)
      - config_overrides assembly (replaces _codex_config_toml_builder)
      - MCP URL SSE→streamable rewrite
      - Permission policy translation
      - Notification → internal event translation
      - Cancellation through TurnHandle.interrupt
    """

    def __init__(self, working_path: str = "./"):
        self.working_path = working_path

    @timed("llm.codex_v2.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages,
        mcp_server_urls,
        *,
        streaming=True,
        extra_env=None,
        cancellation=None,
        **kwargs,
    ):
        # 1. Build base_instructions from messages
        # 2. Assemble config_overrides tuple (model_instructions still
        #    passes through a file because the SDK doesn't have a
        #    "base_instructions=long-string" option for very large
        #    prompts — falls back to model_instructions_file in
        #    config_overrides if base_instructions exceeds N kchars)
        # 3. Stage CODEX_HOME with auth.json copy
        # 4. Construct AsyncCodex with the assembled CodexConfig
        # 5. await codex.thread_start(...)
        # 6. handle = thread.turn(TextInput(text=user_message))
        # 7. async for notification in handle.stream():
        #      for translated in _translate_notification(notification):
        #          if cancellation and cancellation.is_set():
        #              handle.interrupt()
        #          yield translated
```

### What v1 functionality must survive in v2

These are NOT codex CLI internals — they're NarraNexus concerns and
remain v2's responsibility:

* `$CODEX_HOME` per-run tempdir + `auth.json` staging (for OAuth path
  — `Codex().login_chatgpt()` exists but uses interactive flow; for
  exec-style "use already-logged-in credentials", staging the file
  remains the path of least resistance)
* MCP URL rewrite (SSE → streamable HTTP) — NarraNexus emits both;
  the rewrite belongs to the wrapper
* `_codex_permission_translator` → emit translated rules as TOML
  literal strings in `config_overrides`
* `CodexConfig` ContextVar (`api_config.codex_config`) — model selection
  + base_url resolution flows the same; we just hand the resolved
  values into `CodexConfig.config_overrides` instead of writing a file
* `output_transfer` translation — extended for new Notification types
* All test fixtures still apply at the contract level (events flowing
  to `response_processor` look the same; only the upstream producer
  changes)

### `output_transfer` extensions for Notification → internal event

v2 must translate Notification objects to the same shape v1 produces.
Mapping table (notification → `ResponseType` or internal event):

| Notification                              | Internal event type            |
| ----------------------------------------- | ------------------------------ |
| ThreadStartedNotification                 | (drop or info-only)            |
| TurnStartedNotification                   | (drop or info-only)            |
| TurnCompletedNotification                 | `response.done` + usage        |
| ItemStartedNotification (mcp_tool_call)   | `tool_call_item` (started)     |
| ItemCompletedNotification (mcp_tool_call) | `tool_call_output_item`        |
| ItemCompletedNotification (command_exec)  | `tool_call_output_item` (Bash) |
| AgentMessageDeltaNotification             | `response.text.delta`          |
| ReasoningTextDeltaNotification            | `thinking_delta`               |
| ReasoningSummaryTextDeltaNotification     | `thinking_delta`               |
| CommandExecutionOutputDeltaNotification   | inline tool_output update?     |
| McpToolCallProgressNotification           | tool_call progress update?     |
| ContextCompactedNotification              | (log + info-only)              |
| ErrorNotification                         | `response.error`               |

The streaming reasoning deltas are the headline win — they replace
v1's "Thinking panel appears all at once" with progressive rendering
matching DeepSeek's UX.

### Cancellation flow

v1: `_drain_stderr` + race-with-cancel + SIGTERM 5s grace + SIGKILL.

v2: keep the cancellation token concept (NarraNexus-side), but the
implementation is a single `handle.interrupt()` call inside the
streaming loop:

```python
async for notification in handle.stream():
    if cancellation and cancellation.is_set():
        await asyncio.to_thread(handle.interrupt)  # interrupt is sync RPC
        break
    yield from _translate_notification(notification)
```

Latency goal: interrupt-to-final-event under 1s (vs v1's < 5s).
`TurnHandle.interrupt` is a synchronous RPC call to the local
app-server, so it's effectively immediate — we just need to drain a
few more "post-interrupt" notifications then break.

### Sync vs async client

The official SDK has both `Codex` (sync) and `AsyncCodex` (async).
NarraNexus is async-first (FastAPI + asyncio everywhere), so v2 must
use `AsyncCodex`.

Caveat: `TurnHandle.stream()` returns `Iterator[Notification]`
(synchronous). If `AsyncTurnHandle` has an async iterator, prefer it;
otherwise wrap in `asyncio.to_thread`. Spike Section A will resolve
this.

---

## What v1 does that the SDK does NOT (yet) handle

* **`raw event log` diagnostic**: v1's `[CodexSDK][raw]` INFO log lets
  us see exactly what codex emitted. SDK's Notification objects are
  Pydantic models — we can dump them to JSON for the same purpose, but
  the log line format will differ. Re-implement as a v2 helper.
* **`--ignore-user-config` semantics**: v1 explicitly does NOT pass
  this flag (we want our config to be read). SDK abstracts this; need
  to verify the SDK passes our `config_overrides` correctly and does
  NOT inherit the user's `~/.codex/config.toml`. Mitigated by
  overriding `CODEX_HOME` to a per-run temp dir.
* **`stderr` capture**: v1 reads codex CLI stderr line-by-line and
  logs WARNING for each. SDK swallows stderr into the underlying
  client. Need to check whether errors surface as Notification
  (probably `ConfigWarningNotification` / `ErrorNotification`) or
  remain invisible.

---

## Open questions (TBD during v2 spike Section A/B)

1. Does `config_overrides=('mcp_servers.X.url="..."',)` actually wire
   MCP through correctly? Highly likely yes (it's the same --config
   passthrough we already verified at CLI level), but unproven via
   SDK.
2. `TurnHandle.stream()` is sync iterator. Does `AsyncTurnHandle`
   exist with async iterator? If yes, use that. If no, wrap in
   `asyncio.to_thread`.
3. How does `TurnHandle.interrupt()` propagate through the stream?
   Does it terminate cleanly with `TurnCompletedNotification` having
   `status=interrupted`, or does the stream just stop mid-flow?
4. Does the SDK have any built-in retry on transient errors (rate
   limits / network blips)? `retry_on_overload` is exported; check
   default behavior.
5. Does `Codex(config=CodexConfig(env={"CODEX_HOME": "..."}))` actually
   isolate per-run? Or does the env get merged with parent process
   env in a way that lets the user's `~/.codex/config.toml` leak in?

---

## Migration timeline (no human-day estimates per binding rule #17)

* **Phase 0 (done)**: this design doc + community-spike archive on
  `feat/codex-sdk-05-29`.
* **Phase 1 (next branch)**: `feat/codex-sdk-v2` —
  * write `xyz_codex_official_sdk.py` (CodexSDKv2 class)
  * extend `output_transfer.py` with notification → event translation
    table
  * register v2 in `agent_loop_driver`
  * spike resolves the 5 open questions above
  * unit tests: notification translation, config_overrides assembly
* **Phase 2**: in-process A/B —
  * v1 remains the default `codex_cli` driver
  * v2 available via `agent_framework=codex_cli_v2` or env override
  * dogfood internally for some period
* **Phase 3**: cutover —
  * swap default: `codex_cli` registry name now maps to v2
  * v1 keeps its name `codex_cli_legacy` for rollback
  * after some uptime without rollback, delete v1

---

## What we got wrong on the way here

Documented so the next person doesn't repeat:

1. **"Official SDK doesn't exist" — wrong.** It does at
   `github.com/openai/codex/tree/main/sdk/python`, published as
   `openai-codex` on PyPI. I confused `openai-codex-sdk` (community
   fork) with the official package and spent hours analyzing the wrong
   API surface.
2. **"`pip install openai-codex-sdk` is the official command" — wrong.**
   The right command is `pip install openai-codex`. The "-sdk" suffix
   is the COMMUNITY fork's namespace.
3. **"`Codex().start_thread()` is the API" — wrong (for the official).**
   Community fork uses `start_thread`; official uses `thread_start`.
4. **"`TextInput(type='text', text=...)` requires a discriminator
   field" — wrong (for the official).** That was a quirk of the
   community fork's pydantic models. Official `TextInput(text=...)` is
   the simple shape.
5. **"`run_streamed()` returns a streaming async iter" — wrong (for
   the official).** Official SDK uses `thread.turn() → TurnHandle.stream()`,
   no `run_streamed`. Architecturally different: JSON-RPC app-server
   not `codex exec` JSON Lines.
6. **"Codex CLI rejects all reasoning models except gpt-5.4-codex" —
   wrong.** Initial test that returned "model not supported" was OAuth
   tier rejecting NetMind-aggregator format request; the actual
   curated set per the CLI's own `codex` interactive picker is
   `gpt-5.5 / gpt-5.4 / gpt-5.4-mini` — those work on both OAuth and
   API key.
7. **"`config_overrides` is the v2 way; v1 must keep config.toml
   forever" — partially wrong.** SDK ALSO writes the config to disk
   internally; `config_overrides` is sugar for `--config k=v` flags
   passed at launch. So v1's config.toml builder isn't deeply wrong —
   it just expresses the same intent through a different surface.

Bottom line: future-me / new contributor reading this — **start from
the official `openai-codex` docs**, NOT from PyPI search.
