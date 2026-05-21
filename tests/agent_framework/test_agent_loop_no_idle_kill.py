"""
@file_name: test_agent_loop_no_idle_kill.py
@author: Bin Liang
@date: 2026-05-19
@description: `ClaudeAgentSDK.agent_loop` must NOT hard-kill the loop
on idle silence — agents legitimately think for minutes
(DeepSeek-V4-Pro CoT, large Bash commands, etc.). CLAUDE.md 铁律 #14:

  > 禁止给 agent_loop 加任何形式的硬性时间/迭代上限作为"修复方案"
  > （`max_iterations` / `max_duration` / `max_tool_calls` /
  > agent_loop 总超时）。需要兜底时只能加纯诊断用的 metrics + 告警，
  > 不能 force-stop。

Observed on EC2 jobs container 2026-05-19T04:16:35 (7 concurrent agents
all tripped at the same second). Without the fix, every CLI silence
≥ 600s killed the agent run.

The fix replaces the hard `raise TimeoutError(...)` with a soft path:
log a WARNING, check the subprocess is still alive, and continue
waiting. Only a *dead* subprocess (which is a genuine failure) bubbles
up as a real error.
"""
from __future__ import annotations

import inspect

from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    ClaudeAgentSDK,
)


def _agent_loop_source() -> str:
    return inspect.getsource(ClaudeAgentSDK.agent_loop)


def test_agent_loop_does_not_raise_timeouterror_on_idle():
    src = _agent_loop_source()
    assert "raise TimeoutError(" not in src, (
        "agent_loop must not raise TimeoutError on idle silence "
        "(铁律 #14 — no hard cap on agent_loop). Replace with a "
        "WARNING + subprocess-liveness check + continue."
    )


def test_agent_loop_keeps_warning_and_subprocess_check_for_idle():
    """Make sure the soft idle path stays present so a future refactor
    doesn't silently remove the observability we still owe operators."""
    src = _agent_loop_source()
    # WARNING-level log on idle (not exception)
    assert "logger.warning(" in src, (
        "Expected a logger.warning(...) call inside agent_loop for the "
        "idle-but-alive path."
    )
    # Subprocess liveness check (returncode is the canonical asyncio probe)
    assert "returncode" in src, (
        "Expected a subprocess `returncode` liveness check in agent_loop "
        "so we only abort when the CLI is actually dead."
    )
