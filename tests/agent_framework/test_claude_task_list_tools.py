"""
@file_name: test_claude_task_list_tools.py
@date: 2026-09-09
@description: The Claude Code CLI's task-LIST tools (TaskCreate / TaskGet /
TaskList / TaskUpdate) are wired to nothing on the platform: the list lives
only inside the CLI process. Under the SDK's headless (piped, non-TTY) spawn
the CLI's own gate already leaves the family off, so this is defence in
depth, not a behaviour change: the adapter pins the gate on EVERY run (any
provider, any auth) — CLAUDE_CODE_ENABLE_TASKS=false AFTER the skill env
merge (the CLI honours an explicit "true" ahead of its headless default)
plus the four names on disallowed_tools — and tells the model that its
in-run TodoWrite list is run-scoped too, that in-run background commands
keep working, and that work outliving the run goes through the Job module.
TodoWrite itself stays enabled (never disallowed).

Which names belong to the family is a fact about the CLI binary, not about
this file. The hand-written set below is checked against the bundled binary
whenever it is present (``_cli_binary``): the four names must be there, and
the two look-alikes that are NOT task-list tools — TaskOutput (the
BashOutput/AgentOutputTool tool) and TaskStop (KillShell) — must be recognised
as such. To re-verify by hand (minified names as of 2.1.56):

    B=$(python -c "import claude_agent_sdk,os;print(os.path.dirname(claude_agent_sdk.__file__))")/_bundled/claude
    grep -aoF 'isEnabled(){return T4()}' "$B" | wc -l    # 8 = 4 tools x 2 copies
    grep -aoF 'isEnabled(){return!T4()}' "$B" | wc -l    # 2 = TodoWrite (complement gate)
    grep -ao 'function T4(){.\{0,160\}' "$B" | head -1   # env off→false, env on→true, !interactive→false
    grep -ao 'function ZI(){.\{0,40\}' "$B" | head -1    # ZI = !isInteractive (--print/--sdk-url/!isTTY)
    grep -ao 'aliases:\["AgentOutputTool","BashOutputTool"\]' "$B"  # TaskOutput
    grep -ao 'aliases:\["KillShell"\]' "$B"                         # TaskStop

Each behavioural test goes red when the env injection / the disallow list /
the prompt notice is removed. The WebSearch guard is asserted alongside so
the family disallow cannot accidentally replace it (merge, never replace).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import narranexus_plugins.frameworks_claude_code.sdk as sdk_mod
from narranexus_plugins.frameworks_claude_code.prompts import task_list_tools_notice
from narranexus_plugins.frameworks_claude_code.sdk import (
    CLI_ENABLE_TASKS_ENV,
    TASK_LIST_TOOLS,
    ClaudeAgentSDK,
)
from narranexus.platform.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient

_TASK_LIST_TOOLS = {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}
# Same prefix, different feature: in-run background command tools.
_IN_RUN_BACKGROUND_TOOLS = {"TaskOutput", "TaskStop"}
# The model's own run-scoped checklist: named in the notice, never disabled.
_RUN_SCOPED_CHECKLIST_TOOL = "TodoWrite"


def _cli_binary() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    path = Path(os.path.dirname(claude_agent_sdk.__file__)) / "_bundled" / "claude"
    return path if path.is_file() else None


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _configure(*, supports_server_tools: bool) -> None:
    set_user_config(
        claude=ClaudeConfig(
            api_key="k",
            auth_type="api_key",
            supports_anthropic_server_tools=supports_server_tools,
        ),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "this turn input"},
    ]


async def _run_and_capture_options(**kwargs):
    sdk = ClaudeAgentSDK(working_path="/tmp/ws-task-list-tools")
    _ = [e async for e in sdk.agent_loop(_messages(), {}, **kwargs)]
    (client,) = _StubClient.instances
    return client.options


# ── the family itself ─────────────────────────────────────────────────────


def test_constant_is_exactly_the_task_list_family():
    assert set(TASK_LIST_TOOLS) == _TASK_LIST_TOOLS
    assert not (set(TASK_LIST_TOOLS) & _IN_RUN_BACKGROUND_TOOLS)
    assert "Task" not in TASK_LIST_TOOLS  # sub-agent launch stays allowed
    assert CLI_ENABLE_TASKS_ENV == "CLAUDE_CODE_ENABLE_TASKS"


def test_family_matches_the_bundled_cli_binary():
    """The names and the env gate are facts about the binary; check them
    there instead of against a second hand-written copy."""
    binary = _cli_binary()
    if binary is None:
        pytest.skip("claude-agent-sdk bundled binary not installed")
    blob = binary.read_bytes()
    for name in TASK_LIST_TOOLS:
        assert f'"{name}"'.encode() in blob, name
    assert CLI_ENABLE_TASKS_ENV.encode() in blob
    # The look-alikes are other tools (BashOutput / KillShell) — proof they
    # do not belong in the set.
    assert b'aliases:["AgentOutputTool","BashOutputTool"]' in blob
    assert b'aliases:["KillShell"]' in blob


# ── behaviour on the built options ────────────────────────────────────────


@pytest.mark.asyncio
async def test_feature_is_switched_off_in_the_cli_env():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()
    assert options.env[CLI_ENABLE_TASKS_ENV] == "false"


@pytest.mark.asyncio
async def test_skill_env_cannot_switch_the_feature_back_on():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options(
        extra_env={CLI_ENABLE_TASKS_ENV: "true", "TAVILY_API_KEY": "t"}
    )
    assert options.env[CLI_ENABLE_TASKS_ENV] == "false"
    assert options.env["TAVILY_API_KEY"] == "t"  # other skill env untouched


@pytest.mark.asyncio
async def test_task_list_tools_disallowed_and_websearch_kept_without_server_tools():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()

    disallowed = set(options.disallowed_tools)
    assert _TASK_LIST_TOOLS <= disallowed
    assert not (_IN_RUN_BACKGROUND_TOOLS & disallowed)
    assert _RUN_SCOPED_CHECKLIST_TOOL not in disallowed
    # The pre-existing guard survives the family disallow (merge, not replace).
    assert "WebSearch" in disallowed
    assert len(options.disallowed_tools) == len(disallowed), "no duplicates"


@pytest.mark.asyncio
async def test_task_list_tools_disallowed_even_when_websearch_is_allowed():
    """Positive case for the other guard: an official-Anthropic provider keeps
    WebSearch, yet the task-list family is still gone — provider-independent."""
    _configure(supports_server_tools=True)
    options = await _run_and_capture_options()

    disallowed = set(options.disallowed_tools)
    assert _TASK_LIST_TOOLS <= disallowed
    assert "WebSearch" not in disallowed
    assert not (_IN_RUN_BACKGROUND_TOOLS & disallowed)


@pytest.mark.asyncio
async def test_upstream_disallow_list_is_merged_not_replaced():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options(
        disallowed_tools=["mcp__lark__lark_cli", "TaskList"]
    )

    disallowed = options.disallowed_tools
    assert "mcp__lark__lark_cli" in disallowed
    assert _TASK_LIST_TOOLS <= set(disallowed)
    assert "WebSearch" in disallowed
    assert disallowed.count("TaskList") == 1


# ── the prompt notice ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_points_cross_run_work_at_the_job_module():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()

    prompt = options.system_prompt
    assert prompt.startswith("SYSTEM INSTRUCTIONS")
    notice = task_list_tools_notice(TASK_LIST_TOOLS)
    assert notice and notice in prompt
    for tool in _TASK_LIST_TOOLS:
        assert tool in notice
    for tool in _IN_RUN_BACKGROUND_TOOLS:
        assert tool in notice  # named as STILL WORKING, never as disabled
    assert "still work" in notice
    # Honest wording: the family is "not available" (the headless spawn
    # never offered it), not "disabled"; the run-scoped list the model does
    # hold is named as such.
    assert "not available" in notice
    assert "disabled" not in notice
    assert _RUN_SCOPED_CHECKLIST_TOOL in notice
    assert "Job module" in notice
    assert "job_create" in notice
    # Exactly once: the notice rides the BASE prompt, never the history.
    assert prompt.count(notice) == 1


def test_notice_is_empty_for_an_empty_tool_list():
    assert task_list_tools_notice(()) == ""


def test_the_job_tool_named_in_the_notice_is_a_real_job_mcp_tool():
    """The notice names a Job tool; a name the Job module does not register
    (it once said `create_job`) sends the model after a tool that does not
    exist. Check the named tool against the MCP server's own tool list."""
    import re

    from narranexus_plugins.job_module._job_mcp_tools import create_job_mcp_server

    notice = task_list_tools_notice(("TaskCreate",))
    named = re.search(r"\((\w+) and friends\)", notice)
    assert named is not None, notice
    registered = {t.name for t in create_job_mcp_server()._tool_manager.list_tools()}
    assert named.group(1) in registered, (named.group(1), sorted(registered))
    assert "create_job" not in registered  # negative: the old wording's name
