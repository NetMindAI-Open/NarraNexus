"""
@file_name: test_plugins_extra_lockstep.py
@author: NarraNexus
@date: 2026-08-28
@description: Executable guard for the `--extra plugins` lockstep.

The coding-agent SDKs (claude-agent-sdk / openai-codex) live in the optional
`[project.optional-dependencies].plugins` extra, kept OUT of the base install so
the local build stays light. Every place that must RUN a framework has to pull
them back in with `uv sync ... --extra plugins`; every LOCAL place must NOT
(that is the whole point of the slim-down).

This test scans every GIT-TRACKED `.sh` / `.yml` / `Dockerfile*` / Makefile for
lines that INSTALL this project with uv (`uv sync` or `uv pip install`) and
requires each such file to be explicitly classified below. A NEW deploy/CI entry
point — and forgets to classify itself — fails
`test_every_uv_install_file_is_classified` once committed (an un-added file is
invisible to `git ls-files`, but by CI/review time it is committed, so the
guard's effective coverage is unchanged) instead of silently shipping
claude_code / codex_cli dead. Same "mirror a fact + assert they agree" shape as
test_claude_cli_pin.py.

`uv pip install` counts too, and not only for the extra: uv is the ONLY
installer that understands `[tool.uv.sources]`, so an install path that reaches
for plain `pip` cannot resolve the 30 workspace members at all (the desktop DMG
build shipped exactly that bug). `test_desktop_build_installs_through_uv` pins
that one down by name because its failure is invisible until a release tag
exists.

NOTE: the deploy repo's Dockerfile.executor / Dockerfile.python are the OTHER
half of this lockstep; they live in a separate repo and are guarded on that side.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._shell_commands import command_lines

_REPO = Path(__file__).resolve().parents[2]

_DESKTOP_BUILD = "scripts/release/build-desktop.sh"

# CLOUD/CI: `uv sync` here runs the agent frameworks → MUST carry --extra plugins.
_CLOUD_SYNC_FILES = frozenset({
    ".github/workflows/ci.yml",
    "docker/Dockerfile.manyfold",
    "scripts/release/deploy-cloud.sh",
})
# LOCAL: the lightweight path → `uv sync` MUST NOT carry --extra plugins.
_LOCAL_SYNC_FILES = frozenset({
    "run.sh",
    "scripts/dev/.dev-local-safe.sh",
    "scripts/dev/dev-local.sh",
    # The macOS DMG: installs into the BUNDLED standalone interpreter, and the
    # bundle must stay light (claude-agent-sdk alone is ~186 MB).
    _DESKTOP_BUILD,
})


def _is_command_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith((".sh", ".yml", ".yaml")) or name.startswith("Dockerfile") or name == "Makefile"


def _uv_sync_command_lines(path: Path) -> list[str]:
    """Only the `uv sync` lines — the ones the `--extra plugins` rule is about."""
    return command_lines(path, "uv sync")


def _uv_install_command_lines(path: Path) -> list[str]:
    """Every line that installs this project with uv, sync or pip alike.

    Deliberately does NOT include `uv export`: this is what
    `test_every_uv_install_file_is_classified` enumerates over, and
    verify_release_artifacts.sh runs an export purely to PARSE the lock — it
    installs nothing and belongs in neither the cloud nor the local list.
    """
    return command_lines(path, "uv sync", "uv pip install")


def _uv_light_build_lines(path: Path) -> list[str]:
    """Lines that decide WHAT a local build installs.

    Since 2026-09-10 the desktop build materializes uv.lock first, so the extras
    selection (no `--extra plugins`) and the dev-group exclusion (`--no-dev`)
    live on the `uv export` line, not on the install. A needle set that only
    knows `uv sync` / `uv pip install` cannot see them, and the one assertion
    keeping claude-agent-sdk out of the dmg would pass over an export that pulls
    it in.
    """
    return command_lines(path, "uv sync", "uv pip install", "uv export")


def _all_files_running_uv_sync() -> dict[str, list[str]]:
    # Enumerate GIT-TRACKED files only: a `.worktrees/<name>/` (this repo's own
    # parallel-work dir), node_modules, .venv, tauri/target etc. are all
    # gitignored, so `git ls-files` excludes them for free — no manual skip list
    # and no full-tree walk (which would traverse hundreds of thousands of
    # node_modules files just to discard them).
    try:
        listed = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout; cannot enumerate tracked files")
    found: dict[str, list[str]] = {}
    for rel in listed.split("\0"):
        if not rel or not _is_command_file(rel):
            continue
        path = _REPO / rel
        if not path.is_file():
            continue
        lines = _uv_install_command_lines(path)
        if lines:
            found[rel] = lines
    return found


def test_every_uv_install_file_is_classified():
    """A repo file that installs this project with uv MUST be in exactly one of
    the two lists — this is what makes a NEW forgotten entry point fail here."""
    classified = _CLOUD_SYNC_FILES | _LOCAL_SYNC_FILES
    unclassified = sorted(set(_all_files_running_uv_sync()) - classified)
    assert not unclassified, (
        "these files install the project with uv but are not classified cloud/local in "
        "test_plugins_extra_lockstep.py — classify them:\n  " + "\n  ".join(unclassified)
    )


@pytest.mark.parametrize("rel", sorted(_CLOUD_SYNC_FILES))
def test_cloud_sync_pulls_plugins_extra(rel):
    lines = _uv_sync_command_lines(_REPO / rel)
    assert lines, f"{rel} no longer runs `uv sync` — update this guard"
    for line in lines:
        assert "--extra plugins" in line, (
            f"{rel}: cloud/CI `uv sync` missing `--extra plugins` → claude_code /"
            f" codex_cli will break there:\n    {line}"
        )


@pytest.mark.parametrize("rel", sorted(_LOCAL_SYNC_FILES))
def test_local_sync_stays_light(rel):
    lines = _uv_light_build_lines(_REPO / rel)
    assert lines, f"{rel} no longer installs with uv — update this guard (renamed?)"
    for line in lines:
        assert "--extra plugins" not in line, (
            f"{rel}: local uv install pulls `--extra plugins`, defeating the "
            f"lightweight build:\n    {line}"
        )


def test_desktop_build_installs_through_uv():
    """The DMG's Python install must go through uv and must not go through pip.

    `[tool.uv.sources]` is a uv-only table: plain `pip install <project>` looks
    the 30 workspace members up on PyPI, where they do not exist, and the
    release job dies at Step 3 — AFTER the tag is pushed. Nothing else in the
    repo catches it, because every other install path already uses uv.
    """
    path = _REPO / _DESKTOP_BUILD
    installs = _uv_install_command_lines(path)
    assert installs, f"{_DESKTOP_BUILD} no longer installs the project — update this guard"
    # `--no-editable` must be on the INSTALL line itself. #390 relaxed this to
    # "on the install OR the export" when step 3 started exporting uv.lock
    # first — and that relaxation is precisely what let v1.21.3 ship broken:
    # the export carried the flag, the install did not, `uv pip install -r`
    # installed all 30 workspace members as editable `.pth` hooks pointing at
    # the build machine's source tree, and every user got
    # `No module named 'narranexus.contracts'`. The flag on the export does not
    # propagate to the install; only the install decides.
    assert all("--no-editable" in line for line in installs), (
        f"{_DESKTOP_BUILD}: every bundled install must be NON-editable — an editable "
        f"install bakes the build machine's absolute source path into the .app "
        f"(v1.21.3). The flag has to be on the install, not only the export:\n    "
        + "\n    ".join(installs)
    )
    pip_installs = [
        line for line in command_lines(path, "pip install")
        if "uv pip install" not in line
    ]
    assert not pip_installs, (
        f"{_DESKTOP_BUILD}: plain `pip install` cannot resolve the "
        f"[tool.uv.sources] workspace members — use uv:\n    " + "\n    ".join(pip_installs)
    )


def test_desktop_workflow_installs_uv():
    """...and the runner that executes it has uv at all (otherwise the fix above
    fails differently: `uv: command not found`)."""
    wf = (_REPO / ".github/workflows/build-desktop.yml").read_text(encoding="utf-8")
    assert "astral-sh/setup-uv" in wf, (
        "build-desktop.yml runs build-desktop.sh, which now needs uv on PATH; "
        "add the astral-sh/setup-uv step back"
    )
