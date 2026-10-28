"""
@file_name: test_login.py
@author:
@date: 2026-09-23
@description: Cross-process login notices and explicit human handoff lifecycle.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.browser.browser_service import BrowserService
from narranexus.platform.utils.db import db_factory
from tests.browser.test_read import FakeCdp, allow, session_with
from tests.browser.test_read import live_snapshot as live_snapshot, snapshot_site as snapshot_site, observe_dom


async def notice(service):
    async with asyncio.timeout(3):
        while True:
            pending = await service.pending_notices("a")
            if pending:
                return pending[0]
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_login_is_visible_across_processes_and_requires_explicit_owner_release(db_client, monkeypatch):
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    host, backend = BrowserService(), BrowserService()
    cdp = FakeCdp(value={"title": "Sign in", "text": "Login form"})
    session = session_with(allow("https://ok.example"), cdp)
    host.register_session("a", session)
    task = asyncio.create_task(host.request_login("a", reason="Verification required", turn_id="t", thread_id="th"))
    try:
        pending = await notice(backend)
        assert pending["kind"] == "login" and pending["session_id"] == "a"
        assert pending["reason"] == "Verification required" and pending["state"] == "pending"
        assert not task.done()
        assert await session.take_control("owner")
        await host.login_control_changed("a", session, "owner", "take")
        assert (await notice(backend))["state"] == "in_control"
        await host.login_control_changed("a", session, "watcher", "release")
        assert not task.done()
        assert await session.release_control("owner")
        await host.login_control_changed("a", session, "owner", "disconnect")
        assert (await notice(backend))["state"] == "pending"
        assert not task.done(), "Disconnect must never claim login is complete"
        assert await session.take_control("reconnected")
        await host.login_control_changed("a", session, "reconnected", "take")
        cdp.value = {"title": "Account", "text": "Signed-in account"}
        assert await session.release_control("reconnected")
        await host.login_control_changed("a", session, "reconnected", "release")
        result = await asyncio.wait_for(task, 3)
        assert result["outcome"] == "OK"
        assert result["handoff"] == "completed"
        assert result["login_state"] == "unverified"
        assert result["data"]["text"] == "Signed-in account"
        assert await backend.pending_notices("a") == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()


@pytest.mark.asyncio
async def test_login_cancellation_and_session_replacement_retire_notice(db_client, monkeypatch):
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    service = BrowserService()
    session = session_with(allow("https://ok.example"), FakeCdp())
    service.register_session("a", session)
    task = asyncio.create_task(service.request_login("a", reason="Sign in", turn_id="t", thread_id="th"))
    await notice(service)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await service.pending_notices("a") == []
    task = asyncio.create_task(service.request_login("a", reason="Sign in", turn_id="t", thread_id="th"))
    await notice(service)
    replacement = session_with(allow("https://ok.example"), FakeCdp())
    service.register_session("a", replacement)
    try:
        assert (await asyncio.wait_for(task, 3))["outcome"] == "ERROR"
        assert await service.pending_notices("a") == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()
        await replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["allow", "ask", "deny"])
async def test_login_requested_during_existing_human_control_needs_only_one_release(db_client, monkeypatch, access):
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy

    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    service = BrowserService()
    cdp = FakeCdp(value={"title": "Account", "text": "Private account"})
    session = session_with(BrowserPolicy.from_dict({"origins": {"https://ok.example": {"access": access}}}), cdp)
    service.register_session("a", session)
    assert await session.take_control("already-human")
    task = asyncio.create_task(service.request_login("a", reason="Finish login", turn_id="t", thread_id="th"))
    try:
        pending = await notice(service)
        async with asyncio.timeout(3):
            while pending["state"] != "in_control":
                await asyncio.sleep(0.01)
                pending = await notice(service)
        assert not task.done() and not cdp.calls, "A notice cannot read through human control or site policy"
        assert await session.release_control("already-human")
        await service.login_control_changed("a", session, "already-human", "release")
        result = await asyncio.wait_for(task, 3)
        assert result["handoff"] == "completed" and result["login_state"] == "unverified"
        assert result["outcome"] == "OK"
        assert result["data"]["title"] == "Account"
        assert await service.pending_notices("a") == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()


@pytest.mark.asyncio
async def test_failed_handoff_receipt_returns_error_and_retires_notification(db_client, monkeypatch):
    from narranexus.platform.repository.browser_login_repository import BrowserLoginRepository

    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    service = BrowserService()
    session = session_with(allow("https://ok.example"), FakeCdp())
    service.register_session("a", session)
    task = asyncio.create_task(service.request_login("a", reason="Sign in", turn_id="t", thread_id="th"))
    try:
        await notice(service)
        monkeypatch.setattr(BrowserLoginRepository, "control_changed", AsyncMock(side_effect=RuntimeError("write failed")))
        pending = AsyncMock(side_effect=RuntimeError("reads also unavailable"))
        monkeypatch.setattr(BrowserLoginRepository, "pending", pending)
        assert await session.take_control("owner")
        with pytest.raises(RuntimeError, match="write failed"):
            await service.login_control_changed("a", session, "owner", "take")
        result = await asyncio.wait_for(task, 3)
        assert result["outcome"] == "ERROR" and "retry" in result["message"]
        assert "handoff" not in result
        pending.assert_not_awaited()
        assert await db_client.get("instance_browser_login_requests", {"agent_id": "a"}) == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()


@pytest.mark.asyncio
async def test_login_verification_reads_new_origin_without_site_approval(db_client, monkeypatch):
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    service = BrowserService()
    cdp = FakeCdp()
    session = session_with(allow("https://ok.example"), cdp)
    service.register_session("a", session)
    task = asyncio.create_task(service.request_login("a", reason="Sign in", turn_id="t", thread_id="th"))
    try:
        await notice(service)
        assert await session.take_control("owner")
        await service.login_control_changed("a", session, "owner", "take")
        cdp.url = "https://new.example/account"
        assert await session.release_control("owner")
        await service.login_control_changed("a", session, "owner", "release")
        result = await asyncio.wait_for(task, 3)
        assert result["outcome"] == "OK"
        assert result["handoff"] == "completed"
        assert "data" in result
        assert "human_action" not in result
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_human", [False, True])
async def test_live_login_notice_human_input_and_fresh_read(live_snapshot, db_client, monkeypatch, already_human):
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    service, backend = BrowserService(), BrowserService()
    session = live_snapshot
    snapshot = (await session.read_page())["data"]
    target = next(field["selector"] for field in snapshot["fields"] if field["label"] == "Query")
    assert (await session.act("fill", selector=target, text=""))["outcome"] == "OK"
    service.register_session("a", session)
    if already_human:
        assert await session.take_control("human")
    task = asyncio.create_task(service.request_login("a", reason="Complete verification", turn_id="t", thread_id="th"))
    try:
        assert (await notice(backend))["kind"] == "login"
        if not already_human:
            assert await session.take_control("human")
            await service.login_control_changed("a", session, "human", "take")
        position = await observe_dom(session, "(() => {const r = document.querySelector('[name=query]').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
        for kind, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
            await session.handle_user_input({"kind": "mouse", "type": kind, **position, "buttons": buttons}, "human")
        await session.handle_user_input({"kind": "text", "text": "Human verified"}, "human")
        for kind in ("keyDown", "keyUp"):
            await session.handle_user_input({"kind": "key", "type": kind, "key": "Enter", "code": "Enter"}, "human")
        assert await session.release_control("human")
        await service.login_control_changed("a", session, "human", "release")
        result = await asyncio.wait_for(task, 3)
        assert result["handoff"] == "completed"
        assert "Submitted: Human verified" in result["data"]["text"]
        assert await backend.pending_notices("a") == []
        assert (await session.run_script("document.title"))["outcome"] == "REJECTED"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
