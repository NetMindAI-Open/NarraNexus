---
code_file: src/xyz_agent_context/agent_framework/_codex_config_toml_builder.py
stub: false
last_verified: 2026-06-10
---
## 2026-06-10 — neutral reasoning_effort → model_reasoning_effort

Maps the slot's neutral reasoning_effort into Codex dialect: low/medium/high
pass through, "max" clamps to "high" (log, never error — rules #9/#15);
"" emits nothing so the CLI keeps its default. Neutral `thinking` has no
Codex equivalent → debug-logged and ignored. Distinct from the existing
`model_reasoning_summary="detailed"` (display verbosity, not effort).


## Why it exists

Codex CLI reads its full configuration from a single ``config.toml``
file at ``$CODEX_HOME/config.toml``. There is no command-line
equivalent for declaring MCP servers, custom model providers, or
permission profiles — they have to be in TOML. The
``xyz_codex_cli_sdk`` wrapper writes a fresh ``config.toml`` into a
per-run temp directory before every spawn, populated from the
runtime inputs (MCP URLs, CodexConfig, permission spec).

This builder is the pure-function helper that produces that TOML
string. Kept separate from the wrapper so the toml shape can be
unit-tested without spawning a subprocess.

## Design decisions

- **Stdlib only.** No ``tomli_w`` / ``toml`` dep. The output is
  small (typically < 2 KB) and the TOML subset we need is trivial:
  scalar key=value lines + table headers + inline string-list
  arrays. We use ``json.dumps`` to render string values because
  JSON basic strings are a strict subset of TOML basic strings —
  one-liner escaping, no manual ``\n`` / ``"`` handling.
- **Deterministic key order.** Tables are sorted by name (e.g.
  MCP server entries by server name, permission filesystem entries
  by path) so two runs with the same inputs produce byte-identical
  output. Useful for test snapshots and log diffs.
- **Skip empty sections.** No MCP servers → no ``[mcp_servers.*]``
  tables. No ``config.base_url`` → no ``[model_providers.*]`` block.
  The output is always a valid TOML doc, even with minimal inputs.
- **Constant provider name (``narranexus``)** for the custom
  model_provider. Hardcoding the table key keeps the TOML
  predictable; if we ever need per-agent provider names, the
  builder takes a parameter.
- **``model_provider`` is set ONLY when ``base_url`` is non-empty.**
  Otherwise Codex falls back to its bundled OpenAI provider, which
  is the right default for users who login via ``codex login``.
- **MCP servers use the HTTP transport form.** NarraNexus MCP
  servers expose ``http://localhost:780X/sse`` endpoints (no auth,
  localhost only). We do NOT emit ``bearer_token_env_var`` because
  there's no token to use. Future stdio-based MCP servers would
  need a different branch.

## Upstream / downstream

- **Upstream**: ``xyz_codex_cli_sdk.CodexSDK.agent_loop`` calls
  this once per run, then writes the result to
  ``<temp>/config.toml``.
- **Downstream**: Codex CLI reads the TOML at process start when
  ``$CODEX_HOME`` is set to the temp directory and
  ``--ignore-user-config`` skips the user's ``~/.codex/config.toml``.

## Gotchas

- **TOML basic string escaping.** Don't ``f"\"{value}\""`` — control
  chars and embedded quotes need escaping. Always go through
  ``_toml_str()`` which delegates to ``json.dumps``.
- **Glob keys MUST be quoted.** Permission filesystem entries
  (``/etc/**``, ``"workspace/path"``) and command patterns
  (``"sudo *"``) contain ``/``, ``*``, and spaces. The builder
  passes ``quote_key=True`` for these — bare ``key = value`` would
  produce invalid TOML.
- **No nested table consolidation.** We emit each sub-table
  separately (``[permissions.narranexus.filesystem]``,
  ``[permissions.narranexus.commands]``) rather than merging into
  one ``[permissions.narranexus]`` block with inline tables. Easier
  to read in the generated file, easier to grep in logs.
- **``[sandbox_workspace_write]`` writable_roots is the ONLY
  override of ``--sandbox`` mode.** Codex CLI flag wins for
  read-only/write/danger choice; this TOML block extends the set
  of allowed roots beyond the cwd. Mismatched ``--sandbox read-only``
  on the CLI plus ``writable_roots`` here would silently leave
  things read-only.
- **Default sandbox is ``danger-full-access``, not
  ``workspace-write``.** Forced by codex CLI 0.135 issue #16685:
  under ``read-only`` / ``workspace-write``, every MCP tool call
  in ``codex exec`` mode is auto-cancelled with ``"user cancelled
  MCP tool call"`` because codex hits an approval-elicitation path
  that exec mode can't respond to. Only ``danger-full-access``
  lets MCP work. Wrapper-level callers and CLI-level
  ``--sandbox`` flag MUST agree (CLI wins on mismatch). Revisit
  the default if #16685 ever ships a real fix.
- **Constant key name ``CODEX_API_KEY`` for ``env_key``.** Keep
  aligned with ``CodexConfig.to_cli_env`` — both reference the
  same env var. Diverging means "key set but never read" bugs.
- **``model_reasoning_summary = "detailed"``** — codex CLI's
  ``exec`` default is ``none``，导致 reasoning model（gpt-5.5 /
  gpt-5.4 / gpt-5.4-mini）跑完之后**完全不发 `reasoning` item**，
  前端的 Thinking 面板永远空。2026-06-04 跑了 `codex exec --json`
  实测：``none`` 无 reasoning item；``concise`` 只给 header 字符串；
  ``detailed`` 给完整自然语言段落（"The user wants me to... I'll
  express it as 17 × (20 + 3)... 391"）。我们走 ``detailed``，跟
  DeepSeek R1 的 CoT 体验对齐——OpenAI 仍然 gate 完整 CoT，但
  detailed summary 至少给一段人话。Token 成本+30-200 / turn。
