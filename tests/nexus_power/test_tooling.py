"""
@file_name: test_tooling.py
@author: Bin Liang
@date: 2026-07-29
@description: Tooling group: policy fail-closed semantics, workspace
confinement, builtin file/shell tools against a real tmp workspace,
dispatcher routing + marker short-circuit + allow/deny filters,
capability expansion.
"""

import sys

import pytest

from narranexus_plugins.frameworks_nexus_power.core.contracts.model import McpServerSpec
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    PolicyContext,
    ToolAnnotations,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.builtin import (
    BuiltinToolset,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.dispatcher import (
    ToolDispatcher,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.expansion import (
    CapabilityExpander,
    Expandable,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
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
                name="mcp__chat__reply_owner",
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
        ToolCall(id="c1", name="mcp__chat__reply_owner",
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
async def test_search_lines_multi_word_query_tokenizes(ctx, engine):
    """2026-08-13 voice run: `tool_search("narra reply speak send")` came
    back "(no matches)" because the whole query was one substring — the
    model concluded its reply tools did not exist and went silent.
    Multi-word queries must tokenize: all-token matches first, any-token
    as the fallback so a verification probe never false-negatives on
    tools that ARE in scope."""
    builtin = BuiltinToolset(ctx, enabled_groups=frozenset({"files"}))
    stub = _StubChannel()
    dispatcher = ToolDispatcher((builtin, stub), policy=engine, ctx=ctx)

    # ANY-token fallback: no single tool matches every word, but the
    # probe still surfaces each tool that matches some word.
    lines = dispatcher.search_lines("zeta reply")
    assert any("zeta_tool" in line for line in lines)
    assert any("reply_owner" in line for line in lines)

    # ALL-token matches rank alone when they exist.
    lines = dispatcher.search_lines("reply user")
    assert any("reply_owner" in line for line in lines)
    assert not any("zeta_tool" in line for line in lines)


@pytest.mark.asyncio
async def test_search_lines_any_token_fallback_is_ranked_and_capped(ctx, engine):
    """Review 2026-08-13 rounds 2-4: a natural-language probe whose
    tokens include glue words must not flood the context, must not let
    long-winded descriptions outrank the real match, and must not let
    single-letter tokens decide the top key. Scoring uses CONTENT words
    only (leaf-name hit first, then coverage, then occurrences as the
    tiebreak); filter semantics keep the full token list. Expressive
    (reply) tools that pass the filter hold reserved seats, so the
    turn's reply surface can never be crowded out by fillers."""
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling import (
        dispatcher as dispatcher_mod,
    )

    verbose = (
        "This module operates on a broad set of resources and it is "
        "designed so that i can be invoked whenever a workflow needs to "
        "coordinate the state of a resource with the state of another "
        "resource, and it will do so in a way that is careful about how "
        "the user of the system perceives the interaction with the "
        "system as a whole over time."
    )
    specs = [
        ToolSpec(name=f"filler_tool_{i:02d}", description=verbose,
                 input_schema={"type": "object"})
        for i in range(30)
    ]
    specs.append(ToolSpec(
        name="mcp__chat__reply_owner",
        description="Reply to the user.", input_schema={"type": "object"},
    ))
    # The hostile shape (round 4): a reply tool whose NAME shares zero
    # tokens with the probe — `speak` has no `i`, no `reply` — and whose
    # short description ties with the fillers on coverage. PRODUCTION
    # path (round 5): reply tools are MCP specs whose annotations cannot
    # carry `expressive` — the fact arrives via the injected live
    # adjudicator, exactly as assembly passes
    # ExpressionContract.is_expressive.
    specs.append(ToolSpec(
        name="mcp__narramessenger_module__speak",
        description="Speak to the user on the current real-time voice call.",
        input_schema={"type": "object"},
    ))
    builtin = BuiltinToolset(ctx, enabled_groups=frozenset())

    class _Chan(_StubChannel):
        def list_tools(self):
            return specs

    expressive_names = {
        "mcp__narramessenger_module__speak",
        "mcp__chat__reply_owner",
    }
    dispatcher = ToolDispatcher(
        (builtin, _Chan()), policy=engine, ctx=ctx,
        is_expressive=expressive_names.__contains__,
    )
    cap = dispatcher_mod._SEARCH_MAX_HITS
    lines = dispatcher.search_lines("how do i reply to the user")
    assert len(lines) <= cap  # capped, not the whole surface
    # Both REAL reply tools survive 30 verbose fillers: the friendly
    # name shape by content-word scoring, the hostile `speak` shape by
    # its guaranteed expressive seat — granted through the production
    # name-list path, no annotation involved.
    assert any("reply_owner" in line for line in lines)
    assert any("__speak" in line for line in lines)
    # Seats require a filter hit: an expressive tool unrelated to the
    # probe gets no free ride.
    unrelated = dispatcher.search_lines("compile the kernel sources")
    assert not any("__speak" in line for line in unrelated)

    # Pipeline review Important #3 — the cap must not be bypassable:
    # whitespace-only query routes to the grouped overview (not a
    # vacuous match of everything)...
    ws = dispatcher.search_lines("   ")
    assert ws and ws[0].endswith("tools in scope:")
    # ...a single glue token is capped like any other query...
    single = dispatcher.search_lines("a")
    assert len(single) <= cap
    # ...and card_index lines have their OWN reserved seats: with 12+
    # matching tools the capability index must not be starved out, while
    # the combined result stays bounded. Card lines are ranked too: the
    # strongest card match must claim a seat ahead of weaker ones.
    card_cap = dispatcher_mod._SEARCH_MAX_CARD_HITS
    card_lines = [f"card-{i}: operates on a thing" for i in range(40)]
    card_lines.append("card-best: reply directly to the user of a thing")
    carded = dispatcher.search_lines("reply user thing", card_index="\n".join(card_lines))
    assert len(carded) <= cap + card_cap
    assert any(line.startswith("card-") for line in carded)
    assert any("card-best" in line for line in carded)


@pytest.mark.asyncio
async def test_expressive_seat_replaces_weakest_without_reordering(ctx, engine):
    """Round 5 review Important #1: the seat is a GUARANTEE, not top
    placement — strong matches keep their rank order, and the missing
    reply tool replaces only the weakest non-expressive seat at the
    tail."""
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling import (
        dispatcher as dispatcher_mod,
    )

    # 13 strong leaf-name matches for the probe (exact `search` in the
    # leaf) — more than the slice holds on their own.
    specs = [
        ToolSpec(name=f"search_tool_{i:02d}", description="search things",
                 input_schema={"type": "object"})
        for i in range(13)
    ]
    # TWO expressive tools with coverage 1 only (descriptions mention
    # `search` once, names share nothing) — two, so seat order among the
    # substitutes is falsifiable (round 6 Minor #2: a single substitute
    # cannot distinguish tail-forward from reversed placement).
    specs.append(ToolSpec(
        name="mcp__narramessenger_module__speak",
        description="Speak results of a search to the user.",
        input_schema={"type": "object"},
    ))
    specs.append(ToolSpec(
        name="mcp__narramessenger_module__narra_send",
        description="Send search results to a room.",
        input_schema={"type": "object"},
    ))
    builtin = BuiltinToolset(ctx, enabled_groups=frozenset())

    class _Chan(_StubChannel):
        def list_tools(self):
            return specs

    dispatcher = ToolDispatcher(
        (builtin, _Chan()), policy=engine, ctx=ctx,
        is_expressive={
            "mcp__narramessenger_module__speak",
            "mcp__narramessenger_module__narra_send",
        }.__contains__,
    )
    cap = dispatcher_mod._SEARCH_MAX_HITS
    lines = dispatcher.search_lines("search")
    assert len(lines) == cap
    # The head keeps rank order (leaf-name matches, scope order): no
    # fake top placement — the substitutes take the LAST seats, and keep
    # their own rank order between them (speak ranks above narra_send
    # here by scope order, so it sits first of the two tail seats).
    assert "search_tool_00" in lines[0]
    assert "search_tool_01" in lines[1]
    assert "__speak" in lines[-2]
    assert "__narra_send" in lines[-1]


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
    # Hand-written literal, not a second call to the same code path: if
    # registration order were ever "fixed" to alphabetical (or any other
    # derived order), `a == b` alone would stay green while silently
    # breaking prompt-cache-friendly ordering.
    assert a == ["read_file", "write_file", "edit_file", "glob", "grep", "ls", "bash"]
    assert a == b


def test_mcp_channel_registers_batches_append_only():
    """The C2 contract on the REAL MCP channel: batches register in
    server-name order within a batch, and a later batch APPENDS after an
    earlier one — never interleaves or resorts."""
    from types import SimpleNamespace

    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.mcp_channel import (
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


# ── extra readable roots (team shared workspace) ────────────────────────────
#
# Why these exist: the team prompt tells an agent to `Read` the team shared
# folder (`_shared/teams/{team_id}`, a SIBLING of the agent workspace), but
# both confinement layers denied it — prompt and framework contradicted each
# other, and claude/codex (which have no such layer) behaved differently.
# `extra_accessible_roots` is the framework-generic escape hatch: the PLATFORM
# decides which roots are additionally readable this turn; the framework
# never learns what `_shared` means. Fail-closed is preserved — an empty
# tuple (the default) reproduces the old behaviour exactly.


@pytest.fixture()
def shared_root(tmp_path):
    """A sibling of the agent workspace, mirroring `_shared/` on disk."""
    d = tmp_path.parent / "_shared_probe"
    d.mkdir(exist_ok=True)
    (d / "team_report.md").write_text("shared\n")
    return d


@pytest.fixture()
def ctx_with_shared(workspace, shared_root):
    return ToolContext(
        agent_id="a1",
        workspace=str(workspace),
        extra_accessible_roots=(str(shared_root),),
    )


def test_extra_root_allows_path_tools(engine, ctx_with_shared, shared_root):
    """A declared extra root is readable by the path-argument tools."""
    ok = engine.check(
        ToolCall(id="1", name="read_file", args={"path": str(shared_root / "team_report.md")}),
        _pctx(ctx_with_shared),
    )
    assert ok.allowed, ok.reason


def test_extra_root_allows_shell(engine, ctx_with_shared, shared_root):
    """The shell layer honours the same roots — otherwise `cat` would be
    denied for a file `read_file` just allowed (inconsistent surface)."""
    ok = engine.check(
        ToolCall(
            id="2",
            name="bash",
            args={"command": f"cat {shared_root / 'team_report.md'}"},
        ),
        _pctx(ctx_with_shared),
    )
    assert ok.allowed, ok.reason


def test_extra_root_does_not_widen_anything_else(engine, ctx_with_shared):
    """Declaring one extra root must not turn the layers off: any OTHER
    outside path stays denied, for both layers."""
    denied = engine.check(
        ToolCall(id="3", name="read_file", args={"path": "/etc/passwd"}),
        _pctx(ctx_with_shared),
    )
    assert not denied.allowed

    denied_shell = engine.check(
        ToolCall(id="4", name="bash", args={"command": "cat /etc/passwd"}),
        _pctx(ctx_with_shared),
    )
    assert not denied_shell.allowed


def test_no_extra_roots_preserves_old_behaviour(engine, ctx, shared_root):
    """Default (empty tuple) must deny the shared dir exactly as before —
    the widening is opt-in, never implicit."""
    denied = engine.check(
        ToolCall(id="5", name="read_file", args={"path": str(shared_root / "team_report.md")}),
        _pctx(ctx),
    )
    assert not denied.allowed and "outside the workspace" in denied.reason


def test_a_granted_root_does_not_admit_its_siblings(engine, workspace, tmp_path):
    """Granting one team's folder must not admit the team next to it.

    The narrow grant only holds if the layer compares against the granted path
    itself and not its parent. Two sibling directories under a common root are
    exactly the shape that would slip through a parent-based check — and the
    common root here is `_shared`, which holds every team the owner has.
    """
    # OUTSIDE the workspace, mirroring the real layout: `_shared` is a sibling
    # of each agent workspace, never inside one. Putting it under the workspace
    # would make this test pass on workspace containment alone and prove
    # nothing about the grant.
    shared = tmp_path.parent / "i3_shared" / "teams"
    mine = shared / "team_1"
    theirs = shared / "team_2"
    mine.mkdir(parents=True, exist_ok=True)
    theirs.mkdir(parents=True, exist_ok=True)
    (mine / "ours.md").write_text("ours")
    (theirs / "secret.md").write_text("theirs")

    ctx = ToolContext(
        agent_id="a1", workspace=str(workspace), extra_accessible_roots=(str(mine),)
    )
    pctx = PolicyContext(tool_ctx=ctx, disallowed_tools=frozenset())

    allowed = engine.check(
        ToolCall(id="1", name="read_file", args={"path": str(mine / "ours.md")}), pctx
    )
    assert allowed.allowed, allowed.reason

    denied = engine.check(
        ToolCall(id="2", name="read_file", args={"path": str(theirs / "secret.md")}), pctx
    )
    assert not denied.allowed, "a sibling team's folder must stay out of bounds"


def test_the_grant_covers_writes_not_just_reads(engine, workspace, tmp_path):
    """Pins the reason the field is called "accessible": `file_path` is in
    `_PATH_ARG_NAMES`, so write tools are governed by the same list. A reader
    who assumes read-only would under-estimate what a grant hands out."""
    granted = tmp_path.parent / "i3_shared_w" / "teams" / "team_1"
    granted.mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(
        agent_id="a1", workspace=str(workspace), extra_accessible_roots=(str(granted),)
    )
    pctx = PolicyContext(tool_ctx=ctx, disallowed_tools=frozenset())

    write = engine.check(
        ToolCall(
            id="1", name="write_file",
            args={"path": str(granted / "new.md"), "content": "x"},
        ),
        pctx,
    )
    assert write.allowed, "the grant is not read-only, and the name now says so"


def test_a_team_artifacts_home_is_readable_by_a_teammate(engine, workspace, tmp_path):
    """The end of the chain N3 was about.

    A team artifact is required to live in the team folder, and a teammate's
    turn is granted exactly that folder. Those two rules only mean something
    together: this asserts the file a teammate is pointed at is one its own
    confinement layer admits — on NexusPower, which is the framework where the
    previous arrangement failed while claude and codex quietly succeeded.
    """
    shared = tmp_path.parent / "n3_shared"
    team_folder = shared / "teams" / "team_1"
    team_folder.mkdir(parents=True, exist_ok=True)
    (team_folder / "report.md").write_text("ours")

    # A DIFFERENT agent's turn in the same team: its own workspace plus what
    # turn_accessible_roots grants.
    ctx = ToolContext(
        agent_id="agent_teammate",
        workspace=str(workspace),
        extra_accessible_roots=(str(shared / "bus_files"), str(team_folder)),
    )
    pctx = PolicyContext(tool_ctx=ctx, disallowed_tools=frozenset())

    read = engine.check(
        ToolCall(id="1", name="read_file", args={"path": str(team_folder / "report.md")}),
        pctx,
    )
    assert read.allowed, read.reason

    # And the producer's private workspace stays unreachable, which is why the
    # artifact had to move rather than the grant widen.
    producer_ws = tmp_path.parent / "n3_producer_ws"
    producer_ws.mkdir(exist_ok=True)
    (producer_ws / "private.md").write_text("mine")
    denied = engine.check(
        ToolCall(id="2", name="read_file", args={"path": str(producer_ws / "private.md")}),
        pctx,
    )
    assert not denied.allowed


def test_mcp_channel_runs_a_stdio_server_with_the_host_interpreter(tmp_path):
    """The tool template's default transport, end to end: the channel spawns
    the server as a child process, lists its tools under the mcp__ namespace
    and calls one. Before this the channel spoke SSE only and read a stdio
    config as an empty URL, so a plugin's tools silently never existed."""
    import asyncio

    from narranexus_plugins.frameworks_nexus_power.core.assembly import mcp_spec_from_config
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.mcp_channel import (
        McpToolChannel,
        stdio_command,
    )

    server = tmp_path / "srv.py"
    server.write_text(
        "from fastmcp import FastMCP\n"
        "mcp = FastMCP('calc')\n"
        "@mcp.tool()\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "mcp.run()\n"
    )
    spec = mcp_spec_from_config({"command": "python", "args": [str(server)], "env": {"NX_TEST_MARK": "1"}})
    assert spec.is_stdio and spec.args == (str(server),) and spec.env == {"NX_TEST_MARK": "1"}
    assert stdio_command("python") == sys.executable and stdio_command("uvx") == "uvx"
    url_spec = mcp_spec_from_config({"url": "http://x/sse", "headers": {"A": "1"}})
    assert not url_spec.is_stdio and url_spec.url == "http://x/sse" and url_spec.headers == {"A": "1"}

    async def _run():
        channel = McpToolChannel({"calc": spec})
        try:
            await channel.connect()
            names = [t.name for t in channel.list_tools()]
            assert names == ["mcp__calc__add"], names
            result = await channel.call("mcp__calc__add", {"a": 1, "b": 2}, ToolContext(agent_id="a", workspace=str(tmp_path)))
            assert result.ok and result.content.strip() == "3", result
        finally:
            await channel.aclose()

    asyncio.run(_run())
