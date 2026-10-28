"""
@file_name: test_lark_unknown_subcommand.py
@date: 2026-09-09
@description: Agents hallucinate lark-cli +shortcuts that do not exist
(``docs +get``, ``calendar +events-list``). lark-cli answers with a bare
validation error and a "run --help" hint, the agent retries another guess,
and the turn burns tool calls. The tool layer now recognises that error
shape, fetches ``lark-cli <domain> --help`` ONCE per domain (cached), and
returns a structured error that lists the domain's real +shortcuts.

The CLI is a fake Python script reached through ``LARK_CLI_BIN`` so the
whole chain — spawn, exit code, JSON envelope (on STDERR for validation
errors, as the real CLI does; on stdout for API errors), help probe, cache —
runs at the OS level; nothing in ``LarkCLIClient`` is mocked. Each test goes red
when the translation is removed: the bare CLI message carries no shortcut
list and no ``valid_shortcuts`` key.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from narranexus_plugins.lark_module import lark_cli_client as mod
from narranexus_plugins.lark_module.lark_cli_client import LarkCLIClient

DOCS_HELP = textwrap.dedent(
    """\
    Document and content operations

    Domain guide (concepts, command choice, conventions): lark-cli skills read lark-doc

    Usage:
      lark-cli docs [flags]
      lark-cli docs [command]

    Available Commands:
      +create                Create a Lark document
      +fetch                 Fetch Lark document content
      +search                Search Lark docs, Wiki, and spreadsheet files
      +update                Update a Lark document
      docs.blocks            list, get
      wiki.spaces            list

    Flags:
      -h, --help   help for docs

    Use "lark-cli docs [command] --help" for more information about a command.
    """
)

FAKE_CLI = textwrap.dedent(
    """\
    import json, sys
    from pathlib import Path
    args = sys.argv[1:]
    counter = Path(sys.argv[0]).with_name("help_calls")
    spawns = Path(sys.argv[0]).with_name("spawns")
    spawns.write_text(str(int(spawns.read_text()) + 1) if spawns.exists() else "1")
    domain, sub = args[0], args[1]
    if len(args) == 2 and args[1] == "--help" and domain != "broken":
        counter.write_text(str(int(counter.read_text() or 0) + 1) if counter.exists() else "1")
        if domain == "bare":
            # A domain with raw resources but no +shortcuts.
            sys.stdout.write("Available Commands:\\n  bare.things  list, get\\n\\nFlags:\\n  -h, --help\\n")
            sys.exit(0)
        sys.stdout.write(Path(sys.argv[0]).with_name("docs_help.txt").read_text())
        sys.exit(0)
    if sub == "+scope-fail":
        print(json.dumps({"ok": False, "error": {"code": 99991672,
              "message": "missing_scope: docs:document"}}))
        sys.exit(1)
    # "broken" answers even --help with the unknown-subcommand envelope: the
    # shape that would recurse without the probe guard.
    if domain in ("broken", "bare") or (sub.startswith("+") and sub not in ("+create", "+fetch", "+search", "+update")):
        # Like the real CLI (1.0.86): validation errors go to STDERR, rc=2.
        sys.stderr.write(json.dumps({"ok": False, "error": {
            "type": "validation", "subtype": "invalid_argument",
            "message": f'unknown subcommand "{sub}" for "lark-cli {domain}"',
            "hint": f"run `lark-cli {domain} --help` to see available subcommands",
            "params": [{"name": sub, "reason": "unknown subcommand"}]},
            "_notice": {"update": {"message": "lark-cli 1.0.94 available"}}}))
        sys.exit(2)
    print(json.dumps({"ok": True, "data": {"echo": args}}))
    """
)


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch):
    script = tmp_path / "lark-cli"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    script.chmod(0o755)
    (tmp_path / "docs_help.txt").write_text(DOCS_HELP)
    monkeypatch.setenv("LARK_CLI_BIN", str(script))
    monkeypatch.setattr(mod, "_LARK_CLI_BIN", None)
    monkeypatch.setattr(mod, "_LARK_EXTRA_PATH", None)
    monkeypatch.setattr(mod, "_SHORTCUT_CACHE", {})
    monkeypatch.setattr(mod, "_SHORTCUT_PROBE_FAILED_AT", {})
    yield tmp_path


def _help_calls(root: Path) -> int:
    f = root / "help_calls"
    return int(f.read_text()) if f.exists() else 0


def _spawns(root: Path) -> int:
    f = root / "spawns"
    return int(f.read_text()) if f.exists() else 0


async def _run(args: list[str]) -> dict:
    return await LarkCLIClient()._exec_lark_cli(
        ["lark-cli", *args], "", 10.0, env=dict(os.environ)
    )


@pytest.mark.asyncio
async def test_unknown_shortcut_returns_the_domains_valid_shortcuts(fake_cli):
    result = await _run(["docs", "+get"])

    assert result["success"] is False
    assert "+get" in result["error"]
    for shortcut in ("+create", "+fetch", "+search", "+update"):
        assert shortcut in result["error"]
    # Raw API resources are not +shortcuts and must not be offered as such.
    assert "docs.blocks" not in result["error"]
    assert result["error_data"]["valid_shortcuts"] == ["+create", "+fetch", "+search", "+update"]
    assert result["error_data"]["domain"] == "docs"
    assert "shortcuts_unavailable" not in result["error_data"]
    assert _help_calls(fake_cli) == 1


@pytest.mark.asyncio
async def test_probe_that_fails_the_same_way_does_not_recurse(fake_cli):
    """A CLI whose --help ALSO answers with the unknown-subcommand envelope
    must produce exactly two spawns (the call + one probe), a degraded
    message, and no valid_shortcuts key — never a probe of the probe."""
    result = await _run(["broken", "+anything"])

    assert result["success"] is False
    assert "+anything" in result["error"]
    assert "could not be read" in result["error"]
    assert "valid_shortcuts" not in result["error_data"]
    assert result["error_data"]["shortcuts_unavailable"] is True
    assert result["error_data"]["domain"] == "broken"
    assert _spawns(fake_cli) == 2
    # Within the retry window a later hit does not re-probe: one spawn.
    await _run(["broken", "+again"])
    assert _spawns(fake_cli) == 3
    # Not cached for good: once the window has passed, the next hit probes
    # again (still bounded to one probe).
    for key in list(mod._SHORTCUT_PROBE_FAILED_AT):
        mod._SHORTCUT_PROBE_FAILED_AT[key] -= mod._SHORTCUT_PROBE_RETRY_SEC
    await _run(["broken", "+later"])
    assert _spawns(fake_cli) == 5


@pytest.mark.asyncio
async def test_successful_empty_probe_is_cached(fake_cli):
    """A domain whose --help lists no +shortcuts: the probe SUCCEEDED, so
    its (empty) answer is cached and the next hallucinated call costs no
    second probe. Contrast with the failed probe above, which is retried."""
    result = await _run(["bare", "+anything"])
    assert result["success"] is False
    assert "could not be read" in result["error"]
    assert result["error_data"]["shortcuts_unavailable"] is True
    assert _help_calls(fake_cli) == 1

    await _run(["bare", "+again"])
    assert _help_calls(fake_cli) == 1  # cached, not re-probed


@pytest.mark.asyncio
async def test_cache_is_keyed_by_executable(fake_cli, monkeypatch):
    await _run(["docs", "+get"])
    assert _help_calls(fake_cli) == 1
    # Same domain through a different binary path → its own probe.
    other = fake_cli / "lark-cli-other"
    other.write_text((fake_cli / "lark-cli").read_text())
    other.chmod(0o755)
    (fake_cli / "help_calls").unlink()
    await LarkCLIClient()._exec_lark_cli(
        [str(other), "docs", "+get"], "", 10.0, env=dict(os.environ)
    )
    assert _help_calls(fake_cli) == 1


@pytest.mark.asyncio
async def test_help_is_fetched_once_per_domain(fake_cli):
    await _run(["docs", "+get"])
    second = await _run(["docs", "+events-list"])
    assert "+fetch" in second["error"]
    assert _help_calls(fake_cli) == 1


@pytest.mark.asyncio
async def test_known_shortcut_passes_through(fake_cli):
    result = await _run(["docs", "+fetch", "--doc", "doxcn1"])
    assert result["success"] is True
    assert result["data"]["data"]["echo"] == ["docs", "+fetch", "--doc", "doxcn1"]
    assert _help_calls(fake_cli) == 0


@pytest.mark.asyncio
async def test_other_cli_errors_are_untouched(fake_cli):
    result = await _run(["docs", "+scope-fail"])
    assert result["success"] is False
    assert result["error"] == "missing_scope: docs:document"
    assert "valid_shortcuts" not in result["error_data"]
    assert _help_calls(fake_cli) == 0


def test_help_parser_keeps_only_shortcuts():
    assert mod._parse_help_shortcuts(DOCS_HELP) == ("+create", "+fetch", "+search", "+update")
    assert mod._parse_help_shortcuts("no commands here") == ()


def test_unknown_subcommand_detector():
    err = {
        "message": 'unknown subcommand "+get" for "lark-cli docs"',
        "params": [{"name": "+get", "reason": "unknown subcommand"}],
    }
    assert mod._unknown_subcommand(err) == ("docs", "+get")
    assert mod._unknown_subcommand({"message": "missing_scope: x"}) is None
    assert mod._unknown_subcommand({}) is None
    # Message text alone is not enough when structured params disagree: an
    # unrelated error that merely quotes those words must not be translated.
    assert mod._unknown_subcommand({
        "message": 'unknown subcommand "+get" for "lark-cli docs"',
        "params": [{"name": "body", "reason": "other"}],
    }) is None
    # ...while a params list that does carry the reason (or no params at
    # all, as older CLIs emit) matches.
    assert mod._unknown_subcommand({
        "message": 'unknown subcommand "+get" for "lark-cli docs"',
    }) == ("docs", "+get")
