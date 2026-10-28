"""
@file_name: test_runtime_selection.py
@date: 2026-09-23
@description: Explicit runtime selection without personal-profile reuse or live-session replacement.
"""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from narranexus.platform.browser._browser_impl import selection
from narranexus.platform.browser.browser_service import BrowserService


@pytest.fixture
def runtime_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(tmp_path / "runtime"))
    system = tmp_path / "Google Chrome"
    system.write_text("fixture")
    system.chmod(0o755)
    monkeypatch.setattr(selection, "system_candidates", lambda: [system])
    monkeypatch.setattr(selection, "probe_version", lambda path: "Google Chrome 153.0.0.0")
    return tmp_path / "runtime", system


def test_default_does_not_select_or_install_a_system_browser(runtime_selection):
    root, _ = runtime_selection
    assert selection.read_source() == "managed"
    assert selection.locate_selected() is None
    assert not root.exists()


def test_selection_is_visible_to_an_existing_service_without_restart(runtime_selection):
    root, system = runtime_selection
    service = BrowserService(probe=lambda path: "Google Chrome 153.0.0.0")
    assert service.status().state == "absent"
    selection.save_source("system")
    assert selection.read_source() == "system"
    assert service.status().executable == system
    assert selection.source_view()["system_executable"] == str(system)
    assert (root / "runtime-source.json").is_file()
    selection.save_source("managed")
    assert service.status().state == "absent"


def test_unavailable_or_broken_system_browser_cannot_replace_the_preference(runtime_selection, monkeypatch):
    _, system = runtime_selection
    monkeypatch.setattr(selection, "probe_version", lambda path: None)
    with pytest.raises(ValueError, match="could not start"):
        selection.save_source("system")
    assert selection.read_source() == "managed"
    system.unlink()
    with pytest.raises(ValueError, match="not found"):
        selection.save_source("system")
    assert selection.read_source() == "managed"


def test_a_missing_selected_browser_never_silently_falls_back(runtime_selection, monkeypatch):
    _, system = runtime_selection
    selection.save_source("system")
    monkeypatch.setattr(selection, "locate_executable", lambda: Path("/managed/chrome"))
    system.unlink()
    assert selection.locate_selected() is None
    assert selection.source_view()["source"] == "system"


@pytest.mark.parametrize("value", ['{"source":"/bin/sh"}', '{"source":"system","executable":"/bin/sh"}', '[]', '{broken'])
def test_invalid_preference_is_reported_without_fallback(runtime_selection, value):
    root, _ = runtime_selection
    root.mkdir()
    (root / "runtime-source.json").write_text(value)
    with pytest.raises(ValueError, match="Invalid browser source"):
        selection.read_source()


def test_cloud_cannot_select_a_host_browser(runtime_selection, monkeypatch):
    root, _ = runtime_selection
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert selection.source_view()["editable"] is False
    with pytest.raises(PermissionError):
        selection.save_source("system")
    assert not root.exists()


def test_mode_persists_across_processes_without_changing_the_source(runtime_selection):
    root, _ = runtime_selection
    assert selection.read_mode() == "headless"
    assert not root.exists()
    selection.save_source("system")
    selection.save_mode("headed")
    result = subprocess.run(
        [sys.executable, "-c", "from narranexus.platform.browser._browser_impl.selection import read_mode; print(read_mode())"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "headed"
    assert selection.read_source() == "system"
    assert (root / "runtime-mode.json").stat().st_mode & 0o077 == 0
    selection.save_source("managed")
    assert selection.read_mode() == "headed"


@pytest.mark.parametrize("value", ['{"mode":true}', '{"mode":"auto"}', '{"mode":"headed","flags":[]}', '[]', '{broken'])
def test_invalid_mode_is_reported_without_fallback(runtime_selection, value):
    root, _ = runtime_selection
    root.mkdir()
    (root / "runtime-mode.json").write_text(value)
    with pytest.raises(ValueError, match="Invalid browser mode"):
        selection.read_mode()


def test_failed_mode_write_preserves_previous_choice(runtime_selection, monkeypatch):
    root, _ = runtime_selection
    selection.save_mode("headed")

    def fail(*args):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        selection.save_mode("headless")
    assert selection.read_mode() == "headed"
    assert not list(root.glob(".runtime-mode-*"))


def test_cloud_ignores_local_mode_and_cannot_mutate_it(runtime_selection, monkeypatch):
    selection.save_mode("headed")
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert selection.read_mode() == "headless"
    assert selection.source_view()["mode"] == "headless"
    with pytest.raises(PermissionError):
        selection.save_mode("headed")
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    assert selection.read_mode() == "headed"


@pytest.mark.parametrize("system,expected", [
    ("Darwin", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("Linux", "/opt/google/chrome/chrome"),
    ("Windows", "/programs/Google/Chrome/Application/chrome.exe"),
])
def test_system_candidates_are_stable_chrome_locations(system, expected, monkeypatch):
    monkeypatch.setattr(selection.platform, "system", lambda: system)
    monkeypatch.setenv("PROGRAMFILES", "/programs")
    assert Path(expected) in selection.system_candidates()
    assert all("Testing" not in str(path) for path in selection.system_candidates())


@pytest.mark.asyncio
async def test_system_launch_keeps_embedded_mode_and_uses_an_isolated_profile(runtime_selection, monkeypatch):
    from narranexus.platform.browser._browser_impl import runtime_launch
    from tests.browser.test_session import make_session

    _, system = runtime_selection
    selection.save_source("system")
    launched = []
    session, _, _ = make_session()

    async def launch(**kwargs):
        launched.append(kwargs)
        return session

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    service = BrowserService(probe=lambda path: "Google Chrome 153.0.0.0")
    monkeypatch.setattr(service, "_prepare_session_audit", lambda *args: (lambda row: None, lambda s: None, None))
    first, refusal = await service.open_session("a1", policy=session._policy)
    assert refusal is None and first is session
    assert launched[0]["executable"] == system
    assert launched[0]["headless"] is True
    assert launched[0]["profile"] == "system"
    selection.save_source("managed")
    second, refusal = await service.open_session("a1", policy=session._policy)
    assert refusal is None and second is first
    assert len(launched) == 1
    assert first.is_open
    await session.close()


@pytest.mark.asyncio
async def test_saved_mode_applies_only_to_new_sessions_in_the_same_profile(runtime_selection, monkeypatch):
    from narranexus.platform.browser._browser_impl import runtime_launch
    from tests.browser.test_session import make_session

    root, _ = runtime_selection
    selection.save_source("system")
    launched = []
    sessions = []

    async def launch(**kwargs):
        launched.append(kwargs)
        session, _, _ = make_session()
        sessions.append(session)
        return session

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    service = BrowserService(probe=lambda path: "Google Chrome 153.0.0.0")
    monkeypatch.setattr(service, "_prepare_session_audit", lambda *args: (lambda row: None, lambda s: None, None))
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
    policy = BrowserPolicy()
    try:
        selection.save_mode("headed")
        first, refusal = await service.open_session("a1", policy=policy)
        assert first is sessions[0] and refusal is None
        assert launched[0]["headless"] is False
        selection.save_mode("headless")
        second, refusal = await service.open_session("a1", policy=policy)
        assert second is first and refusal is None and first.is_open
        assert len(launched) == 1
        await first.close()
        third, refusal = await service.open_session("a1", policy=policy)
        assert third is sessions[1] and refusal is None
        assert launched[1]["headless"] is True
        assert launched[0]["profile"] == launched[1]["profile"] == "system"
        (root / "runtime-mode.json").write_text("broken")
        assert (await service.open_session("a1", policy=policy))[0] is third
        await third.close()
        missing, refusal = await service.open_session("a1", policy=policy)
        assert missing is None and refusal["outcome"] == "ERROR"
        assert "mode" in refusal["message"]
        assert len(launched) == 2
    finally:
        for session in sessions:
            await session.close()


@pytest.fixture
def source_client(runtime_selection, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from backend.routes import browser

    monkeypatch.setattr(browser, "_service", BrowserService(probe=lambda path: "Google Chrome 153.0.0.0"))
    app = FastAPI()
    app.include_router(browser.router, prefix="/api/browser")

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id")
        return await call_next(request)

    with TestClient(app) as client:
        yield client


def test_source_api_requires_identity_and_rejects_arbitrary_executables(source_client):
    assert source_client.put("/api/browser/runtime/source", json={"source": "system"}).status_code == 401
    response = source_client.put("/api/browser/runtime/source", headers={"X-User-Id": "u1"},
                                 json={"source": "system", "executable": "/bin/sh"})
    assert response.status_code == 422


def test_source_api_returns_selected_runtime_and_keeps_preferences_across_requests(source_client, runtime_selection):
    _, system = runtime_selection
    response = source_client.put("/api/browser/runtime/source", headers={"X-User-Id": "u1"},
                                 json={"source": "system"})
    assert response.status_code == 200
    assert response.json()["executable"] == str(system)
    assert response.json()["selection"]["source"] == "system"
    assert source_client.get("/api/browser/runtime", headers={"X-User-Id": "u1"}).json()["selection"]["source"] == "system"


def test_source_api_reports_failure_and_forbids_cloud_mutation(source_client, runtime_selection, monkeypatch):
    _, system = runtime_selection
    system.unlink()
    response = source_client.put("/api/browser/runtime/source", headers={"X-User-Id": "u1"},
                                 json={"source": "system"})
    assert response.status_code == 422
    assert selection.read_source() == "managed"
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert source_client.put("/api/browser/runtime/source", headers={"X-User-Id": "u1"},
                             json={"source": "managed"}).status_code == 403


def test_mode_api_requires_identity_and_a_strict_body(source_client):
    assert source_client.put("/api/browser/runtime/mode", json={"mode": "headed"}).status_code == 401
    for body in ({"mode": "auto"}, {"mode": True}, {"mode": "headed", "flags": []}):
        assert source_client.put("/api/browser/runtime/mode", headers={"X-User-Id": "u1"},
                                 json=body).status_code == 422


def test_mode_api_persists_and_returns_the_choice(source_client):
    response = source_client.put("/api/browser/runtime/mode", headers={"X-User-Id": "u1"},
                                 json={"mode": "headed"})
    assert response.status_code == 200
    assert response.json()["selection"]["mode"] == "headed"
    assert source_client.get("/api/browser/runtime", headers={"X-User-Id": "u1"}).json()["selection"]["mode"] == "headed"


def test_mode_api_forbids_cloud_and_reports_write_failure(source_client, monkeypatch):
    from backend.routes import browser

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert source_client.put("/api/browser/runtime/mode", headers={"X-User-Id": "u1"},
                             json={"mode": "headed"}).status_code == 403
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    def fail(mode):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(browser, "save_mode", fail)
    response = source_client.put("/api/browser/runtime/mode", headers={"X-User-Id": "u1"},
                                 json={"mode": "headed"})
    assert response.status_code == 503
    assert selection.read_mode() == "headless"


def test_mode_api_can_repair_invalid_preferences(source_client, runtime_selection):
    root, _ = runtime_selection
    root.mkdir()
    (root / "runtime-mode.json").write_text(json.dumps({"mode": "unknown"}))
    assert source_client.get("/api/browser/runtime", headers={"X-User-Id": "u1"}).status_code == 503
    response = source_client.put("/api/browser/runtime/mode", headers={"X-User-Id": "u1"},
                                 json={"mode": "headed"})
    assert response.status_code == 200
    assert selection.read_mode() == "headed"
