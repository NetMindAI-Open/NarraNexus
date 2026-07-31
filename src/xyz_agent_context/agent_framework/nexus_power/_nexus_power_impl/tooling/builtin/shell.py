"""
@file_name: shell.py
@author: Bin Liang
@date: 2026-07-29
@description: Command execution — ``bash`` (v1), with ``bash_background``
and ``process`` seats (P3, schema-honest: ungated groups never list
them, they are never registered-but-dead).

The subprocess runs with the workspace as cwd and its own process
group; timeout kills the whole group (orphan subprocesses are a known
incident class). Output is size-capped with explicit markers.
"""

from __future__ import annotations

import asyncio
import os
import signal

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolAnnotations,
    ToolContext,
    ToolResult,
    ToolSpec,
)

_DEFAULT_TIMEOUT_S = 120
_MAX_TIMEOUT_S = 600

#: Host variables the agent's shell inherits. An ALLOWLIST, because the
#: obvious `{**os.environ}` hands the model every credential the host
#: process happens to hold — provider keys, DB password, master secret —
#: and one `env` reads them all (2026-07-29 review; iron rule #20 draws
#: the line at scoped credentials). These entries are what makes a shell
#: a working shell rather than what makes it privileged: without PATH
#: nothing resolves, without HOME git and every CLI misbehave, without
#: the locale set output mangles non-ASCII.
#:
#: The turn's OWN scoped values arrive separately via ``ctx.extra_env``
#: and are meant to be there. Anything an agent legitimately needs is
#: added here deliberately, one name at a time.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
)


def _shell_env(ctx: ToolContext) -> dict[str, str]:
    """The environment the agent's shell actually gets."""
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env.update(ctx.extra_env)
    return env
_MAX_OUTPUT_CHARS = 60_000


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="bash",
            description=(
                "Run a shell command in the workspace (cwd is the workspace "
                "root). Returns combined stdout/stderr and the exit code. "
                f"Default timeout {_DEFAULT_TIMEOUT_S}s (max {_MAX_TIMEOUT_S}s); "
                "on timeout the whole process group is terminated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_TIMEOUT_S,
                    },
                },
                "required": ["command"],
            },
            annotations=ToolAnnotations(destructive=True),
        ),
    ]


async def bash(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    command = str(args.get("command", "")).strip()
    if not command:
        return ToolResult(call_id=call_id, ok=False, error="empty command")
    timeout = min(int(args.get("timeout_s") or _DEFAULT_TIMEOUT_S), _MAX_TIMEOUT_S)
    env = _shell_env(ctx)

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=ctx.workspace,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # own process group → clean group kill
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_group(process.pid)
        await process.wait()
        return ToolResult(
            call_id=call_id,
            ok=False,
            error=f"command timed out after {timeout}s (process group terminated)",
        )

    text = (stdout or b"").decode("utf-8", errors="replace")
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + f"\n[truncated {len(text) - _MAX_OUTPUT_CHARS} chars]"
    body = f"{text}\n[exit code: {process.returncode}]" if text else f"[exit code: {process.returncode}]"
    return ToolResult(call_id=call_id, ok=process.returncode == 0, content=body,
                      error=None if process.returncode == 0 else f"exit code {process.returncode}")


def _kill_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


HANDLERS = {"bash": bash}
