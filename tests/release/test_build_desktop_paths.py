"""
@file_name: test_build_desktop_paths.py
@date: 2026-07-28
@description: Every repo path build-desktop.sh derives must actually exist.

The v1.12.0 dmg build failed at step 3.6 with "missing bundle manifest at
scripts/release/desktop-bundle". The 2026-07-24 layout cleanup moved
build-desktop.sh from scripts/ into scripts/release/, and one line resolved the
bundle manifest relative to the SCRIPT's directory rather than the repo root,
so it moved with the script while desktop-bundle/ stayed put.

Nothing caught it: the script only runs on a tag push, on a macOS runner, after
~4 minutes of Python/Node setup. The feedback loop was a failed release.

This test evaluates the script's own path assignments in a shell and asserts
each one points at something that exists — cheap, and it fails in CI the moment
a file moves out from under the script again (铁律 #24: moves must sweep
Path-join fragments, and a `$SCRIPT_DIR/...` is exactly that).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "build-desktop.sh"

# Variables the script derives that must resolve to an existing path. Excludes
# anything it CREATES during a build (resources/python, staging dirs) — those
# legitimately do not exist yet on a clean checkout.
MUST_EXIST = [
    "PROJECT_ROOT",
    "TAURI_DIR",
    "SRC_TAURI",
    "BUNDLE_MANIFEST_DIR",
]


def _resolve(varname: str) -> str:
    """Run the script's own preamble + the assignment, then echo the result.

    Sourcing the whole script is not an option (it builds a dmg), so this
    re-evaluates just the assignment lines, which is what we want to test.
    """
    text = SCRIPT.read_text()
    lines = [
        l for l in text.splitlines()
        # The preamble assignments plus any line defining the target var.
        if l.startswith(("SCRIPT_DIR=", "PROJECT_ROOT=", "TAURI_DIR=",
                         "SRC_TAURI=", "RESOURCES_DIR=", "BUNDLE_MANIFEST_DIR="))
    ]
    # SCRIPT_DIR is derived from $0, so $0 must be the real script path.
    # `bash -c <snippet> <name>` sets $0 to <name>; `bash -s` would leave it
    # as "bash" and silently resolve SCRIPT_DIR to the cwd instead.
    snippet = "\n".join(lines) + f'\necho "${varname}"\n'
    out = subprocess.run(
        ["bash", "-c", snippet, str(SCRIPT)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("varname", MUST_EXIST)
def test_derived_path_exists(varname: str) -> None:
    resolved = _resolve(varname)
    assert resolved, f"{varname} resolved to an empty string"
    assert Path(resolved).exists(), (
        f"build-desktop.sh derives {varname}={resolved!r}, which does not "
        f"exist. A file moved without updating the script — the release build "
        f"would fail on a macOS runner minutes in."
    )


def test_bundle_manifest_has_both_lockfiles() -> None:
    """npm ci needs both, and the script errors out without them."""
    d = Path(_resolve("BUNDLE_MANIFEST_DIR"))
    assert (d / "package.json").is_file()
    assert (d / "package-lock.json").is_file()


def test_bundle_manifest_is_not_under_the_script_dir() -> None:
    """Pin the exact regression: desktop-bundle/ is a sibling of release/,
    so anchoring it to SCRIPT_DIR is always wrong."""
    assert Path(_resolve("BUNDLE_MANIFEST_DIR")) == REPO / "scripts" / "desktop-bundle"
