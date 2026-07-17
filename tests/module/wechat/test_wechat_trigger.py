"""Unit tests for WeChatTrigger's permanent-auth-failure classification.

Regression guard for the zombie-reconnect class of incident (CLAUDE.md
"Incident-derived engineering lessons" #1): a dead iLink session must DISABLE
the credential, while a transient blip must keep reconnecting.
"""
import httpx

from xyz_agent_context.module.wechat_module.wechat_sdk_client import WeChatSDKError
from xyz_agent_context.module.wechat_module.wechat_trigger import WeChatTrigger


def test_dead_session_is_permanent_auth_failure():
    trig = WeChatTrigger()
    # getupdates ret!=0 == session expired / bad token -> stop reconnecting and
    # let the base class disable the credential (else: reconnect storm forever).
    assert trig.is_permanent_auth_failure(WeChatSDKError(1001, "updates")) is True


def test_send_failure_is_not_permanent():
    trig = WeChatTrigger()
    # A send-side ret!=0 is per-message (stale context_token), not a dead login,
    # and never reaches the connect loop anyway — must not disable the account.
    assert trig.is_permanent_auth_failure(WeChatSDKError(500, "send")) is False


def test_transient_network_error_is_not_permanent():
    trig = WeChatTrigger()
    # Network blips must keep retrying under the default backoff, never disable.
    assert trig.is_permanent_auth_failure(httpx.ConnectError("boom")) is False


import asyncio  # noqa: E402

import pytest  # noqa: E402

from xyz_agent_context.module.wechat_module._wechat_credential_manager import (  # noqa: E402
    WeChatCredential,
)
import xyz_agent_context.module.wechat_module.wechat_trigger as _wt  # noqa: E402


def _cred() -> WeChatCredential:
    return WeChatCredential(agent_id="agent_x", bot_token="tok", base_url="http://x")


@pytest.mark.asyncio
async def test_hung_getupdates_raises_transient_stall(monkeypatch):
    """A getupdates that never returns within the hard timeout is surfaced as a
    TRANSIENT WeChatSDKError(source="stall") — so the base reconnects (its
    backoff + paired transport audit), NOT disable. connect() does not reconnect
    in place (that would lose backoff + emit an orphan DISCONNECTED)."""
    class _FakeClient:
        def __init__(self, *_a):
            pass

        async def get_updates(self, cursor):
            await asyncio.sleep(30)  # hang far past the hard timeout

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True
    trig.GETUPDATES_HARD_TIMEOUT_SECONDS = 0.05

    with pytest.raises(WeChatSDKError) as ei:
        async for _ in trig.connect(_cred()):
            pass
    assert ei.value.source == "stall"
    assert trig.is_permanent_auth_failure(ei.value) is False  # reconnect, not disable


@pytest.mark.asyncio
async def test_idle_account_does_not_raise_or_reconnect(monkeypatch):
    """A quiet account (empty polls) is NORMAL — no raise, no reconnect, no
    audit noise. The client is created exactly once."""
    created: list[int] = []

    class _FakeClient:
        def __init__(self, *_a):
            created.append(1)

        async def get_updates(self, cursor):
            return {"msgs": [], "sync_buf": "s", "get_updates_buf": "c"}

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True
    trig.POLL_IDLE_SLEEP_SECONDS = 0.0

    async def _drive():
        async for _ in trig.connect(_cred()):
            pass
    t = asyncio.create_task(_drive())
    await asyncio.sleep(0.1)  # many empty polls
    trig.running = False
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    assert len(created) == 1  # never reconnected on idle


@pytest.mark.asyncio
async def test_healthy_updates_flow_single_client(monkeypatch):
    """Real messages flow through; the client is created exactly once (no
    reconnect churn). Uses the real getupdates schema (no `ret`)."""
    created: list[int] = []
    got: list[dict] = []

    class _FakeClient:
        def __init__(self, *_a):
            created.append(1)

        async def get_updates(self, cursor):
            return {"msgs": [{"from_user_id": "u"}], "sync_buf": "s", "get_updates_buf": "c"}

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True

    async def _drive():
        async for msg in trig.connect(_cred()):
            got.append(msg)
            if len(got) >= 3:
                trig.running = False
                break
    await asyncio.wait_for(_drive(), timeout=2.0)
    assert len(created) == 1
    assert len(got) >= 3
