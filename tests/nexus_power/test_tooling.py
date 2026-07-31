"""
@file_name: test_tooling.py
@author: Bin Liang
@date: 2026-07-29
@description: Tooling group: policy fail-closed semantics, workspace
confinement, builtin file/shell tools against a real tmp workspace,
dispatcher routing + marker short-circuit + allow/deny filters,
capability expansion.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_power.contracts.model import McpServerSpec
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    PolicyContext,
    ToolAnnotations,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.builtin import (
    BuiltinToolset,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.dispatcher import (
    ToolDispatcher,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.expansion import (
    CapabilityExpander,
    Expandable,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.policy import (
    DisallowedToolsLayer,
    PolicyEngine,
    ShellConfinementLayer,
    WorkspaceConfinementLayer,
)


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
    return tmp_path


@pytest.fixture()
def ctx(workspace):
    return ToolContext(agent_id="a1", workspace=str(workspace))


@pytest.fixture()
def engine():
    return PolicyEngine(
        (DisallowedToolsLayer(), WorkspaceConfinementLayer(), ShellConfinementLayer())
    )


def _pctx(ctx, disallowed=()):
    return PolicyContext(tool_ctx=ctx, disallowed_tools=frozenset(disallowed))


def test_policy_disallow_and_confinement(engine, ctx):
    ok = engine.check(ToolCall(id="1", name="read_file", args={"path": "hello.txt"}), _pctx(ctx))
    assert ok.allowed
    denied = engine.check(
        ToolCall(id="2", name="read_file", args={"path": "/etc/passwd"}), _pctx(ctx)
    )
    assert not denied.allowed and "outside the workspace" in denied.reason
    escape = engine.check(
        ToolCall(id="3", name="read_file", args={"path": "../../etc/passwd"}), _pctx(ctx)
    )
    assert not escape.allowed
    banned = engine.check(
        ToolCall(id="4", name="bash", args={"command": "ls"}), _pctx(ctx, ["bash"])
    )
    assert not banned.allowed


def test_policy_layer_crash_is_deny(ctx):
    class Broken:
        def check(self, call, ctx):
            raise RuntimeError("boom")

    engine = PolicyEngine((Broken(),))
    decision = engine.check(ToolCall(id="1", name="x", args={}), _pctx(ctx))
    assert not decision.allowed and "fail-closed" in decision.reason


@pytest.mark.asyncio
async def test_builtin_file_tools_roundtrip(ctx):
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"files", "shell"}))
    names = {s.name for s in toolset.list_tools()}
    assert {"read_file", "write_file", "edit_file", "glob", "grep", "ls", "bash"} <= names

    read = await toolset.call("read_file", {"path": "hello.txt", "offset": 2, "limit": 1}, ctx)
    assert read.ok and read.content == "line2"

    write = await toolset.call("write_file", {"path": "sub/new.txt", "content": "abc"}, ctx)
    assert write.ok
    edit = await toolset.call("edit_file", {"path": "sub/new.txt", "old": "b", "new": "B"}, ctx)
    assert edit.ok
    read2 = await toolset.call("read_file", {"path": "sub/new.txt"}, ctx)
    assert read2.content == "aBc"

    grep = await toolset.call("grep", {"pattern": "line2"}, ctx)
    assert grep.ok and "hello.txt:2:" in grep.content

    bash = await toolset.call("bash", {"command": "echo hi && pwd"}, ctx)
    assert bash.ok and "hi" in bash.content and ctx.workspace in bash.content


@pytest.mark.asyncio
async def test_bash_failure_and_timeout(ctx):
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"shell"}))
    fail = await toolset.call("bash", {"command": "exit 3"}, ctx)
    assert not fail.ok and "exit code 3" in (fail.error or "")
    slow = await toolset.call("bash", {"command": "sleep 5", "timeout_s": 1}, ctx)
    assert not slow.ok and "timed out" in (slow.error or "")


class _StubChannel:
    """A fake channel with a marker tool and a normal tool."""

    generation = 0

    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [
            ToolSpec(
                name="mcp__chat__send_message_to_user_directly",
                description="Reply to the user.",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(
                    expressive=True, marker_only=True, streamable_fields=("content",)
                ),
            ),
            ToolSpec(name="zeta_tool", description="does z", input_schema={"type": "object"}),
        ]

    async def call(self, name, args, ctx):
        self.calls.append(name)
        return ToolResult(call_id="", ok=True, content=f"ran {name}")

    async def refresh(self):
        return False


@pytest.mark.asyncio
async def test_dispatcher_routing_marker_and_filters(ctx, engine):
    builtin = BuiltinToolset(ctx, enabled_groups=frozenset({"files"}))
    stub = _StubChannel()
    dispatcher = ToolDispatcher(
        (builtin, stub),
        policy=engine,
        ctx=ctx,
        disallowed_tools=frozenset({"grep"}),
    )
    names = [s.name for s in dispatcher.visible_tools()]
    assert "grep" not in names            # disallowed filtered from schema
    assert "zeta_tool" in names

    # Marker tool short-circuits: channel never invoked.
    marker = await dispatcher.execute(
        ToolCall(id="c1", name="mcp__chat__send_message_to_user_directly",
                 args={"content": "hi"})
    )
    assert marker.ok and stub.calls == []

    routed = await dispatcher.execute(ToolCall(id="c2", name="zeta_tool", args={}))
    assert routed.ok and routed.call_id == "c2" and stub.calls == ["zeta_tool"]

    denied = await dispatcher.execute(ToolCall(id="c3", name="grep", args={"pattern": "x"}))
    assert not denied.ok and "denied" in (denied.error or "")

    missing = await dispatcher.execute(ToolCall(id="c4", name="ghost", args={}))
    assert not missing.ok

    lines = dispatcher.search_lines("zeta")
    assert any("zeta_tool" in line for line in lines)


@pytest.mark.asyncio
async def test_capability_expander_idempotent_and_seams():
    attached, env = [], {}

    async def add_servers(servers):
        attached.append(servers)

    expander = CapabilityExpander(
        (
            Expandable(
                key="jobs",
                card="schedule recurring work",
                instructions="Use job tools wisely.",
                mcp_servers={"job": McpServerSpec(url="http://x/sse")},
                extra_env={"JOB": "1"},
            ),
            Expandable(key="empty", card="nothing much"),
        ),
        add_mcp_servers=add_servers,
        add_env=env.update,
    )
    assert "jobs: schedule recurring work" in expander.card_index()

    text = await expander.expand("jobs")
    assert text == "Use job tools wisely."
    assert len(attached) == 1 and env == {"JOB": "1"}

    again = await expander.expand("jobs")   # idempotent: no re-attach,
    assert len(attached) == 1 and again == text  # instructions re-served

    with pytest.raises(KeyError):
        await expander.expand("nope")

    block = await expander.expand_initial(frozenset({"empty"}))
    assert "now active" in block
    assert expander.expanded_keys() == frozenset({"jobs", "empty"})


@pytest.mark.asyncio
async def test_expander_declares_expressive_tools_through_seam():
    """An expandable may carry delivery tools; expanding it grants them
    to the expression contract exactly once (idempotent with the rest)."""
    granted: list[str] = []

    async def add_servers(servers):
        pass

    expander = CapabilityExpander(
        (
            Expandable(
                key="lark",
                card="Lark channel",
                instructions="Reply on Lark with lark_cli.",
                expressive_tools=("mcp__lark_module__lark_cli",),
            ),
        ),
        add_mcp_servers=add_servers,
        add_env=lambda env: None,
        add_expressive=granted.extend,
    )
    await expander.expand("lark")
    assert granted == ["mcp__lark_module__lark_cli"]
    await expander.expand("lark")  # idempotent: not re-granted
    assert granted == ["mcp__lark_module__lark_cli"]


@pytest.mark.asyncio
async def test_dispatcher_preserves_registration_order_append_only(ctx, engine):
    """C2: the tool array is (channel order, registration order) and an
    expansion APPENDS — no name resort that would move new tools into the
    middle of the cached prefix."""

    class _Ordered:
        generation = 0

        def __init__(self):
            self._specs = [
                ToolSpec(name="zebra", description="z", input_schema={"type": "object"}),
                ToolSpec(name="alpha", description="a", input_schema={"type": "object"}),
            ]

        def list_tools(self):
            return list(self._specs)

        async def call(self, name, args, ctx):
            return ToolResult(call_id="", ok=True, content="ok")

        async def refresh(self):
            return False

    channel = _Ordered()
    dispatcher = ToolDispatcher((channel,), policy=engine, ctx=ctx)
    assert [s.name for s in dispatcher.visible_tools()] == ["zebra", "alpha"]

    # Simulate a mid-turn expansion: registration appends, generation bumps.
    channel._specs.append(
        ToolSpec(name="aardvark", description="new", input_schema={"type": "object"})
    )
    channel.generation += 1
    assert [s.name for s in dispatcher.visible_tools()] == ["zebra", "alpha", "aardvark"]


def test_builtin_toolset_order_is_deterministic(ctx):
    """The C2 contract on a REAL channel: two separately built toolsets
    with the same groups expose identical (registration-order) name
    sequences — code order, not name order, not dict-iteration luck."""
    groups = frozenset({"files", "shell", "context"})
    a = [s.name for s in BuiltinToolset(ctx, enabled_groups=groups).list_tools()]
    b = [s.name for s in BuiltinToolset(ctx, enabled_groups=groups).list_tools()]
    assert a == b and len(a) > 0


def test_mcp_channel_registers_batches_append_only():
    """The C2 contract on the REAL MCP channel: batches register in
    server-name order within a batch, and a later batch APPENDS after an
    earlier one — never interleaves or resorts."""
    from types import SimpleNamespace

    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.mcp_channel import (
        McpToolChannel,
    )

    def _tool(name):
        return SimpleNamespace(
            name=name, description="", inputSchema={"type": "object"}, annotations=None
        )

    channel = McpToolChannel({})
    # Batch 1: two servers, registered in server-name order.
    channel._register_tools("beta", [_tool("zz"), _tool("aa")])
    # Batch 2 (a later expansion): appends after batch 1 even though its
    # server name sorts first.
    channel._register_tools("alpha", [_tool("mm")])
    names = [s.name for s in channel.list_tools()]
    assert names == ["mcp__beta__zz", "mcp__beta__aa", "mcp__alpha__mm"]


def test_shell_confinement_blocks_the_documented_escapes(engine, ctx):
    """Regression for acceptance case `safety` (2026-07-29): the file
    tools denied /etc/passwd and the model simply ran `bash head -1
    /etc/passwd` instead."""
    for command in (
        "head -1 /etc/passwd",
        "cat ~/.ssh/id_rsa",
        "cd / && ls",
        "cd ~",
    ):
        decision = engine.check(
            ToolCall(id="1", name="bash", args={"command": command}), _pctx(ctx)
        )
        assert not decision.allowed, command
        assert "workspace" in decision.reason


def test_shell_confinement_allows_normal_work(engine, ctx, workspace):
    for command in (
        "ls -la",
        "python3 -c 'print(1)'",
        "cat notes/summary.txt",
        f"wc -l {workspace}/hello.txt",   # absolute but INSIDE the workspace
        "sed -i 's/a/b/' hello.txt",
    ):
        decision = engine.check(
            ToolCall(id="1", name="bash", args={"command": command}), _pctx(ctx)
        )
        assert decision.allowed, f"{command}: {decision.reason}"


def test_shell_confinement_covers_relative_escapes(engine, ctx):
    """Absolute-path checking alone is not confinement.

    The review found the token loop skipping every token that did not
    start with ``/`` or ``~`` — so the shortest escape in the book,
    ``cat ../../../etc/passwd``, walked straight through the layer that
    exists precisely to stop it.
    """
    for command in (
        "cat ../../../../etc/passwd",
        "cd ../.. && cat etc/passwd",
        "head -1 ../../etc/passwd",
        "cd ..",
    ):
        decision = engine.check(
            ToolCall(id="x", name="bash", args={"command": command}), _pctx(ctx)
        )
        assert not decision.allowed, f"escaped: {command}"
        assert "workspace" in decision.reason

    # Ordinary work inside the workspace stays allowed.
    for command in (
        "ls",
        "cat hello.txt",
        "cd sub && ls",
        "grep -rn foo ./sub",
        "sed -i 's|/usr/bin|x|' hello.txt",
    ):
        decision = engine.check(
            ToolCall(id="y", name="bash", args={"command": command}), _pctx(ctx)
        )
        assert decision.allowed, f"false positive: {command} -> {decision.reason}"


def test_glob_pattern_is_a_path_operand(engine, ctx):
    """``WorkspaceConfinementLayer`` only inspected ``path`` /
    ``file_path`` / ``directory``, so ``glob('../../../etc/*')``
    enumerated the host while every declared path argument looked clean.

    ``grep``'s ``pattern`` is a REGEX and must stay unresolved, or a
    search for ``^/etc`` would be denied as an escape.
    """
    denied = engine.check(
        ToolCall(id="1", name="glob", args={"pattern": "../../../etc/*"}), _pctx(ctx)
    )
    assert not denied.allowed and "outside the workspace" in denied.reason

    allowed = engine.check(
        ToolCall(id="2", name="glob", args={"pattern": "**/*.py"}), _pctx(ctx)
    )
    assert allowed.allowed

    regex = engine.check(
        ToolCall(id="3", name="grep", args={"pattern": "^/etc/passwd"}), _pctx(ctx)
    )
    assert regex.allowed, "grep patterns are regexes, not paths"

    file_filter = engine.check(
        ToolCall(id="4", name="grep", args={"pattern": "x", "glob": "../../*.conf"}),
        _pctx(ctx),
    )
    assert not file_filter.allowed


@pytest.mark.asyncio
async def test_glob_tool_filters_outside_hits_itself(ctx):
    """Defence in depth: the tool that touches the filesystem does not
    assume the policy layer ran — that assumption opened the hole."""
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"files"}))
    escaped = await toolset.call("glob", {"pattern": "../../../etc/*"}, ctx)
    assert escaped.ok and escaped.content.strip() == ""

    inside = await toolset.call("glob", {"pattern": "*.txt"}, ctx)
    assert inside.ok and "hello.txt" in inside.content


@pytest.mark.asyncio
async def test_shell_env_is_an_allowlist_not_the_host_environment(ctx, monkeypatch):
    """The agent's shell used to inherit ``os.environ`` wholesale.

    One ``env`` call would then hand the model every credential the host
    process holds — DB passwords, provider keys, the master secret —
    which is precisely the "scoped creds" line iron rule #20 draws.
    """
    monkeypatch.setenv("NEXUS_TEST_MASTER_SECRET", "super-secret-value")
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"shell"}))

    dumped = await toolset.call("bash", {"command": "env"}, ctx)
    assert dumped.ok
    assert "super-secret-value" not in dumped.content
    assert "NEXUS_TEST_MASTER_SECRET" not in dumped.content

    # …while the shell stays a usable shell.
    usable = await toolset.call("bash", {"command": "echo $HOME && pwd && echo ok"}, ctx)
    assert usable.ok and "ok" in usable.content and ctx.workspace in usable.content


@pytest.mark.asyncio
async def test_shell_still_receives_turn_scoped_env(workspace):
    """``extra_env`` is the turn's OWN scoped set and must pass through."""
    scoped = ToolContext(
        agent_id="a1", workspace=str(workspace), extra_env={"AGENT_SCOPED": "yes-please"}
    )
    toolset = BuiltinToolset(scoped, enabled_groups=frozenset({"shell"}))
    got = await toolset.call("bash", {"command": "echo $AGENT_SCOPED"}, scoped)
    assert got.ok and "yes-please" in got.content


@pytest.mark.asyncio
async def test_write_tools_refuse_missing_path(ctx, workspace):
    """A write-shaped call whose ``path`` never arrived (truncated or
    malformed arguments) must fail with an actionable error — the old
    fallback resolved to the workspace ROOT and surfaced
    ``[Errno 21] Is a directory``, sending the model down a
    path-debugging rabbit hole."""
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"files"}))
    for name, args in (
        ("write_file", {"content": "x"}),
        ("edit_file", {"old": "a", "new": "b"}),
        ("read_file", {}),
    ):
        result = await toolset.call(name, args, ctx)
        assert not result.ok
        assert "path" in (result.error or "")
        assert "Is a directory" not in (result.error or "")


@pytest.mark.asyncio
async def test_dispatcher_rejects_missing_required_args_before_routing(ctx, engine):
    """Hermes-style pre-dispatch validation: required fields missing →
    the schema comes back to the model and the handler never runs."""
    dispatcher = ToolDispatcher(
        (BuiltinToolset(ctx, enabled_groups=frozenset({"files"})),),
        policy=engine,
        ctx=ctx,
    )
    result = await dispatcher.execute(
        ToolCall(id="1", name="write_file", args={"content": "hello"})
    )
    assert not result.ok
    assert "path" in result.error and "NOT executed" in result.error
    # And nothing was written anywhere in the workspace.
    assert list((ctx_dir := __import__("pathlib").Path(ctx.workspace)).glob("**/*")) == [
        ctx_dir / "hello.txt"
    ]
    # A complete call still routes normally.
    ok = await dispatcher.execute(
        ToolCall(id="2", name="write_file", args={"path": "out.txt", "content": "hi"})
    )
    assert ok.ok
