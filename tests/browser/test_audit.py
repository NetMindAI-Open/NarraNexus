"""
@file_name: test_audit.py
@date: 2026-09-23
@description: Durable browser audit, private payload exclusion, and CDP responsiveness.
"""
import asyncio
import json
import uuid

import pytest
import pytest_asyncio

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession
from narranexus.platform.browser._browser_impl import runtime_launch
from narranexus.platform.browser import browser_service
from narranexus.platform.repository.service_audit_repository import ServiceAuditRepository
from narranexus.platform.services.service_audit import ServiceAuditor
from tests.browser.test_browser_service import make_service


class Page:
    is_open = True

    def __init__(self):
        self.probes = 0
        self.probe_blocked = False

    async def call(self, method, params=None, **kwargs):
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if expression == "1":
                self.probes += 1
                if self.probe_blocked:
                    await asyncio.Future()
                return {"result": {"value": 1}}
            if expression == "location.href":
                return {"result": {"value": "https://site.example/private?password=secret"}}
            return {"result": {"value": {"filled": True}}}
        return {}

    async def close(self):
        self.is_open = False


@pytest_asyncio.fixture
async def observed(monkeypatch, db_client):
    repo = ServiceAuditRepository(db_client)

    async def get_repo(_self):
        return repo

    monkeypatch.setattr(ServiceAuditor, "_get_repo", get_repo)
    monkeypatch.setattr(browser_service, "BROWSER_PROBE_INTERVAL", 0.02, raising=False)
    monkeypatch.setattr(browser_service, "BROWSER_PROBE_TIMEOUT", 0.02, raising=False)
    page = Page()

    async def launch(**kwargs):
        return BrowserSession(cdp=page, policy=kwargs["policy"], audit=kwargs["audit"],
                              turn_id=kwargs["turn_id"], thread_id=kwargs["thread_id"])

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    service = make_service()
    session, refusal = await service.open_session("audit_agent", turn_id="turn1", thread_id="thread1",
        policy=BrowserPolicy(origins={"https://site.example": OriginPolicy()}))
    assert refusal is None
    try:
        yield service, session, page, repo
    finally:
        await service.close()


async def eventually(repo, event_type, predicate=lambda _: True):
    async with asyncio.timeout(2):
        while True:
            rows = await repo.recent(service="browser", event_type=event_type)
            matches = [json.loads(row["detail"]) for row in rows]
            if any(predicate(detail) for detail in matches):
                return matches
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_default_audit_persists_lifecycle_control_actions_and_sanitized_errors(observed):
    service, session, _, repo = observed
    await session.navigate("https://site.example/private?password=secret#token")
    await session.act("fill", selector="#password", text="form-secret")
    await session.take_control("panel")
    await session.release_control("panel")
    await session.act("bad-action-containing-secret")
    session._record("error", operation="act", error="full page text and password form-secret")
    await service.close_session("audit_agent")
    rows = await repo.recent(service="browser", limit=100)
    kinds = {row["event_type"] for row in rows}
    assert {"started", "stopped", "navigate", "action", "control", "error"} <= kinds
    serialized = "\n".join(row["detail"] for row in rows)
    for private in ("password", "form-secret", "full page text", "private", "bad-action-containing-secret", "#token"):
        assert private not in serialized
    detail = [json.loads(row["detail"]) for row in rows]
    assert all(item["agent_id"] == "audit_agent" and item["session_id"] for item in detail)
    assert any(item.get("origin") == "https://site.example" for item in detail)
    assert any(item.get("turn_id") == "turn1" and item.get("thread_id") == "thread1" for item in detail)


@pytest.mark.asyncio
async def test_probe_records_responsiveness_and_recovers_without_closing_session(observed):
    _, session, page, repo = observed
    await eventually(repo, "heartbeat", lambda d: d.get("responsive") is True)
    page.probe_blocked = True
    await eventually(repo, "error", lambda d: d.get("operation") == "probe")
    assert session.is_open
    page.probe_blocked = False
    previous = page.probes
    await eventually(repo, "heartbeat", lambda d: d.get("probe_count", 0) > previous)
    assert session.is_open


@pytest.mark.asyncio
async def test_close_waits_for_pending_audit_writes(observed, monkeypatch):
    _, session, _, repo = observed
    await eventually(repo, "started")
    entered, release = asyncio.Event(), asyncio.Event()
    original = repo.record

    async def slow(service, event_type, detail=None):
        if event_type == "action":
            entered.set()
            await release.wait()
        return await original(service, event_type, detail)

    monkeypatch.setattr(repo, "record", slow)
    await session.act("fill", selector="#q", text="secret")
    await asyncio.wait_for(entered.wait(), 1)
    close = asyncio.create_task(session.close())
    try:
        await asyncio.sleep(0)
        assert not close.done()
    finally:
        release.set()
        await close
    assert await repo.recent(service="browser", event_type="action")
    assert await repo.recent(service="browser", event_type="stopped")


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_audit_writes_are_observed_without_failing_browser(observed, monkeypatch, raises):
    service, session, _, repo = observed
    await eventually(repo, "started")
    original = repo.record
    failures = []

    async def fail(service, event_type, detail=None):
        if event_type == "action":
            failures.append(detail)
            if raises:
                raise OSError("audit unavailable")
            return False
        return await original(service, event_type, detail)

    monkeypatch.setattr(repo, "record", fail)
    assert (await session.act("fill", selector="#q", text="private"))["ok"]
    await service.close_session("audit_agent")
    assert failures
    stopped = (await repo.recent(service="browser", event_type="stopped"))[0]
    assert json.loads(stopped["detail"])["audit_write_failures"] >= 1


@pytest.mark.asyncio
async def test_transport_initiated_close_also_drains_audit(observed):
    _, session, _, repo = observed
    # runtime_launch's transport observer retains the original close closure.
    await BrowserSession.close(session)
    await eventually(repo, "stopped")
    assert not session.is_open


@pytest.mark.asyncio
async def test_launch_failure_is_audited_without_raw_stderr(monkeypatch, db_client):
    async def get_repo(_self):
        return ServiceAuditRepository(db_client)

    async def fail(**kwargs):
        raise runtime_launch.LaunchError("SingletonLock with secret page/password value")

    monkeypatch.setattr(ServiceAuditor, "_get_repo", get_repo)
    monkeypatch.setattr(runtime_launch, "launch_session", fail)
    service = make_service()
    session, refusal = await service.open_session("failed", policy=BrowserPolicy())
    assert session is None and refusal["outcome"] == "ERROR"
    await service.close()
    rows = await db_client.get("service_audit", {"service": "browser"})
    assert any(row["event_type"] == "error" for row in rows)
    assert "password" not in "\n".join(row["detail"] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_close", [False, True])
async def test_default_sink_through_runtime_close_writes_to_factory_database(monkeypatch, tmp_path, transport_close):
    from narranexus.platform.browser._browser_impl import locate
    from narranexus.platform.utils import get_db_client
    from tests.browser.test_runtime_launch import FakeSocket, harness

    class ProbeSocket(FakeSocket):
        async def send(self, raw):
            message = json.loads(raw)
            if message.get("method") == "Runtime.evaluate":
                self.sent.append(raw)
                await self._q.put(json.dumps({"id": message["id"], "result": {"result": {"value": 1}}}))
            else:
                await super().send(raw)

    options, recorder = harness()
    recorder["sock"] = ProbeSocket()
    original_launch = runtime_launch.launch_session

    async def launch(**kwargs):
        return await original_launch(**kwargs, **options)

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    monkeypatch.setattr(locate, "install_root", lambda: tmp_path)
    agent_id = f"audit_integration_{uuid.uuid4().hex}"
    service = make_service()
    db = await get_db_client()  # The autouse fixture isolates the real factory database.
    repo = ServiceAuditRepository(db)
    session, refusal = await service.open_session(agent_id, policy=BrowserPolicy(origins={
        "https://site.example": OriginPolicy(),
    }), turn_id="turn", thread_id="thread")
    assert refusal is None
    try:
        await eventually(repo, "heartbeat", lambda detail: detail.get("agent_id") == agent_id)
        assert (await session.navigate("https://site.example/private?token=secret"))["outcome"] == "OK"
        if transport_close:
            await session.pages.browser.close()
            await eventually(repo, "stopped", lambda detail: detail.get("agent_id") == agent_id)
        else:
            await service.close_session(agent_id)
    finally:
        await service.close()
    rows = await db.get("service_audit", {"service": "browser"})
    records = [(row["event_type"], json.loads(row["detail"])) for row in rows]
    records = [(event, detail) for event, detail in records if detail.get("agent_id") == agent_id]
    assert {"started", "heartbeat", "navigate", "stopped"} <= {event for event, _ in records}
    assert len([event for event, _ in records if event == "stopped"]) == 1
    assert recorder["proc"].returncode is not None
    assert not service._audit_tasks
    assert "secret" not in json.dumps(records)
