"""
@file_name: test_telegram_poll_failures.py
@date: 2026-09-09
@description: getUpdates failure classification for the Telegram long-poll.

Dev logs showed ``getUpdates failed`` 115 times across three agents with
no way to tell a revoked token (HTTP 401 ``Unauthorized``) from a second
poller on the same bot (HTTP 409 ``Conflict: terminated by other
getUpdates request``): ``TelegramSDKError`` carried only the description
string, non-JSON and transport failures were flattened to
``client_error:<ExceptionName>``, and a 409 was retried (with
``deleteWebhook``) every second forever.

Contract pinned here, with a fake HTTP layer (no network):
  - ``TelegramSDKError`` carries ``status`` + ``description``; ``str()``
    names both;
  - 401 -> permanent: connect raises, ``is_permanent_auth_failure`` says
    so, the base loop disables the credential ONCE with a readable
    ``disabled_reason``;
  - 409 -> one ``deleteWebhook`` retry, then transient (rides the base
    5s -> 120s backoff); NEVER disables the credential — the usual cause
    is our own previous long-poll that Telegram has not released yet;
  - 5xx / transport errors -> transient, the base backoff keeps retrying.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp
import pytest

from narranexus.platform.channel.credential_store import GenericCredentialStore
from narranexus_plugins.telegram_module import telegram_sdk_client as sdk_mod
from narranexus_plugins.telegram_module import telegram_trigger as trigger_mod
from narranexus_plugins.telegram_module._telegram_credential_manager import (
    TelegramCredential,
    TelegramCredentialManager,
)
from narranexus_plugins.telegram_module.telegram_sdk_client import (
    TelegramSDKClient,
    TelegramSDKError,
)
from narranexus_plugins.telegram_module.telegram_trigger import TelegramTrigger


# ── fake HTTP layer for the SDK client ───────────────────────────────────


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def json(self, **_kw) -> dict:
        if self._payload is None:
            raise aiohttp.ContentTypeError(None, (), message="not json")
        return self._payload

    async def text(self) -> str:
        return self._text


class _Session:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict | None = None):
        self.calls.append((url, json or {}))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _sdk_with(monkeypatch, responses: list) -> tuple[TelegramSDKClient, _Session]:
    session = _Session(responses)
    monkeypatch.setattr(sdk_mod.aiohttp, "ClientSession", lambda *a, **k: session)
    return TelegramSDKClient("1234:tok"), session


UNAUTHORIZED = _Resp(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
CONFLICT = _Resp(
    409,
    {
        "ok": False,
        "error_code": 409,
        "description": "Conflict: terminated by other getUpdates request; "
        "make sure that only one bot instance is running",
    },
)


@pytest.mark.asyncio
async def test_sdk_error_carries_status_and_description(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [UNAUTHORIZED])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status == 401
    assert err.description == "Unauthorized"
    assert err.code == "Unauthorized"  # existing callers branch on .code
    assert "401" in str(err) and "Unauthorized" in str(err)


@pytest.mark.asyncio
async def test_sdk_error_keeps_non_json_body_and_status_with_a_stable_code(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [_Resp(502, None, "<html>Bad Gateway</html>")])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status == 502
    assert err.code == "http_502"  # short code: bind/test panels render .code
    assert "Bad Gateway" in str(err)  # the detail lives in description / str()


@pytest.mark.asyncio
async def test_sdk_error_names_the_transport_exception_with_a_stable_code(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [aiohttp.ClientConnectionError("Cannot connect to host api.telegram.org")])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status is None
    assert err.code == "client_error:ClientConnectionError"
    assert "api.telegram.org" in str(err)


@pytest.mark.asyncio
async def test_api_call_envelope_carries_error_code_and_detail(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [CONFLICT, _Resp(502, None, "<html>Bad Gateway</html>")])
    out = await client.api_call("getUpdates", {})
    assert out["ok"] is False
    assert out["error_code"] == 409
    assert out["error"].startswith("Conflict")
    out = await client.api_call("getUpdates", {})
    assert (out["error"], out["error_code"]) == ("http_502", 502)
    assert "Bad Gateway" in out["error_detail"]


# ── trigger classification + poll behaviour ─────────────────────────────


class _FakeClient:
    """Stands in for TelegramSDKClient inside the trigger's connect loop."""

    instances: list["_FakeClient"] = []

    def __init__(self, token: str, script: list):
        self.token = token
        # Shared across the instances one test creates: a reconnect must
        # continue the scripted sequence, not replay it from the start
        # (replaying a transient error forever would hang the loop test).
        self.script = script
        self.delete_webhook_calls = 0
        _FakeClient.instances.append(self)

    async def get_updates(self, **_kw) -> list[dict]:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):  # a real SDK call, so the traceback is production-shaped
            return await item()
        return item

    async def delete_webhook(self) -> bool:
        self.delete_webhook_calls += 1
        return True

    async def close(self) -> None:
        return None


def _script(monkeypatch, script: list) -> None:
    _FakeClient.instances.clear()
    shared = list(script)
    monkeypatch.setattr(trigger_mod, "TelegramSDKClient", lambda token: _FakeClient(token, shared))


def _err(status: int, description: str) -> TelegramSDKError:
    return TelegramSDKError.from_envelope(
        {"ok": False, "error": description, "error_code": status}, "getUpdates failed"
    )


def _cred() -> TelegramCredential:
    return TelegramCredential(agent_id="agent_a", bot_token="1234:tok", bot_user_id="1001", bot_username="acme_bot")


async def _drain(trigger: TelegramTrigger, cred: TelegramCredential) -> list[dict]:
    got = []
    async for raw in trigger.connect(cred):
        got.append(raw)
        trigger.running = False
    return got


@pytest.mark.asyncio
async def test_401_is_permanent_and_raises_out_of_connect(monkeypatch):
    _script(monkeypatch, [_err(401, "Unauthorized")])
    trigger = TelegramTrigger()
    trigger.running = True

    with pytest.raises(TelegramSDKError) as exc_info:
        await _drain(trigger, _cred())
    assert trigger.is_permanent_auth_failure(exc_info.value) is True
    assert "Unauthorized" in str(exc_info.value)


@pytest.mark.asyncio
async def test_409_gets_one_delete_webhook_retry_then_raises_as_transient(monkeypatch):
    # A second 409 right after the retry is usually OUR previous long-poll
    # still attached (up to POLL_TIMEOUT_SECONDS after a restart), so it
    # must NOT disable the credential: it raises and rides the base backoff.
    conflict = "Conflict: terminated by other getUpdates request; make sure that only one bot instance is running"
    _script(monkeypatch, [_err(409, conflict), _err(409, conflict), [{"update_id": 1}]])
    trigger = TelegramTrigger()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    with pytest.raises(TelegramSDKError) as exc_info:
        await _drain(trigger, _cred())
    assert exc_info.value.status == 409
    assert trigger.is_permanent_auth_failure(exc_info.value) is False
    assert _FakeClient.instances[0].delete_webhook_calls == 1  # no per-second storm
    assert "other getUpdates request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_409_retry_budget_resets_after_a_successful_poll(monkeypatch):
    _script(
        monkeypatch,
        [_err(409, "Conflict: x"), [{"update_id": 1}], _err(409, "Conflict: y"), [{"update_id": 2}]],
    )
    trigger = TelegramTrigger()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    got = []
    async for raw in trigger.connect(_cred()):
        got.append(raw)
        if len(got) == 2:
            trigger.running = False
    assert got == [{"update_id": 1}, {"update_id": 2}]
    assert _FakeClient.instances[0].delete_webhook_calls == 2


@pytest.mark.asyncio
async def test_subscribe_loop_keeps_reconnecting_on_409(db_client, monkeypatch):
    # The base loop must back off and try again, never disable the row.
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001"}, enabled=True)
    conflict = _err(409, "Conflict: terminated by other getUpdates request")
    _script(monkeypatch, [conflict, conflict, conflict, conflict])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger.running = True
    sleeps: list[float] = []

    async def _sleep(seconds, *_a, **_k):
        # asyncio is one module: this stub sees the trigger's 1s retry sleep
        # AND the base loop's backoff sleeps; only the backoffs (>= 5s) count.
        if seconds >= 5:
            sleeps.append(seconds)
            if len(sleeps) >= 2:  # two backoffs observed — stop the loop
                trigger.running = False

    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _sleep)

    await trigger._subscribe_loop(_cred())

    cred = await TelegramCredentialManager(db_client).get("agent_a")
    assert cred.enabled is True and cred.disabled_reason == ""
    assert len(_FakeClient.instances) == 2  # reconnected after the first backoff
    assert sleeps and sleeps[0] >= 5


@pytest.mark.asyncio
async def test_409_from_a_stale_webhook_recovers_after_delete_webhook(monkeypatch):
    _script(monkeypatch, [_err(409, "Conflict: terminated by setWebhook request"), [{"update_id": 5}]])
    trigger = TelegramTrigger()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    got = await _drain(trigger, _cred())
    assert got == [{"update_id": 5}]
    assert _FakeClient.instances[0].delete_webhook_calls == 1


@pytest.mark.parametrize(
    "exc",
    [
        _err(500, "Internal Server Error"),
        _err(502, "http_502: <html>Bad Gateway</html>"),
        TelegramSDKError("client_error:TimeoutError", "getUpdates failed (client_error:TimeoutError)"),
        asyncio.TimeoutError(),
    ],
)
@pytest.mark.asyncio
async def test_transient_errors_are_not_permanent(monkeypatch, exc):
    _script(monkeypatch, [exc])
    trigger = TelegramTrigger()
    trigger.running = True

    with pytest.raises(type(exc)):
        await _drain(trigger, _cred())
    assert trigger.is_permanent_auth_failure(exc) is False


async def _no_sleep(*_a, **_k) -> None:
    return None


# ── the base loop disables the credential once, with a readable reason ──


@pytest.mark.asyncio
async def test_subscribe_loop_disables_credential_with_reason_on_401(db_client, monkeypatch):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001", "bot_username": "acme_bot"}, enabled=True)
    _script(monkeypatch, [_err(401, "Unauthorized")])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger.running = True

    await trigger._subscribe_loop(_cred())  # returns instead of backing off forever

    mgr = TelegramCredentialManager(db_client)
    cred = await mgr.get("agent_a")
    assert cred.enabled is False
    assert "401" in cred.disabled_reason and "Unauthorized" in cred.disabled_reason
    assert len(cred.disabled_reason) <= 200
    assert "disabled_reason" in cred.to_public_dict()
    assert await mgr.list_active() == []
    assert len(_FakeClient.instances) == 1  # no reconnect attempt after the permanent failure


@pytest.mark.asyncio
async def test_re_enabling_clears_the_disabled_reason(db_client):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001"}, enabled=True)
    mgr = TelegramCredentialManager(db_client)

    assert await mgr.set_enabled("agent_a", False, reason="TelegramSDKError: getUpdates failed (HTTP 409)") is True
    assert (await mgr.get("agent_a")).disabled_reason.startswith("TelegramSDKError")

    # The generic set-active route re-enables through the store directly.
    assert await store.set_enabled("telegram", "agent_a", True) is True
    cred = await mgr.get("agent_a")
    assert cred.enabled is True
    assert cred.disabled_reason == ""
    assert await store.set_enabled("telegram", "nobody", False, reason="x") is False


# ── reason hygiene + store contract ─────────────────────────────────────


@pytest.mark.asyncio
async def test_set_enabled_reports_a_lost_version_race_as_false(db_client, monkeypatch):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001"}, enabled=True)

    async def _losing_patch(*_a, **_k):
        raise RuntimeError("telegram/agent_a: credential patch kept losing the version race")

    monkeypatch.setattr(GenericCredentialStore, "patch", _losing_patch)
    assert await store.set_enabled("telegram", "agent_a", False, reason="x") is False
    assert await TelegramCredentialManager(db_client).set_enabled("agent_a", False, reason="x") is False


@pytest.mark.parametrize(
    "channel, manager_path",
    [
        ("slack", "narranexus_plugins.slack_module._slack_credential_manager:SlackCredentialManager"),
        ("discord", "narranexus_plugins.discord_module._discord_credential_manager:DiscordCredentialManager"),
        ("wechat", "narranexus_plugins.wechat_module._wechat_credential_manager:WeChatCredentialManager"),
        (
            "narramessenger",
            "narranexus_plugins.narramessenger_module._narramessenger_credential_manager:NarramessengerCredentialManager",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_auto_disabling_channel_persists_the_reason(db_client, channel, manager_path):
    import importlib

    mod_name, cls_name = manager_path.split(":")
    manager_cls = getattr(importlib.import_module(mod_name), cls_name)
    store = GenericCredentialStore(db_client)
    await store.upsert(channel, "agent_a", {}, enabled=True)
    mgr = manager_cls(db_client)

    assert await mgr.set_enabled("agent_a", False, reason="SomeSDKError: token revoked") is True
    cred = await mgr.get("agent_a")
    assert cred.enabled is False
    assert cred.disabled_reason == "SomeSDKError: token revoked"
    assert cred.to_public_dict()["disabled_reason"] == "SomeSDKError: token revoked"

    assert await mgr.set_enabled("agent_a", True) is True
    assert (await mgr.get("agent_a")).disabled_reason == ""


class _AuditRecorder:
    def __init__(self):
        self.rows: list[tuple[str, dict]] = []

    async def append(self, event_type: str, **kwargs) -> None:
        self.rows.append((event_type, kwargs))


@pytest.mark.asyncio
async def test_audit_and_disable_reason_never_carry_the_request_url_or_token(db_client, monkeypatch):
    # Review I1: the transport exception text (which can quote Telegram's
    # request URL — the bot token is in its path) reaches the log line, the
    # audit row's details.error and disabled_reason through ONE sanitiser.
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "7981632450:AAHsecretsecretsecretsecret", "bot_user_id": "1001"}, enabled=True)
    poisoned = "https://api.telegram.org/bot7981632450:AAHsecretsecretsecretsecret/getUpdates"
    transient = TelegramSDKError(
        "client_error:InvalidURL", "getUpdates failed", description=f"client_error:InvalidURL: {poisoned}"
    )
    permanent = TelegramSDKError(
        "Unauthorized", "getUpdates failed", status=401, description=f"Unauthorized (url {poisoned})"
    )
    _script(monkeypatch, [transient, permanent])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger._audit_repo = _AuditRecorder()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    await trigger._subscribe_loop(_cred())

    errors = [row[1]["details"]["error"] for row in trigger._audit_repo.rows if "error" in row[1].get("details", {})]
    assert len(errors) == 2  # one transient disconnect, one permanent
    for text in errors + [(await TelegramCredentialManager(db_client).get("agent_a")).disabled_reason]:
        assert "api.telegram.org" not in text and "7981632450:AAH" not in text
        assert "<url>" in text
    assert errors[1].startswith("TelegramSDKError: getUpdates failed")


@pytest.mark.asyncio
async def test_transient_log_output_including_traceback_never_carries_the_bot_token(db_client, monkeypatch):
    # Round-3 I1: safe_error_text cleans the formatted line, but
    # logger.exception also renders the traceback, whose last line is the
    # raw str(exc). The token must therefore be stripped at the SOURCE —
    # TelegramSDKClient._redact — so the exception the base loop logs never
    # contained it. Goes through the real SDK client, then captures loguru.
    from loguru import logger as loguru_logger

    token = "7981632450:AAHsecretsecretsecretsecret"
    monkeypatch.setattr(
        sdk_mod.aiohttp,
        "ClientSession",
        lambda *a, **k: _Session([aiohttp.InvalidURL(f"https://api.telegram.org/bot{token}/getUpdates")]),
    )
    real_client = TelegramSDKClient(token)
    with pytest.raises(TelegramSDKError) as exc_info:
        await real_client.get_updates()
    real_exc = exc_info.value
    assert token not in str(real_exc) and "<token>" in str(real_exc)
    assert real_exc.__cause__ is None and real_exc.__context__ is None  # no aiohttp frame in the chain

    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": token, "bot_user_id": "1001"}, enabled=True)
    monkeypatch.setattr(
        sdk_mod.aiohttp,
        "ClientSession",
        lambda *a, **k: _Session([aiohttp.InvalidURL(f"https://api.telegram.org/bot{token}/getUpdates")]),
    )
    real_client = TelegramSDKClient(token)  # raised INSIDE the loop: production-shaped traceback
    _script(monkeypatch, [real_client.get_updates])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger._audit_repo = _AuditRecorder()
    trigger.running = True

    async def _stop_after_backoff(seconds, *_a, **_k):
        if seconds >= 5:
            trigger.running = False

    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _stop_after_backoff)
    captured: list[str] = []
    sink_id = loguru_logger.add(captured.append, level="DEBUG", backtrace=True, diagnose=True)
    try:
        await trigger._subscribe_loop(_cred())
    finally:
        loguru_logger.remove(sink_id)

    full = "".join(captured)
    assert "transport error" in full and "Traceback" in full  # the exception branch ran, with traceback
    assert token not in full
    assert not re.search(r"\b\d{6,}:[A-Za-z0-9_-]{20,}", full)
    assert f"api.telegram.org/bot{token}" not in full


@pytest.mark.asyncio
async def test_download_file_network_error_is_redacted_too(monkeypatch):
    token = "7981632450:AAHsecretsecretsecretsecret"
    client, session = _sdk_with(monkeypatch, [_Resp(200, {"ok": True, "result": {"file_path": "photos/1.jpg"}})])

    class _Get:
        async def __aenter__(self):
            raise aiohttp.InvalidURL(f"https://api.telegram.org/file/bot{token}/photos/1.jpg")

        async def __aexit__(self, *_exc):
            return None

    session.get = lambda url: _Get()
    client._bot_token = token
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.download_file("f1")
    assert token not in str(exc_info.value) and "<token>" in str(exc_info.value)


@pytest.mark.asyncio
async def test_non_json_error_page_echoing_the_url_is_redacted_before_truncation(monkeypatch):
    # PR #388 review I1: a proxy's 407/502 HTML page echoes the request URL
    # (token in the path); the snippet is what tg_cli hands the agent. The
    # token must be redacted BEFORE the 160-char cut, or its head survives.
    token = "7981632450:AAHsecretsecretsecretsecret"
    padding = "x" * 100  # in the RAW page the token straddles the 160-char boundary
    page = f"<html>{padding}https://api.telegram.org/bot{token}/getUpdates blocked by proxy</html>"
    client, _ = _sdk_with(monkeypatch, [_Resp(502, None, page), _Resp(407, None, page)])
    client._bot_token = token

    out = await client.api_call("getUpdates", {})
    assert out["error"] == "http_502" and out["error_code"] == 502
    assert token not in out["error_detail"] and "7981632450:AAH" not in out["error_detail"]
    assert "<token>" in out["error_detail"] and len(out["error_detail"]) <= 160

    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    assert token not in str(exc_info.value) and "7981632450:AAH" not in str(exc_info.value)


def test_failure_envelope_redacts_before_it_truncates():
    # PR #388 round 2 M3: the redact-then-cut order is a property of
    # _failure itself, not of the caller.
    client = TelegramSDKClient("7981632450:AAHsecretsecretsecretsecret")
    body = "x" * 30 + "https://api.telegram.org/bot7981632450:AAHsecretsecretsecretsecret/getUpdates\nnext line"
    out = client._failure("getUpdates", max_len=70, error="http_502", error_code=502, error_detail=body)
    assert out["error_code"] == 502  # non-strings untouched
    # The cut at 70 lands INSIDE the token (it starts at index 58): cutting
    # first would leave its head "7981632450:A" behind. Assert on the output.
    assert "7981632450" in body[:70]  # premise: the raw cut does split the token
    assert "7981632450" not in out["error_detail"]
    assert "<token>" in out["error_detail"]
    assert len(out["error_detail"]) <= 70 and "\n" not in out["error_detail"]


@pytest.mark.asyncio
async def test_audit_row_keeps_more_of_the_error_than_the_credential_row(db_client, monkeypatch):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001"}, enabled=True)
    long_desc = "Unauthorized " + "detail " * 60  # > 200 chars
    _script(monkeypatch, [TelegramSDKError("Unauthorized", "getUpdates failed", status=401, description=long_desc)])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger._audit_repo = _AuditRecorder()
    trigger.running = True

    await trigger._subscribe_loop(_cred())

    audit_error = trigger._audit_repo.rows[-1][1]["details"]["error"]
    reason = (await TelegramCredentialManager(db_client).get("agent_a")).disabled_reason
    assert 200 < len(audit_error) <= 500
    assert len(reason) == 200 and audit_error.startswith(reason)
