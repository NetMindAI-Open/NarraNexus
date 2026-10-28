"""
@file_name: test_owner_reply_tool_split.py
@author:
@date: 2026-08-17
@description: `send_message_to_user_directly` became two names — the accounting
must recognise both, on the surfaces that use them.

The split (decision: 2026-08-17) exists because the one tool carried two
OPPOSITE disciplines: answering your owner is expected on almost every chat
turn, notifying your owner from someone else's conversation is something you do
only for (a)/(b)/(c). Each name now carries its own.

The hazard the split introduces is here: `_has_organic_reply` asks the registry
"did this turn speak?", and a registry that knows only one of the two names goes
blind on the surface that uses the other. On the owner's chat turn that means a
perfectly answered turn reads as "never spoke", and the helper-LLM fallback
writes a SECOND reply on top of it — every time.
"""
from __future__ import annotations

import pathlib

from narranexus.platform.channel.message_source_handler import MessageSourceRegistry


def test_an_owner_chat_turn_counts_reply_owner_as_speaking():
    """The owner's chat turn has `reply_owner` on its desk and nothing else."""
    handler = MessageSourceRegistry.get("chat")
    assert handler.is_user_reply_tool("mcp__chat_module__reply_owner")


def test_every_other_surface_counts_notify_owner_as_owner_visible():
    """`notify_owner` is the one tool that means "put this in the owner's
    window", and it is on every non-chat desk."""
    for source in ("lark", "slack", "telegram", "wechat", "message_bus", "job"):
        handler = MessageSourceRegistry.get(source)
        assert handler.is_owner_visible_reply_tool(
            "mcp__chat_module__notify_owner"
        ), f"{source} does not recognise notify_owner as owner-visible"


def test_the_bus_still_separates_delivered_from_owner_visible():
    """A peer reply reaches the peer, not the owner's window. Conflating the two
    let every agent-to-agent reply re-anchor the owner's session (PR #230)."""
    handler = MessageSourceRegistry.get("message_bus")
    assert handler.is_user_reply_tool("mcp__message_bus_module__message_agent")
    assert not handler.is_owner_visible_reply_tool(
        "mcp__message_bus_module__message_agent"
    )


def test_the_retired_name_is_gone_from_every_registration():
    """`send_message_to_user_directly` named a mechanism and misdescribed its
    scope — on an IM turn the "user" the agent faces is the IM sender, while the
    tool wrote to the owner. Two prompt sections existed only to correct that."""
    for source in ("chat", "lark", "slack", "telegram", "wechat",
                   "message_bus", "job", "discord", "narramessenger"):
        handler = MessageSourceRegistry.get(source)
        joined = " ".join(handler.user_reply_tool_names)
        assert "send_message_to_user_directly" not in joined, source


def test_the_retired_name_survives_only_as_prose_anywhere_in_the_tree():
    """Repo-wide, and it distinguishes prose from a functional identifier.

    `send_message_to_user_directly` is legitimately named in HISTORY — a dozen
    comments and docstrings explain what it was and why it split, and rewriting
    those would destroy the record. What must not exist is the name in a
    FUNCTIONAL position, because 铁律 #2 leaves no shim: nothing answers to it.

    Round 3 of this PR's pre-review found four such sites, all in `scripts/`, and
    the worst was `nexus_power_vs_claude_bench.py`'s `REPLY_TOOLS` — the harness
    that validates the expressive-surface contract THIS change rewrites. It would
    have recorded zero replies for every run and reported NexusPower as silent:
    a measuring instrument lying in the direction that hides a regression in the
    mechanism it measures.

    The distinction is drawn with AST rather than by grepping for `#`: a comment
    is invisible to the parser, and a docstring is the one string constant that is
    prose by construction. Every OTHER string constant, and any identifier, is
    functional. That is exactly where the four sites lived.
    """
    import ast
    import subprocess

    RETIRED = "send_message_to_user_directly"
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    ).stdout.strip()
    # `src/`, `backend/`, `scripts/` — deliberately NOT `tests/`. A test that
    # asserts the name is ABSENT has to spell it out, and this file is one of
    # them, so including tests would make the guard reject its own siblings. The
    # four real sites round 3 found were all under `scripts/`.
    files = subprocess.run(
        ["git", "grep", "-l", RETIRED, "--", "src/*.py", "backend/*.py",
         "scripts/*.py"],
        capture_output=True, text=True, cwd=root,
    ).stdout.split()

    offenders: list[str] = []
    for rel in files:
        path = pathlib.Path(root) / rel
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - not our problem to police
            continue

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None and RETIRED in doc:
                    docstrings.add(doc)

        # Every AST field that can CARRY the name, not just the three the first
        # version checked (Constant / Name / Attribute). Round 4 mutation-tested
        # that version and it missed five classes — including
        # `@mcp.tool() async def send_message_to_user_directly(...)`, i.e. the
        # 铁律 #2 back-compat shim this guard exists to stop, which is also the
        # single most likely way the name comes back. A guard that reads as
        # exhaustive and is not is worse than no guard.
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if RETIRED in node.value and node.value not in docstrings:
                    hit = "string literal"
            elif isinstance(node, ast.Name) and RETIRED in node.id:
                hit = "identifier"
            elif isinstance(node, ast.Attribute) and RETIRED in node.attr:
                hit = "attribute"
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and RETIRED in node.name:
                hit = "definition — a back-compat shim"
            elif isinstance(node, ast.arg) and RETIRED in node.arg:
                hit = "function parameter"
            elif isinstance(node, ast.keyword) and RETIRED in (node.arg or ""):
                hit = "call keyword"
            elif isinstance(node, ast.alias) and RETIRED in (
                node.name + " " + (node.asname or "")
            ):
                hit = "import alias"
            if hit:
                offenders.append(f"{rel}:{getattr(node, 'lineno', '?')} ({hit})")

    assert not offenders, (
        "the retired owner tool is named in a functional position — nothing "
        "answers to it:\n  " + "\n  ".join(offenders)
    )
