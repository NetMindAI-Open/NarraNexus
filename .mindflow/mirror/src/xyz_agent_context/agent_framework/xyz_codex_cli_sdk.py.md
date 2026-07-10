---
code_file: src/xyz_agent_context/agent_framework/xyz_codex_cli_sdk.py
stub: false
last_verified: 2026-07-09
---

## 2026-07-09 (P0 fix) — bump subprocess StreamReader line limit to 50 MiB

Companion to the [[remote_agent_loop_driver.py]] LineTooLong fix. Same
class of bug, one hop upstream: `asyncio.create_subprocess_exec` without
an explicit `limit=` kwarg uses `asyncio.streams._DEFAULT_LIMIT = 65536`
(64 KiB). Once a codex CLI NDJSON event line exceeds it,
`StreamReader.readline` raises `ValueError: Separator not found, and
chunk exceed the limit`. The ceiling is HALF of aiohttp's (64 KiB vs
128 KiB), so `framework="codex"` agents reading large tool outputs
(images, big MCP payloads) would crash inside the executor process
before the event even reached the HTTP hop.

Fix: pass `limit=_STDOUT_LINE_LIMIT` (50 MiB, matching the Claude SDK's
`max_buffer_size`) to `create_subprocess_exec`. Also new module
constant so tests and future readers can see the ceiling without
grepping for the magic number.

v1 is currently a revival fallback (not registered), but per binding
rule #8 we sweep adjacent code — leaving this bug in a "revival" path
would just re-set the same trap if the wrapper is reactivated.

Regression: `tests/agent_framework/test_codex_cli_stdout_line_limit.py`
locks that the subprocess spawn passes the 50 MiB limit and that a
`StreamReader` at that limit can readline a 200 KiB line intact.

## 2026-06-17 — 安全修复:env 改白名单(不再全量注入)

Step 3 的 `full_env = {**os.environ, **cli_env}` 把 backend 全部环境(含
平台密钥)注入了 codex 子进程。改为调用
`_codex_env.build_codex_subprocess_env`(与 v2 共用),只透传最小系统白
名单 + `CODEX_HOME` / `NO_PROXY` / scoped `CODEX_API_KEY`。v1 虽是 revival
fallback、当前不注册,但一旦被拉回活跃路径必须同样安全,故一并修。详见
`_codex_env.py.md`。

## 2026-06-17 — PR #25 评审收尾(v1 现为 revival fallback,不注册)

v1 已不在活跃路径——`__init__.py` 只注册 v2(`CodexSDKv2` → `codex_cli`),
本文件保持可 import 作为 revival fallback。两处清理(无行为变更):

- **铁律 #1**:`_idle` 分支注释 + "received 0 lines of output" 诊断串原为
  中文,翻译成英文。
- **system prompt 指纹降级**:head/tail 两行(回显 prompt 文本)从 INFO
  降到 DEBUG,与 v2 一致;size+path 行保持 INFO。

## Why it exists

OpenAI Codex CLI wrapper — the second coding-agent SDK NarraNexus
supports, alongside Claude Code. Same async-generator contract as
``ClaudeAgentSDK`` so ``step_3_agent_loop`` can swap one for the
other based on the per-user ``user_slots.agent_framework`` choice
without any caller change.

Conceptual position: where ``ClaudeAgentSDK`` is a wrapper around
the official ``claude_agent_sdk`` Python SDK (which itself wraps
``claude`` CLI subprocess), this file wraps ``codex exec --json``
directly because **no official Codex Python SDK exists**. All the
heavy lifting (subprocess lifecycle, JSON Lines parsing,
cancellation race, SIGTERM/SIGKILL fallback) lives here.

## Design decisions

- **Spawn ``codex exec --json -`` directly.** No Python SDK to wrap.
  The ``-`` argument makes Codex read the prompt from stdin; we
  write the per-turn user message and close stdin to signal EOF
  (without the close, Codex blocks forever).
- **Per-run ``$CODEX_HOME`` temp directory.** Every call writes a
  fresh ``config.toml`` + ``instructions.md`` into a fresh
  ``tempfile.TemporaryDirectory``. Overriding ``CODEX_HOME`` to the
  temp dir is what isolates us from the user's
  ``~/.codex/config.toml`` — Codex only reads
  ``$CODEX_HOME/config.toml``. We do **NOT** pass
  ``--ignore-user-config``; that flag's behaviour is to skip
  ``$CODEX_HOME/config.toml`` (regardless of where $CODEX_HOME
  points), which would silently drop our MCP servers, custom
  provider, and permissions and force the agent back into bare-Bash
  mode. OAuth is the exception that proves the rule: when
  `CodexConfig.auth_ref` points at a host `codex login` auth file,
  the wrapper copies that file into the temp `CODEX_HOME` as
  `auth.json` before spawning the subprocess.
- **MCP via file, not dict.** Codex requires ``[mcp_servers.<name>]``
  TOML tables; we generate them in
  ``_codex_config_toml_builder.build_codex_config_toml``. Adding /
  removing MCP servers between turns is fine — each turn writes a
  fresh config.toml.
- **System prompt via file, not argv.** Codex reads
  ``model_instructions_file`` from config.toml, so there is no
  argv-byte ceiling like CC's 128 KiB limit. The source-aware
  history-eviction logic from CC is preserved (token budget still
  matters) but the byte-cap belt-and-braces is dropped.
- **Race-with-cancel JSON Lines receive loop.** Exact same pattern
  as ``xyz_claude_agent_sdk.py`` lines 393-451: wait on the next
  stdout line and the cancellation token simultaneously,
  ``return_when=FIRST_COMPLETED``. Sub-100ms cancellation latency
  even during a long-running tool call.
- **SIGTERM → 5s grace → SIGKILL fallback** on cleanup. Mirror CC
  wrapper. Codex CLI doesn't document its SIGTERM behaviour
  precisely; in practice it exits cleanly within ~1s, but the
  SIGKILL is the safety net.
- **Event translation via ``output_transfer(transfer_type="codex_cli")``**.
  The translation table is in ``output_transfer.py``; this file
  just yields the post-translation events directly to step_3.
  Tool-call dedupe (``seen_tool_call_ids``) mirrors CC's pattern
  for the started/completed event pair.
- **Tool-policy via config.toml ``[permissions]``, not per-call
  hook.** Codex has no PreToolUse hook API. The
  ``_codex_permission_translator`` module renders the CC tool-policy
  rules into Codex's declarative TOML form. Some dynamic checks (e.g.
  ``Path.resolve(strict=False)`` symlink-escape detection from CC's
  ``_tool_policy_guard.py``) cannot be transcribed; we lean on the
  per-agent ``working_path`` workspace directory as the primary
  sandbox boundary instead.
- **``--sandbox danger-full-access`` is REQUIRED for MCP.** codex
  CLI 0.135 ``exec`` mode auto-cancels every MCP tool call with
  ``"user cancelled MCP tool call"`` under ``read-only`` /
  ``workspace-write`` (issue #16685 — the call hits an approval-
  elicitation path that exec mode can't respond to). Only
  ``danger-full-access`` bypasses that. The downside — losing
  kernel-level filesystem isolation — is absorbed by NarraNexus's
  application-layer guards: ``working_path`` is a per-agent
  workspace, and ``[permissions]`` still denies ``/etc/**`` /
  ``/root/**`` / ``sudo *`` / global installs. Do NOT downgrade
  back to ``workspace-write`` without re-checking issue #16685
  has actually closed.

## Upstream / downstream

- **Upstream**: ``agent_runtime._agent_runtime_steps.step_3_agent_loop``
  (via the ``_resolve_agent_framework_sdk`` dispatcher).
- **Downstream**:
  - ``_codex_config_toml_builder.build_codex_config_toml`` — writes
    config.toml content.
  - ``_codex_permission_translator.translate_tool_policy_to_codex_permissions``
    — translates CC tool-policy to Codex permissions.
  - ``output_transfer.output_transfer(transfer_type="codex_cli")``
    — translates each Codex JSON event to the
    OpenAI-Agents-SDK-style event shape ``response_processor``
    expects.
  - ``api_config.codex_config`` — reads ``model``, ``base_url``,
    ``api_key`` from the current task's ContextVar (set by the
    per-user resolver before step_3 fires).

## Gotchas

- **``codex`` binary must be on PATH.** ``shutil.which("codex")``
  gate at the top of ``agent_loop`` raises an actionable error
  message pointing at ``npm install -g @openai/codex``. We could
  bundle a default-PATH probe but the error message is the
  preferred surface — installing Codex is the user's responsibility.
- **Auth fallback uses staged `auth.json` inside temp `CODEX_HOME`**.
  Codex itself reads `$CODEX_HOME/auth.json` when `CODEX_API_KEY` is
  empty. Because NarraNexus uses a temp `CODEX_HOME` per run, the
  wrapper must copy the host `codex login` file there first; otherwise
  a logged-in host would still look unauthenticated to the subprocess.
- **JSON Lines might NOT be one-per-line if Codex flushes
  mid-line.** We parse each ``readline()`` result independently and
  drop non-JSON lines with a DEBUG log. Codex's ``--json`` does
  appear to flush per event in practice, but the defensive parse
  costs nothing.
- **Cancellation behaviour during ``subprocess.communicate``** —
  we do NOT use ``communicate()`` because it blocks until EOF.
  Instead each ``readline()`` is wrapped in a task that we can
  race against the cancellation token. Without this pattern, a
  user Stop while Codex is executing a long Bash command would
  not be acted on until Codex finished.
- **Sandbox is ``danger-full-access`` by necessity** (see design
  decisions above). The kernel-level filesystem isolation we had
  with ``workspace-write`` is GONE — a misbehaving model that
  bypasses the ``[permissions]`` glob can in principle touch any
  user-readable path. Mitigations: per-agent ``working_path``,
  the ``[permissions]`` deny patterns, and the model being on a
  short leash via the system prompt. If issue #16685 ever lands a
  real fix, downgrade back to ``workspace-write`` as a single-line
  config change.
- **No Python SDK = no monkey-patching seams.** The CC wrapper
  monkey-patches ``claude_agent_sdk._internal.message_parser`` to
  tolerate unknown message types. Codex's CLI is the wire format,
  so any "unknown type" survives as raw JSON we either translate
  or drop with a DEBUG log — no patch needed.
- **Tool policy guard semantic loss.** The CC ``_tool_policy_guard.py``
  uses Python ``re.compile`` patterns with shell-style anchoring
  (``(?:^|[\\s;&|`$(])``); Codex globs cannot replicate that. The
  translation in ``_codex_permission_translator`` covers the common
  cases (``brew install *`` / ``sudo *`` / etc.) but misses
  obscure shell pipelines. If you observe agents bypassing the
  guard via ``echo brew install x | bash`` or similar, escalate by
  tightening ``--sandbox`` mode or adding more deny patterns.
