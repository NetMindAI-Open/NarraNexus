"""
@file_name: test_launcher.py
@author:
@date: 2026-09-22
@description: Tests for Chromium launch arguments and CDP endpoint discovery.

The argv is a security surface, not a formatting detail: it decides whether
the browser runs with web security on, where its profile lives, and whether a
debugging port is exposed beyond loopback. Those are pinned here.

Endpoint discovery is tested for the failure that actually happens — the port
is open before the browser is ready to answer, so a naive "connect once" is
flaky in exactly the way that produces a blank panel on slow machines.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.launcher import (
    build_launch_args,
    pick_profile_dir,
    wait_for_endpoint,
)


# ── launch arguments ─────────────────────────────────────────────────────────


def test_debug_port_is_bound_to_loopback_only():
    """An unbound debugging port is remote code execution on the user's
    machine for anyone on the LAN."""
    args = build_launch_args(executable=Path("/x/chrome"), port=9333, profile=Path("/p"))
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=9333" in args


def test_profile_dir_is_passed_so_logins_persist():
    args = build_launch_args(executable=Path("/x/chrome"), port=1, profile=Path("/p/profile"))
    assert "--user-data-dir=/p/profile" in args


def test_executable_is_argv_zero():
    args = build_launch_args(executable=Path("/x/chrome"), port=1, profile=Path("/p"))
    assert args[0] == "/x/chrome"


def test_web_security_is_not_disabled():
    """Turning off web security would let any page the agent visits read any
    other origin it has cookies for."""
    args = build_launch_args(executable=Path("/x/chrome"), port=1, profile=Path("/p"))
    assert not any("disable-web-security" in a for a in args)


def test_no_automation_banner_flags_that_would_break_normal_sites():
    args = build_launch_args(executable=Path("/x/chrome"), port=1, profile=Path("/p"))
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args


def test_headed_by_default_because_the_panel_streams_a_rendered_page():
    """Screencast needs a compositor. This is the ego-lite lesson: a browser
    that never paints produces exactly one frame and then nothing."""
    args = build_launch_args(executable=Path("/x/chrome"), port=1, profile=Path("/p"))
    assert "--headless=new" not in args


def test_headless_is_opt_in():
    args = build_launch_args(
        executable=Path("/x/chrome"), port=1, profile=Path("/p"), headless=True
    )
    assert "--headless=new" in args


def test_extra_args_are_appended_not_substituted():
    args = build_launch_args(
        executable=Path("/x/chrome"), port=1, profile=Path("/p"), extra=["--lang=zh-CN"]
    )
    assert "--lang=zh-CN" in args
    assert "--remote-debugging-port=1" in args


# ── profile directory ────────────────────────────────────────────────────────


def test_profiles_are_isolated_per_name(tmp_path: Path):
    a = pick_profile_dir(root=tmp_path, profile="default")
    b = pick_profile_dir(root=tmp_path, profile="work")
    assert a != b
    assert a.parent == b.parent


def test_profile_name_cannot_escape_the_root(tmp_path: Path):
    """A profile name reaches this from config; a traversal would let it
    point the browser's data dir at an arbitrary directory."""
    p = pick_profile_dir(root=tmp_path, profile="../../etc")
    assert tmp_path in p.parents or p.parent == tmp_path


def test_blank_profile_name_falls_back_to_default(tmp_path: Path):
    assert pick_profile_dir(root=tmp_path, profile="  ") == pick_profile_dir(
        root=tmp_path, profile="default"
    )


# ── endpoint discovery ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_endpoint_retries_until_the_browser_answers():
    """The port opens before the browser can answer; one attempt is flaky."""
    attempts = {"n": 0}

    async def probe():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("not up yet")
        return "ws://127.0.0.1:9333/devtools/browser/abc"

    url = await wait_for_endpoint(probe=probe, timeout=2.0, interval=0.01)

    assert url.endswith("/abc")
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_wait_for_endpoint_gives_up_with_a_useful_error():
    async def probe():
        raise ConnectionError("refused")

    with pytest.raises(TimeoutError, match="refused"):
        await wait_for_endpoint(probe=probe, timeout=0.05, interval=0.01)


@pytest.mark.asyncio
async def test_wait_for_endpoint_returns_immediately_when_already_up():
    async def probe():
        return "ws://127.0.0.1:1/devtools/browser/x"

    assert await wait_for_endpoint(probe=probe, timeout=1.0, interval=0.5)


# ── profile isolation between agents ────────────────────────────────────────


def test_two_agents_do_not_share_a_profile_directory(tmp_path: Path):
    """Chromium takes a SingletonLock on its `--user-data-dir`. Two agents on
    one directory means the second browser simply refuses to start, and the
    error it produces ("no CDP endpoint within 30s") points nowhere near the
    cause. Observed 2026-09-22 with two sessions both on `default`.
    """
    a = pick_profile_dir(root=tmp_path, profile="default", agent_id="agent_a")
    b = pick_profile_dir(root=tmp_path, profile="default", agent_id="agent_b")
    assert a != b


def test_one_agents_named_profiles_stay_separate(tmp_path: Path):
    """Two logins for the same agent are two identities; sharing a cookie jar
    would silently merge them."""
    work = pick_profile_dir(root=tmp_path, profile="work", agent_id="agent_a")
    home = pick_profile_dir(root=tmp_path, profile="home", agent_id="agent_a")
    assert work != home


def test_the_same_agent_and_profile_is_stable_across_calls(tmp_path: Path):
    """Login state lives in this directory; a new path each launch would throw
    the user's session away every time."""
    first = pick_profile_dir(root=tmp_path, profile="work", agent_id="agent_a")
    second = pick_profile_dir(root=tmp_path, profile="work", agent_id="agent_a")
    assert first == second


def test_an_agent_id_cannot_escape_the_root(tmp_path: Path):
    p = pick_profile_dir(root=tmp_path, profile="default", agent_id="../../etc")
    assert tmp_path in p.parents or p.parent == tmp_path or tmp_path in p.parents


def test_profiles_still_work_without_an_agent_id(tmp_path: Path):
    assert pick_profile_dir(root=tmp_path, profile="default") is not None
