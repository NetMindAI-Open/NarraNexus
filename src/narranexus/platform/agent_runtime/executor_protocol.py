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
import hashlib
import hmac
from typing import Any, Optional

from loguru import logger

from narranexus.platform.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
    snapshot_user_config,
)
from narranexus.platform.settings import settings

# Maps the snapshot keys to their dataclass types for reconstruction.
_CONFIG_TYPES = {
    "claude": ClaudeConfig,
    "openai": OpenAIConfig,
    "codex": CodexConfig,
    "anthropic_helper": AnthropicHelperConfig,
    "cli_helper": CliHelperConfig,
}

# NOTE (2026-07-29): the resume-auth block lived here — an HMAC over
# (resume_session_id, working_path, framework, issued_at), signed by the
# orchestrator and verified by the executor. It existed because a CLI session
# handle names a resource OUTSIDE the request, living in a CLAUDE_CONFIG_DIR
# shared by all tenants, so on an intentionally unauthenticated /agent-loop a
# guessed handle plus a guessable working_path would replay another tenant's
# conversation.
#
# No handle crosses this boundary anymore. The claude adapter writes the CLI
# transcript itself, inside the executor, and deletes it when the turn ends —
# so there is nothing durable on disk to replay and nothing to authorize.
# See agent_framework/adapters/claude/transcript.py.

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
        # Defensive forward-compat: reconstruct only the dataclass's KNOWN fields
        # so a field one end serializes that the other doesn't define can never
        # raise TypeError and fail the turn. (The broker already replaces stale-
        # IMAGE executors on ensure() via _is_stale, so the "new orchestrator →
        # old warm executor" window is narrow; this is belt-and-suspenders against
        # any residual skew — e.g. a same-image content change, or local mode.)
        # Adding identity_token to the three configs was the concrete trigger.
        cls = _CONFIG_TYPES[key]
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = [k for k in raw if k not in known]
        if unknown:
            # Not silent: a deploy skew this covers should still be visible.
            logger.warning(
                f"provider_configs[{key}] dropped unknown field(s) {unknown} — "
                "orchestrator/executor version skew (expected during a rolling deploy)"
            )
        return cls(**{k: v for k, v in raw.items() if k in known})

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
    mcp_servers: dict[str, dict],
    extra_env: Optional[dict[str, str]],
    streaming: bool = True,
    disallowed_tools: Optional[list[str]] = None,
    agent_id: str = "agent",
    expressive_tools: Optional[list[str]] = None,
    deferred_tools: Optional[list[str]] = None,
    turn_profile: Optional[dict[str, Any]] = None,
    extra_accessible_roots: Optional[list[str]] = None,
    origin_declaration: str = "",
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the JSON body for ``POST /agent-loop``.

    ``cancellation`` is intentionally NOT serialized — the orchestrator
    cancels by aborting the HTTP stream; the executor observes client
    disconnect. Provider configs are snapshotted here so the scoped creds
    cross the boundary explicitly (they normally ride a ContextVar).

    ``run_id`` (optional) makes the run STEERABLE: it is the correlation
    handle a later ``POST /steer`` uses to reach THIS run's inbound queue in
    the executor. It MUST be unguessable (the caller mints a
    ``secrets.token_hex``), because ``/steer`` is unauthenticated like
    ``/agent-loop``: an unguessable handle is what keeps a direct caller from
    injecting a message into another tenant's live turn — the same "the body
    names only a resource the caller already holds" property the module
    docstring guards. ``None`` (the default) = a non-steerable run, byte-for-
    byte the old body (the key is omitted, not sent null, so an OLD executor
    that never reads it is unaffected).
    """
    body: dict[str, Any] = {
        "framework": framework,
        "working_path": working_path,
        "messages": messages,
        "mcp_servers": mcp_servers,
        "extra_env": extra_env,
        "streaming": streaming,
        # Setup-residency: per-agent tool suppression must cross the network
        # boundary explicitly (it is per-run state, like the messages).
        "disallowed_tools": disallowed_tools or [],
        # Delivery declaration: the reply surface is per-run state too —
        # NexusPower inside the executor needs it to enforce the
        # monologue contract with the right tools.
        "agent_id": agent_id,
        "expressive_tools": expressive_tools or [],
        # Plugin tools kept out of the up-front list (searchable only); per-run
        # state like the two lists above.
        "deferred_tools": deferred_tools or [],
        # Per-turn fast-mode knobs — per-run state like the messages. The
        # whitelist body means a missing key is a silent cloud-side drop,
        # so the key is always present (None when no profile).
        "turn_profile": turn_profile or None,
        # Extra readable roots (the per-user `_shared` area) — per-run state
        # like the messages, and subject to the same silent-drop hazard noted
        # above, so the key is always present. Paths are orchestrator-side
        # absolutes; this is safe for the same reason `working_path` is: the
        # per-user Executor bind-mounts that same user subtree, so both sides
        # name it identically.
        "extra_accessible_roots": extra_accessible_roots or [],
        # Per-turn origin declaration (§6): the `[Origin] <label> · reply with
        # <tool>` line. Per-run state like the messages, and subject to the same
        # silent-drop hazard as the keys above, so the key is ALWAYS present
        # (empty string when the turn has no expressive surface). Without it the
        # whole §6 line never reaches the model on the cloud (RemoteAgentLoop)
        # path, which is every dev/prod turn.
        "origin_declaration": origin_declaration or "",
        "provider_configs": serialize_provider_configs(),
    }
    # Steerable only when a handle is supplied. Omit the key entirely otherwise
    # (not a null): an executor that predates /steer simply never sees it, and
    # the non-steerable body stays identical to before this field existed.
    if run_id is not None:
        body["run_id"] = run_id
    return body


#: The wire frame the runner already understands for one live injection (see
#: ``runner.parse_steer_line``): the provider message wrapped under ``"steer"``.
#: ``POST /steer``'s body is this same frame plus the ``run_id`` that names the
#: target run — so the executor unwraps ``steer`` and feeds it to that run's
#: inbound queue exactly as the local stdin transport does.
def build_steer_request(*, run_id: str, steer_msg: dict[str, Any]) -> dict[str, Any]:
    """The JSON body for ``POST /steer``: one injection for the live run
    ``run_id``. ``steer_msg`` is the already-rendered provider message (it may
    carry the private ``STEER_ID_KEY`` for consumption tracking — passed through
    verbatim, the executor's inlet strips it before the model sees it)."""
    return {"run_id": run_id, "steer": steer_msg}
