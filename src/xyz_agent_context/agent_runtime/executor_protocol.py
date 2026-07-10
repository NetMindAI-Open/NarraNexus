"""
@file_name: executor_protocol.py
@author:
@date: 2026-06-17
@description: Wire format for the agent-loop executor boundary.

The agent-loop (step 3 of the 7-step pipeline) is the ONLY place that
spawns the claude/codex CLI. Extracting it into a separate "executor"
service means that boundary must be crossed over the network instead of
an in-process call. This module is the shared (de)serialization for that
boundary, used by BOTH ends:

  * orchestrator side: ``RemoteAgentLoopDriver`` builds the request
    (incl. a snapshot of the resolved provider configs, which normally
    travel via ContextVar and therefore would NOT survive a network hop).
  * executor side: ``executor_service`` rebuilds the configs, re-applies
    them via ``api_config.set_user_config``, runs the LOCAL driver, and
    streams raw event dicts back.

Keeping this in the core package (not backend/) so both the executor
service entrypoint and the driver can import it without a backend dep.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
    snapshot_user_config,
)

# Maps the snapshot keys to their dataclass types for reconstruction.
_CONFIG_TYPES = {
    "claude": ClaudeConfig,
    "openai": OpenAIConfig,
    "codex": CodexConfig,
    "anthropic_helper": AnthropicHelperConfig,
    "cli_helper": CliHelperConfig,
}


def serialize_provider_configs() -> dict[str, Optional[dict]]:
    """Snapshot the current task's resolved provider configs as plain dicts.

    Called on the orchestrator side (which ran the provider resolver).
    ``None`` entries are preserved so the executor reproduces the exact
    same ContextVar state (e.g. anthropic_helper unset).
    """
    snap = snapshot_user_config()
    out: dict[str, Optional[dict]] = {}
    for key, cfg in snap.items():
        out[key] = dataclasses.asdict(cfg) if cfg is not None else None
    return out


def apply_provider_configs(payload: dict[str, Optional[dict]]) -> None:
    """Rebuild provider configs from the wire payload and set ContextVars.

    Called on the executor side before running the driver, so the SDK's
    ``to_cli_env`` resolves the same scoped credentials the orchestrator
    chose — without the executor ever touching the DB or the resolver.
    """
    def _build(key: str):
        raw = payload.get(key)
        if raw is None:
            return None
        return _CONFIG_TYPES[key](**raw)

    set_user_config(
        claude=_build("claude") or ClaudeConfig(),
        openai=_build("openai") or OpenAIConfig(),
        codex=_build("codex"),
        anthropic_helper=_build("anthropic_helper"),
        cli_helper=_build("cli_helper"),
    )


def build_agent_loop_request(
    *,
    framework: str,
    working_path: str,
    messages: list[dict[str, Any]],
    mcp_server_urls: dict[str, str],
    extra_env: Optional[dict[str, str]],
    streaming: bool = True,
) -> dict[str, Any]:
    """Assemble the JSON body for ``POST /agent-loop``.

    ``cancellation`` is intentionally NOT serialized — the orchestrator
    cancels by aborting the HTTP stream; the executor observes client
    disconnect. Provider configs are snapshotted here so the scoped creds
    cross the boundary explicitly (they normally ride a ContextVar).
    """
    return {
        "framework": framework,
        "working_path": working_path,
        "messages": messages,
        "mcp_server_urls": mcp_server_urls,
        "extra_env": extra_env,
        "streaming": streaming,
        "provider_configs": serialize_provider_configs(),
    }
