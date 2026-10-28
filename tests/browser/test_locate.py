"""
@file_name: test_locate.py
@author:
@date: 2026-09-22
@description: Tests for locating the installed browser runtime and probing it.

These are the two IO seams `detect_runtime` takes. They are thin on purpose,
but two behaviours here are worth pinning because getting them wrong produces
the exact failure users cannot self-diagnose (a blank panel):

* the locator must not return a directory or a non-executable file;
* the probe must treat a non-zero exit as failure even when the process
  printed something to stdout.

Filesystem only; no process is actually spawned (the runner is injected).
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path

from narranexus.platform.browser._browser_impl.locate import (
    install_root,
    locate_executable,
    probe_version,
)


@pytest.fixture(autouse=True)
def mac_platform(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")


# ── locate_executable ────────────────────────────────────────────────────────


def test_returns_none_when_root_missing(tmp_path: Path):
    assert locate_executable(root=tmp_path / "nope") is None


def test_returns_none_when_candidate_absent(tmp_path: Path):
    (tmp_path / "Chromium.app" / "Contents" / "MacOS").mkdir(parents=True)
    assert locate_executable(root=tmp_path) is None


def test_finds_the_macos_bundle_executable(tmp_path: Path):
    exe = tmp_path / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)

    assert locate_executable(root=tmp_path) == exe


def test_ignores_a_directory_that_matches_the_name(tmp_path: Path):
    """A half-extracted archive can leave a directory where the binary goes."""
    d = tmp_path / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    d.mkdir(parents=True)

    assert locate_executable(root=tmp_path) is None


def test_ignores_a_file_without_the_executable_bit(tmp_path: Path):
    """An interrupted download leaves a non-executable file; reporting it as
    found sends the user to a blank panel instead of the install card."""
    exe = tmp_path / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    exe.parent.mkdir(parents=True)
    exe.write_text("partial")
    exe.chmod(0o644)

    assert locate_executable(root=tmp_path) is None


def test_prefers_the_first_matching_layout(tmp_path: Path):
    """Vendor archive layouts differ between versions; the first match wins so
    an upgrade that adds a layout does not become ambiguous."""
    old = tmp_path / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    new = tmp_path / "chrome-mac-arm64" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    for p in (old, new):
        p.parent.mkdir(parents=True)
        p.write_text("x")
        p.chmod(0o755)

    assert locate_executable(root=tmp_path) == old


# ── install_root ─────────────────────────────────────────────────────────────


def test_install_root_is_under_the_user_data_dir():
    root = install_root()
    assert root.is_absolute()
    # Must NOT live inside the app bundle: the runtime survives app upgrades
    # and a bundle path would be wiped (and is read-only once notarised).
    assert ".app/Contents" not in str(root)


def test_install_root_honours_env_override(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", "/tmp/custom-browser-home")
    assert install_root() == Path("/tmp/custom-browser-home")


def test_install_root_ignores_blank_override(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", "   ")
    assert install_root() != Path("   ")
    assert install_root().is_absolute()


# ── probe_version ────────────────────────────────────────────────────────────


def test_probe_returns_trimmed_stdout_on_success():
    def run(_argv):
        return 0, "  Chromium 152.0.7977.54 \n", ""

    assert probe_version(Path("/x/chrome"), runner=run) == "Chromium 152.0.7977.54"


def test_probe_returns_none_on_nonzero_exit_even_with_output():
    """A browser that prints a banner and then dies is not usable. Trusting
    stdout alone is how `ready` gets reported for a broken runtime."""

    def run(_argv):
        return 1, "Chromium 152", "dyld: missing library"

    assert probe_version(Path("/x/chrome"), runner=run) is None


def test_probe_returns_none_on_empty_stdout():
    def run(_argv):
        return 0, "   \n", ""

    assert probe_version(Path("/x/chrome"), runner=run) is None


def test_probe_returns_none_when_runner_raises():
    def run(_argv):
        raise OSError("Gatekeeper killed it")

    assert probe_version(Path("/x/chrome"), runner=run) is None


def test_probe_passes_version_flag():
    seen: list[list[str]] = []

    def run(argv):
        seen.append(argv)
        return 0, "Chromium 1", ""

    probe_version(Path("/x/chrome"), runner=run)
    assert seen == [["/x/chrome", "--version"]]


def test_probe_does_not_inherit_a_hostile_environment():
    """Sanity: the probe must be a plain argv exec, not a shell string, so a
    path with spaces or shell metacharacters cannot be interpreted."""
    seen: list[list[str]] = []

    def run(argv):
        seen.append(argv)
        return 0, "Chromium 1", ""

    weird = Path("/x/my browser; rm -rf ~/chrome")
    probe_version(weird, runner=run)
    assert seen[0][0] == str(weird)
    assert len(seen[0]) == 2
    assert os.sep in seen[0][0]


# ── real vendor layouts (verified against a downloaded archive) ──────────────


CFT_MAC = ("chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS",
           "Google Chrome for Testing")


def _make_exe(root: Path, parts: tuple[str, ...]) -> Path:
    exe = root.joinpath(*parts)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def test_finds_the_chrome_for_testing_layout(tmp_path: Path):
    """The layout an actual Chrome for Testing archive unpacks to (verified
    2026-09-22 against a real 191 MB download). The first guess at this path
    was wrong and the install reported 'no executable found' — which was the
    right refusal, but for a reason that was ours."""
    exe = _make_exe(tmp_path, CFT_MAC)
    assert locate_executable(root=tmp_path) == exe


def test_helper_processes_are_not_mistaken_for_the_browser(tmp_path: Path):
    """The same archive ships several `... Helper.app/Contents/MacOS/...`
    binaries. Launching one of those would start a renderer with no browser
    around it — a failure with no obvious cause."""
    helper = (
        "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "Frameworks",
        "Google Chrome for Testing Framework.framework", "Versions", "153.0.8010.52",
        "Helpers", "Google Chrome for Testing Helper (GPU).app", "Contents", "MacOS",
        "Google Chrome for Testing Helper (GPU)",
    )
    _make_exe(tmp_path, helper)

    assert locate_executable(root=tmp_path) is None, "a helper is not the browser"


def test_the_browser_wins_when_helpers_are_present_too(tmp_path: Path):
    exe = _make_exe(tmp_path, CFT_MAC)
    _make_exe(tmp_path, (
        "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "Frameworks",
        "F.framework", "Versions", "1", "Helpers", "H.app", "Contents", "MacOS", "H",
    ))
    assert locate_executable(root=tmp_path) == exe


def test_finds_the_linux_layout(tmp_path: Path):
    exe = _make_exe(tmp_path, ("chrome-linux64", "chrome"))
    assert locate_executable(root=tmp_path, plat="linux64") == exe


def test_locator_selects_native_architecture(monkeypatch, tmp_path):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    _make_exe(tmp_path, CFT_MAC)
    native = _make_exe(tmp_path, ("chrome-mac-x64", *CFT_MAC[1:]))
    assert locate_executable(root=tmp_path) == native


def test_locator_rejects_foreign_platform(monkeypatch, tmp_path):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    _make_exe(tmp_path, CFT_MAC)
    _make_exe(tmp_path, ("chrome-linux64", "chrome"))
    assert locate_executable(root=tmp_path) is None


@pytest.mark.parametrize("output", ["error loading framework", "Python 3.13", "Chrome failed"])
def test_probe_rejects_non_browser_output(output):
    assert probe_version(Path("/chrome"), runner=lambda _: (0, output, "")) is None


def test_receipt_with_deleted_generation_does_not_fall_back_to_stale_browser(tmp_path):
    import json
    _make_exe(tmp_path, CFT_MAC)
    (tmp_path / ".runtime-mac-arm64.json").write_text(json.dumps({"executable": "missing/chrome"}))
    assert locate_executable(root=tmp_path) is None


def test_relative_home_override_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", "custom-browser")
    expected = Path.home() / "custom-browser"
    monkeypatch.chdir(tmp_path)
    assert install_root() == expected


def test_windows_probe_reads_rendered_version_page(monkeypatch):
    import platform
    import subprocess
    from types import SimpleNamespace
    from narranexus.platform.browser._browser_impl.locate import _default_runner

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='<span id="version">153.0.8010.52</span>', stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert _default_runner(["C:/Browser/chrome.exe", "--version"]) == (0, "Chromium 153.0.8010.52", "")
    assert "--headless=new" in calls[0]
    assert "--dump-dom" in calls[0]
    assert calls[0][-1] == "chrome://version"
    assert any(arg.startswith("--user-data-dir=") for arg in calls[0])


def test_flattened_framework_link_is_not_ready_even_if_version_probe_answers(tmp_path):
    executable = _make_exe(tmp_path, CFT_MAC)
    versions = executable.parent.parent / "Frameworks/Google Chrome for Testing Framework.framework/Versions"
    versions.mkdir(parents=True)
    (versions / "153.0.8010.52").mkdir()
    (versions / "Current").write_text("153.0.8010.52")
    assert probe_version(executable, runner=lambda _: (0, "Chromium 153.0.8010.52", "")) is None
    (versions / "Current").unlink()
    (versions / "Current").symlink_to("153.0.8010.52")
    assert probe_version(executable, runner=lambda _: (0, "Chromium 153.0.8010.52", "")) is not None
