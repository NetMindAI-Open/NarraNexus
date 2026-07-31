"""
@file_name: test_claude_cli_pin.py
@date: 2026-07-29
@description: Guard the CLI-binary pin and the resolver's fail-open contract.

Two things are protected here.

**The pin agreement.** ``PINNED_CLI_VERSION`` is mirrored as a literal into
``run.sh`` and ``docker/Dockerfile.manyfold``, because neither can import
Python at the point it installs the npm package. Drift is silent and
expensive: the installed CLI stops matching the pin, the resolver rejects it,
and every environment quietly reverts to the SDK's bundled 2.1.56 — the version
whose ``tools``-array reshuffling voids the whole prompt-cache prefix
(experiments E3/E3c). Same "multiple anchors plus a check" shape CLAUDE.md
already uses for the five release version anchors.

**Fail-open.** Every uncertain branch must return None (= keep the SDK's
bundled binary) rather than raise or hand the SDK a path it cannot launch. A
CLI-selection problem must never break an agent run (铁律 #14).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework.adapters.claude import cli_binary
from xyz_agent_context.agent_framework.adapters.claude.cli_binary import (
    PINNED_CLI_VERSION,
    resolve_cli_path,
)

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """The decision is memoized per process; every test re-resolves."""
    cli_binary.reset_cache_for_tests()
    yield
    cli_binary.reset_cache_for_tests()


# --- the pin is mirrored consistently --------------------------------------


def test_pin_is_a_plain_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", PINNED_CLI_VERSION), PINNED_CLI_VERSION


def test_run_sh_pins_the_same_version():
    text = (_REPO / "run.sh").read_text(encoding="utf-8")
    m = re.search(r'_CLAUDE_CLI_VERSION="([0-9.]+)"', text)
    assert m, "run.sh no longer declares _CLAUDE_CLI_VERSION"
    assert m.group(1) == PINNED_CLI_VERSION, (
        f"run.sh pins {m.group(1)}, cli_binary pins {PINNED_CLI_VERSION}"
    )


def test_run_sh_installs_the_pinned_version_not_latest():
    """An unpinned `npm install -g` is the original bug: the version then
    depends on when the machine was provisioned."""
    text = (_REPO / "run.sh").read_text(encoding="utf-8")
    installs = re.findall(r"npm install -g [\"']?@anthropic-ai/claude-code([^\"'\s]*)", text)
    assert installs, "run.sh no longer installs @anthropic-ai/claude-code"
    for suffix in installs:
        assert suffix.startswith("@"), f"unpinned install found: ...claude-code{suffix}"


def test_dockerfile_pins_the_same_version():
    text = (_REPO / "docker" / "Dockerfile.manyfold").read_text(encoding="utf-8")
    m = re.search(r"@anthropic-ai/claude-code@([0-9.]+)", text)
    assert m, "Dockerfile.manyfold no longer installs a pinned claude-code"
    assert m.group(1) == PINNED_CLI_VERSION, (
        f"Dockerfile pins {m.group(1)}, cli_binary pins {PINNED_CLI_VERSION}"
    )


# --- resolver: fail-open in every uncertain branch -------------------------


def test_gate_off_uses_bundled(monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", False)
    assert resolve_cli_path() is None


def test_missing_explicit_path_is_ignored_not_honoured(monkeypatch, tmp_path):
    """A typo'd CLAUDE_CLI_PATH must degrade to the bundled binary. Passing it
    through would surface as CLINotFoundError on every single turn."""
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", str(tmp_path / "nope"))
    assert resolve_cli_path() is None


def test_explicit_path_wins_when_it_exists(monkeypatch, tmp_path):
    from xyz_agent_context.settings import settings

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho 9.9.9\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", str(fake))
    assert resolve_cli_path() == str(fake)


def test_no_claude_on_path_uses_bundled(monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: None)
    assert resolve_cli_path() is None


def test_version_mismatch_uses_bundled(monkeypatch, tmp_path):
    """The pin's whole purpose: an unverified version is worse than the
    known-quantity bundled one."""
    from xyz_agent_context.settings import settings

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '1.0.0 (Claude Code)'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: str(fake))
    assert resolve_cli_path() is None


def test_matching_version_is_adopted(monkeypatch, tmp_path):
    from xyz_agent_context.settings import settings

    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\necho '{PINNED_CLI_VERSION} (Claude Code)'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: str(fake))
    assert resolve_cli_path() == str(fake)


def test_unreadable_version_uses_bundled(monkeypatch, tmp_path):
    """A binary that exists but cannot report a version is not trustworthy."""
    from xyz_agent_context.settings import settings

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho 'no version here'\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: str(fake))
    assert resolve_cli_path() is None


def test_probe_failure_never_raises(monkeypatch, tmp_path):
    """Anything the subprocess call can do — timeout, OSError — is absorbed."""
    from xyz_agent_context.settings import settings

    def _boom(*_a, **_k):
        raise OSError("simulated exec failure")

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: str(tmp_path / "claude"))
    monkeypatch.setattr(cli_binary.subprocess, "run", _boom)
    assert resolve_cli_path() is None


def test_decision_is_resolved_once(monkeypatch, tmp_path):
    """The probe spawns a subprocess; a hot agent loop must not pay it per
    turn."""
    from xyz_agent_context.settings import settings

    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\necho '{PINNED_CLI_VERSION}'\n")
    fake.chmod(0o755)
    calls = {"n": 0}
    real_run = cli_binary.subprocess.run

    def _counting(*a, **k):
        calls["n"] += 1
        return real_run(*a, **k)

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True)
    monkeypatch.setattr(settings, "claude_cli_path", "")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(cli_binary.subprocess, "run", _counting)

    first = resolve_cli_path()
    after = calls["n"]
    for _ in range(5):
        assert resolve_cli_path() == first
    assert calls["n"] == after, "resolver re-probed the binary on a cached call"
