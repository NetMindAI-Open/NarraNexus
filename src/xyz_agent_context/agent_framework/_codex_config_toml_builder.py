"""
@file_name: _codex_config_toml_builder.py
@date: 2026-05-29
@description: Per-run ``$CODEX_HOME/config.toml`` builder.

Codex CLI reads its full configuration from a single TOML file at
``$CODEX_HOME/config.toml``. Unlike Claude Code (which takes
``--system-prompt`` as a CLI flag and accepts ``mcp_servers`` as a
Python dict at SDK call time), Codex requires:

  * The system prompt to be in a file referenced by
    ``model_instructions_file``.
  * MCP servers declared as ``[mcp_servers.<name>]`` tables.
  * Custom OpenAI-compatible providers declared as
    ``[model_providers.<name>]`` tables.
  * Sandbox + permissions as further top-level tables.

The CodexSDK wrapper writes a fresh ``config.toml`` inside a
per-run temp directory and sets ``CODEX_HOME=<temp>``. With
``codex exec --ignore-user-config`` this guarantees the agent run
sees ONLY our config — the user's home ``~/.codex/config.toml`` is
not loaded.

Design constraints
------------------
* **Stdlib-only.** We don't add a ``toml`` package dep — the file
  we emit is small and predictable. We use ``json.dumps`` to encode
  string values because JSON basic strings happen to be valid TOML
  basic strings, sidestepping the manual-escape rabbit hole.
* **Idempotent on empty input.** No MCP servers → no ``[mcp_servers.*]``
  tables. No custom base_url → no ``[model_providers.*]`` block. The
  output is always a valid TOML doc.
* **Deterministic key order.** Tables are emitted in a stable order
  so two runs with the same inputs produce byte-identical output
  (good for test snapshots, log diffs).
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from .api_config import CodexConfig


# Neutral SlotConfig.reasoning_effort -> Codex CLI's model_reasoning_effort.
# Codex accepts minimal|low|medium|high; the neutral "max" has no Codex
# level, so it clamps to "high" (adapter owns the dialect mapping and
# clamps out-of-vocabulary values with a log line, never an error —
# iron rules #9/#15). "" (auto) emits nothing so the CLI keeps its default.
_REASONING_EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}


# Provider name used in the TOML — kept stable so future telemetry /
# Codex logs reference a consistent identifier.
_CUSTOM_PROVIDER_NAME = "narranexus"
# Permission profile name. Mirrors the one in
# ``_codex_permission_translator`` output.
_PERMISSION_PROFILE_NAME = "narranexus"


def _toml_str(value: str) -> str:
    """Render a string as a valid TOML basic string.

    JSON strings are a strict subset of TOML basic strings — control
    chars, quotes, and backslashes are all escaped the same way. So
    ``json.dumps(value)`` is a safe one-liner for arbitrary content.
    """
    return json.dumps(value)


def _toml_table_header(name: str) -> str:
    """Emit a TOML table header line: ``[<name>]``."""
    return f"[{name}]"


def _kv_line(key: str, value: str | int | bool, *, quote_key: bool = False) -> str:
    """One ``key = value`` line. ``quote_key=True`` for table keys
    that contain special chars (paths, glob patterns)."""
    rendered_key = _toml_str(key) if quote_key else key
    if isinstance(value, bool):
        rendered_value = "true" if value else "false"
    elif isinstance(value, int):
        rendered_value = str(value)
    else:
        rendered_value = _toml_str(value)
    return f"{rendered_key} = {rendered_value}"


def build_codex_config_toml(
    *,
    instructions_path: Path,
    mcp_server_urls: dict[str, str],
    config: CodexConfig,
    permissions: dict,
    writable_roots: list[Path] | None = None,
    sandbox_mode: str = "danger-full-access",
) -> str:
    """Build the full per-run config.toml content as a string.

    Args:
        instructions_path: Path to the file holding system prompt +
            conversation history. Becomes ``model_instructions_file``.
            Must exist when the subprocess runs.
        mcp_server_urls: ``{name: url}`` map. Each entry becomes one
            ``[mcp_servers.<name>]`` HTTP table. URLs are NarraNexus's
            SSE endpoints (``http://localhost:780X/sse``); they have
            no auth.
        config: :class:`CodexConfig` carrying model + base_url. When
            ``base_url`` is set, a ``[model_providers.narranexus]``
            block is added and ``model_provider`` is set to that name.
            When empty, Codex uses its bundled OpenAI provider.
        permissions: Output of
            :func:`_codex_permission_translator.translate_tool_policy_to_codex_permissions`.
            Becomes ``[permissions.narranexus]`` + sub-tables.
        writable_roots: Extra writable directories beyond the cwd.
            Defaults to ``[]`` (cwd is implied at runtime).
        sandbox_mode: ``"read-only" | "workspace-write" | "danger-full-access"``.
            Default ``"danger-full-access"`` is REQUIRED for MCP tool
            calls to succeed in ``codex exec`` mode — see codex issue
            #16685: under read-only / workspace-write, every MCP tool
            call is auto-cancelled with ``"user cancelled MCP tool
            call"`` because codex tries to elicit user approval and
            exec mode has no responder. NarraNexus's actual sandboxing
            comes from (a) the per-agent ``working_path`` workspace
            directory and (b) the ``[permissions]`` table (system-dir
            denies + dangerous-command denies); turning off codex's
            kernel sandbox is acceptable when those layers stand.

    Returns:
        The TOML content as a UTF-8 string ready to write to disk.
    """
    lines: list[str] = []

    # ---- 1. Top-level scalar settings --------------------------------------
    lines.append(_kv_line("model_instructions_file", str(instructions_path)))
    lines.append(_kv_line("sandbox_mode", sandbox_mode))
    # Request a human-readable reasoning summary for the UI's
    # "Thinking" panel. ``codex exec`` defaults to ``none`` which
    # leaves the panel empty — verified 2026-06-04 with
    # ``codex exec --json --config 'model_reasoning_summary="..."'``:
    #   none     → no reasoning item emitted at all
    #   concise  → reasoning item with header-only text
    #              (e.g. "**Breaking down multiplication steps**")
    #   detailed → reasoning item with full narrative paragraph
    # OpenAI gates the underlying chain-of-thought tokens regardless;
    # ``detailed`` is the most user-readable summary they expose.
    # Comparable to DeepSeek-R1's CoT — shorter, but the same kind
    # of content. Token cost: +30-200 output tokens per turn for the
    # summary itself.
    lines.append(_kv_line("model_reasoning_summary", "detailed"))
    # Neutral reasoning_effort from the agent slot -> Codex dialect.
    # (model_reasoning_summary above is display verbosity — a different
    # knob from effort; don't conflate.)
    if config.reasoning_effort:
        mapped_effort = _REASONING_EFFORT_MAP.get(config.reasoning_effort)
        if mapped_effort:
            if mapped_effort != config.reasoning_effort:
                logger.debug(
                    f"[CodexConfigToml] neutral reasoning_effort="
                    f"{config.reasoning_effort!r} clamped to {mapped_effort!r}"
                )
            lines.append(_kv_line("model_reasoning_effort", mapped_effort))
        else:
            logger.warning(
                f"[CodexConfigToml] unknown reasoning_effort "
                f"{config.reasoning_effort!r}; omitted (CLI keeps default)"
            )
    if config.thinking:
        # No Codex equivalent for the neutral thinking on/off switch —
        # reasoning models always reason; effort is the only dial.
        logger.debug(
            f"[CodexConfigToml] neutral thinking={config.thinking!r} has "
            f"no Codex equivalent; ignored"
        )
    if config.model:
        lines.append(_kv_line("model", config.model))

    if config.base_url:
        # Tell Codex to use our custom provider entry below.
        lines.append(_kv_line("model_provider", _CUSTOM_PROVIDER_NAME))

    lines.append("")  # blank line separator

    # ---- 2. Custom model provider (only when base_url is set) -------------
    if config.base_url:
        lines.append(_toml_table_header(f"model_providers.{_CUSTOM_PROVIDER_NAME}"))
        lines.append(_kv_line("name", "NarraNexus-configured provider"))
        lines.append(_kv_line("base_url", config.base_url))
        # Codex reads the API key from this env var. Always set to
        # ``CODEX_API_KEY`` because that's the env var ``to_cli_env``
        # populates — keeping the names aligned avoids a class of
        # "key set but never read" bugs.
        lines.append(_kv_line("env_key", "CODEX_API_KEY"))
        # ``responses`` is the OpenAI Responses API surface; ``chat``
        # is the legacy chat-completions one. Default to responses
        # because it's what Codex agents use natively; users with
        # legacy-only endpoints can override via NarraNexus config.
        lines.append(_kv_line("wire_api", "responses"))
        lines.append("")

    # ---- 3. MCP servers ---------------------------------------------------
    # Sort by name so output is deterministic across runs.
    for server_name in sorted(mcp_server_urls.keys()):
        url = mcp_server_urls[server_name]
        lines.append(_toml_table_header(f"mcp_servers.{server_name}"))
        lines.append(_kv_line("url", url))
        # NarraNexus MCP servers don't require auth — they bind to
        # localhost only. Do NOT emit ``bearer_token_env_var``.
        lines.append("")

    # ---- 4. Sandbox writable roots ----------------------------------------
    if writable_roots:
        lines.append(_toml_table_header("sandbox_workspace_write"))
        # Codex uses a list of strings; render via inline array.
        roots = [str(p) for p in writable_roots]
        rendered_roots = ", ".join(_toml_str(r) for r in roots)
        lines.append(f"writable_roots = [{rendered_roots}]")
        lines.append("")

    # ---- 5. Permissions ---------------------------------------------------
    if permissions:
        lines.append(_toml_table_header(f"permissions.{_PERMISSION_PROFILE_NAME}"))
        if permissions.get("extends"):
            lines.append(_kv_line("extends", permissions["extends"]))
        lines.append("")

        filesystem = permissions.get("filesystem") or {}
        if filesystem:
            lines.append(_toml_table_header(
                f"permissions.{_PERMISSION_PROFILE_NAME}.filesystem"
            ))
            for path in sorted(filesystem.keys()):
                # Path / glob keys MUST be quoted because they contain
                # ``/`` and ``*``.
                lines.append(_kv_line(path, filesystem[path], quote_key=True))
            lines.append("")

        commands = permissions.get("commands") or {}
        if commands:
            lines.append(_toml_table_header(
                f"permissions.{_PERMISSION_PROFILE_NAME}.commands"
            ))
            for pattern in sorted(commands.keys()):
                lines.append(_kv_line(pattern, commands[pattern], quote_key=True))
            lines.append("")

        tools = permissions.get("tools") or {}
        if tools:
            lines.append(_toml_table_header(
                f"permissions.{_PERMISSION_PROFILE_NAME}.tools"
            ))
            for tool in sorted(tools.keys()):
                lines.append(_kv_line(tool, tools[tool], quote_key=True))
            lines.append("")

        # Tell Codex to use this permission profile as the default for
        # this run.
        lines.append(_kv_line("default_permissions", _PERMISSION_PROFILE_NAME))

    # Trailing newline keeps text editors happy + makes diffs cleaner.
    return "\n".join(lines).rstrip() + "\n"
