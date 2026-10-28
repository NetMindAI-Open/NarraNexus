"""
@file_name: test_browser_service.py
@author:
@date: 2026-09-22
@description: Tests for BrowserService — the one surface the UI and the agent talk to.

This is the shared readiness gate: three callers (the agent's MCP
tools, the stream renderer, the ContextData hook) all ask the same object
"can we browse right now?" and must get the same answer, shaped so each can
degrade usefully rather than fail opaquely.

What is pinned here:

* status is recomputed per call — a user can delete the runtime between two
  turns, and a cached `ready` sends the next one to a blank panel;
* `require_ready()` returns a NEEDS_HUMAN-shaped refusal, never an exception,
  because its caller is an agent turn;
* install is delegated, not reimplemented (the concurrency rules live in the
  coordinator and must not be duplicated here).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.install import InstallOutcome
from narranexus.platform.browser.browser_service import BrowserService

EXE = Path("/tmp/nx-browser/chrome")


def make_service(*, found=EXE, version="Chromium 152", install_result=None) -> BrowserService:
    """A service whose IO seams are fixtures, so nothing here touches disk."""
    state = {"found": found, "version": version}

    async def downloader(*, on_progress, cancelled):
        # A successful install flips the fixture, the way a real one would.
        state["found"] = EXE
        state["version"] = "Chromium 152"
        return install_result or InstallOutcome(ok=True, executable=EXE, error=None)

    return BrowserService(
        locate=lambda: state["found"],
        probe=lambda _p: state["version"],
        downloader=downloader,
    )


# ── status ───────────────────────────────────────────────────────────────────


def test_status_ready_when_located_and_probed():
    assert make_service().status().state == "ready"


def test_status_absent_when_nothing_installed():
    assert make_service(found=None, version=None).status().state == "absent"


def test_status_absent_when_probe_fails():
    s = make_service(version=None).status()
    assert s.state == "absent"
    assert s.reason == "probe-failed"


def test_status_is_recomputed_not_cached():
    """Design §8.1 — the user can delete the runtime between two calls."""
    state = {"found": EXE}

    async def dl(*, on_progress, cancelled):
        return InstallOutcome(ok=True, executable=EXE, error=None)

    svc = BrowserService(
        locate=lambda: state["found"], probe=lambda _p: "Chromium 152", downloader=dl
    )
    assert svc.status().state == "ready"
    state["found"] = None
    assert svc.status().state == "absent"


def test_status_dict_is_api_shaped():
    d = make_service().status().to_dict()
    assert set(d) == {"state", "reason", "version", "executable"}


# ── require_ready: the agent-facing gate ─────────────────────────────────────


def test_require_ready_passes_when_ready():
    refusal = make_service().require_ready()
    assert refusal is None


def test_require_ready_refuses_with_needs_human_when_absent():
    """The agent must get something it can relay to the user, not an error."""
    refusal = make_service(found=None, version=None).require_ready()

    assert refusal is not None
    assert refusal["outcome"] == "NEEDS_HUMAN"
    assert refusal["human_action"]["action"] == "install_browser"
    assert isinstance(refusal["message"], str) and refusal["message"]


def test_require_ready_refusal_names_the_reason():
    """`probe-failed` and `no-executable` need different advice: re-install vs
    install. Collapsing them loses the only clue the user has."""
    refusal = make_service(version=None).require_ready()
    assert refusal is not None
    assert refusal["human_action"]["reason"] == "probe-failed"


def test_require_ready_never_raises_even_if_seams_explode():
    def boom():
        raise OSError("disk gone")

    async def dl(*, on_progress, cancelled):
        return InstallOutcome(ok=True, executable=None, error=None)

    svc = BrowserService(locate=boom, probe=lambda _p: "x", downloader=dl)
    refusal = svc.require_ready()
    assert refusal is not None
    assert refusal["outcome"] == "NEEDS_HUMAN"


def test_require_ready_reports_installing_distinctly():
    """While a download is in flight the user should be told to wait, not told
    to start another install."""
    svc = make_service(found=None, version=None)
    svc._installer._cancelled = False  # noqa: SLF001 - drive the flag directly

    class Busy:
        installing = True
        progress = None

    svc._installer = Busy()  # type: ignore[assignment]  # noqa: SLF001
    refusal = svc.require_ready()
    assert refusal is not None
    assert refusal["human_action"]["action"] == "wait_for_install"


# ── install delegation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_makes_the_runtime_ready():
    svc = make_service(found=None, version=None)
    assert svc.status().state == "absent"

    out = await svc.install()

    assert out.ok is True
    assert svc.status().state == "ready"


@pytest.mark.asyncio
async def test_install_is_idempotent_via_the_coordinator():
    calls = {"n": 0}

    async def dl(*, on_progress, cancelled):
        calls["n"] += 1
        return InstallOutcome(ok=True, executable=EXE, error=None)

    svc = BrowserService(locate=lambda: EXE, probe=lambda _p: "Chromium 152", downloader=dl)
    await svc.install()
    await svc.install()

    assert calls["n"] == 0, "already ready — must not download"


@pytest.mark.asyncio
async def test_failed_install_surfaces_the_error():
    async def dl(*, on_progress, cancelled):
        raise RuntimeError("mirror unreachable")

    svc = BrowserService(locate=lambda: None, probe=lambda _p: None, downloader=dl)
    out = await svc.install()

    assert out.ok is False
    assert "mirror unreachable" in (out.error or "")


# ── live session registry ────────────────────────────────────────────────────


class FakeSession:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.closed = False

    async def close(self):
        self.closed = True
        self.is_open = False


def launched_session(kwargs):
    from narranexus.platform.browser._browser_impl.session import BrowserSession
    from tests.browser.test_session import FakeCdp

    return BrowserSession(cdp=FakeCdp(), policy=kwargs["policy"], audit=kwargs["audit"],
                          turn_id=kwargs["turn_id"], thread_id=kwargs["thread_id"])


def test_session_for_returns_none_when_none_registered():
    assert make_service().session_for("agent_1") is None


def test_registered_session_is_returned():
    svc = make_service()
    s = FakeSession()
    svc.register_session("agent_1", s)
    assert svc.session_for("agent_1") is s


def test_sessions_are_per_agent():
    svc = make_service()
    a, b = FakeSession(), FakeSession()
    svc.register_session("agent_a", a)
    svc.register_session("agent_b", b)
    assert svc.session_for("agent_a") is a
    assert svc.session_for("agent_b") is b


def test_a_closed_session_is_dropped_not_handed_back():
    """Returning a dead object turns 'the browser closed' into an unrelated
    error at whatever call site touches it next."""
    svc = make_service()
    svc.register_session("agent_1", FakeSession(is_open=False))
    assert svc.session_for("agent_1") is None
    assert svc.session_for("agent_1") is None  # and stays dropped


@pytest.mark.asyncio
async def test_close_session_closes_and_forgets():
    svc = make_service()
    s = FakeSession()
    svc.register_session("agent_1", s)

    await svc.close_session("agent_1")

    assert s.closed is True
    assert svc.session_for("agent_1") is None


@pytest.mark.asyncio
async def test_closing_an_unknown_session_is_a_noop():
    await make_service().close_session("nobody")


# ── the cross-process refresh ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fresh_read_picks_up_a_decision_made_elsewhere():
    """The session process must see a permission the owner changed through the
    API process. Whatever is stored wins over whatever this process cached."""
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy, decide

    svc = make_service()
    stored = {"doc": BrowserPolicy().to_dict()}

    async def fake_load(_agent_id):
        return stored["doc"]

    # Stand in for the repository read.
    async def policy_for(agent_id, *, fresh=False):
        if not fresh and agent_id in svc._policies:  # noqa: SLF001
            return svc._policies[agent_id]  # noqa: SLF001
        p = BrowserPolicy.from_dict(await fake_load(agent_id))
        svc._policies[agent_id] = p  # noqa: SLF001
        return p

    svc.policy_for = policy_for  # type: ignore[assignment]

    first = await svc.policy_for("a1")
    assert decide(first, url="https://x.example/", capability="full_cdp_access").verdict == "deny"

    # The owner allows scripts on that origin through Settings (another process).
    stored["doc"] = BrowserPolicy(origins={"https://x.example": OriginPolicy(full_cdp_access="allow")}).to_dict()

    assert (await svc.policy_for("a1")) is first  # the cache alone cannot see it
    refreshed = await svc.policy_for("a1", fresh=True)
    assert decide(refreshed, url="https://x.example/", capability="full_cdp_access").verdict == "allow"


@pytest.mark.asyncio
async def test_an_explicitly_passed_policy_is_not_replaced_by_a_refresh():
    """`open_session(policy=...)` means "use exactly this". Installing the
    stored-policy provider on top of it silently discarded the caller's
    policy — and when the store was unreachable the refresh handed back an
    empty document, turning an explicit allow into a deny."""
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy

    svc = make_service()
    explicit = BrowserPolicy(origins={"https://ok.example": OriginPolicy(full_cdp_access="allow")})

    captured = {}

    async def fake_launch(**kwargs):
        captured.update(kwargs)
        return launched_session(kwargs)

    import narranexus.platform.browser._browser_impl.runtime_launch as rl

    original = rl.launch_session
    rl.launch_session = fake_launch  # type: ignore[assignment]
    try:
        session, refusal = await svc.open_session("a1", policy=explicit)
    finally:
        rl.launch_session = original  # type: ignore[assignment]
        await svc.close()

    assert refusal is None
    assert captured["policy"] is explicit
    assert captured["policy_provider"] is None, (
        "an explicit policy must not be overridden by a background refresh"
    )


@pytest.mark.asyncio
async def test_sessions_are_headless_by_default(tmp_path, monkeypatch):
    """The page is shown inside the app. A visible OS window is also one the
    user can drive directly — outside the control arbiter and the policy gate
    — so it is an opt-in, never the default."""
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(tmp_path))
    svc = make_service()
    captured = {}

    async def fake_launch(**kwargs):
        captured.update(kwargs)
        return launched_session(kwargs)

    import narranexus.platform.browser._browser_impl.runtime_launch as rl

    original = rl.launch_session
    rl.launch_session = fake_launch  # type: ignore[assignment]
    try:
        await svc.open_session("a1")
    finally:
        rl.launch_session = original  # type: ignore[assignment]
        await svc.close()

    assert captured["headless"] is True


@pytest.mark.asyncio
async def test_a_visible_window_can_still_be_asked_for():
    svc = make_service()
    captured = {}

    async def fake_launch(**kwargs):
        captured.update(kwargs)
        return launched_session(kwargs)

    import narranexus.platform.browser._browser_impl.runtime_launch as rl

    original = rl.launch_session
    rl.launch_session = fake_launch  # type: ignore[assignment]
    try:
        await svc.open_session("a1", headless=False)
    finally:
        rl.launch_session = original  # type: ignore[assignment]
        await svc.close()

    assert captured["headless"] is False
