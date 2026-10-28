"""
@file_name: test_executor_service_warmup.py
@date: 2026-08-20
@description: The executor's startup lifespan primes warm-runner pools for the
frameworks in EXECUTOR_PREWARM_FRAMEWORKS (default nexus_power) BEFORE serving,
so the process's first turn is not cold. Gated so ops can disable the per-
container ~350MB idle-runner cost. Best-effort: a warmup failure never stops the
executor from starting. A seam test (no mocks) pins that the resolved
nexus_power driver actually exposes warmup(), so a rename/proxy can't silently
kill the optimization.
"""
from fastapi.testclient import TestClient

from narranexus.platform.agent_runtime import executor_service


def test_startup_warms_nexus_power_pool_by_default(monkeypatch):
    monkeypatch.delenv("EXECUTOR_PREWARM_FRAMEWORKS", raising=False)
    seen = {"frameworks": [], "warmup": 0}

    class FakeDriver:
        def warmup(self):
            seen["warmup"] += 1

    def fake_get(framework, **kwargs):
        seen["frameworks"].append(framework)
        return FakeDriver()

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", fake_get)
    with TestClient(executor_service.app):  # entering triggers startup lifespan
        pass
    assert seen["frameworks"] == ["nexus_power"]
    assert seen["warmup"] == 1


def test_startup_skips_all_prewarm_when_disabled(monkeypatch):
    # EXECUTOR_PREWARM_FRAMEWORKS="" — ops disables prewarm on memory-pressured
    # hosts; the lifespan must not even resolve a driver.
    monkeypatch.setenv("EXECUTOR_PREWARM_FRAMEWORKS", "")
    seen = {"calls": 0}

    def fake_get(framework, **kwargs):
        seen["calls"] += 1
        return object()

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", fake_get)
    with TestClient(executor_service.app):
        pass
    assert seen["calls"] == 0


def test_startup_honors_custom_prewarm_list(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PREWARM_FRAMEWORKS", "nexus_power, claude_code")
    seen = {"frameworks": []}

    class FakeDriver:
        def warmup(self):
            pass

    def fake_get(framework, **kwargs):
        seen["frameworks"].append(framework)
        return FakeDriver()

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", fake_get)
    with TestClient(executor_service.app):
        pass
    assert seen["frameworks"] == ["nexus_power", "claude_code"]  # order + whitespace trimmed


def test_startup_survives_warmup_failure(monkeypatch):
    monkeypatch.delenv("EXECUTOR_PREWARM_FRAMEWORKS", raising=False)

    def boom(framework, **kwargs):
        raise RuntimeError("warmup blew up")

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", boom)
    # Must NOT raise — executor has to start even if warmup fails.
    with TestClient(executor_service.app):
        pass


def test_startup_tolerates_driver_without_warmup(monkeypatch):
    monkeypatch.delenv("EXECUTOR_PREWARM_FRAMEWORKS", raising=False)

    class NoWarmupDriver:  # e.g. a remote/other driver
        pass

    monkeypatch.setattr(
        executor_service, "get_agent_loop_driver", lambda framework, **kw: NoWarmupDriver()
    )
    with TestClient(executor_service.app):
        pass


def test_real_nexus_power_driver_exposes_warmup(monkeypatch):
    # Seam guard, NO mocks: the driver the lifespan actually resolves for
    # nexus_power must expose warmup(), or a rename/proxy would silently revert
    # first-turn latency to cold with CI still green. In-executor there is no
    # AGENT_EXECUTOR_URL, so this returns the in-process NexusAgent.
    monkeypatch.delenv("AGENT_EXECUTOR_URL", raising=False)
    driver = executor_service.get_agent_loop_driver("nexus_power")
    assert callable(getattr(driver, "warmup", None))
