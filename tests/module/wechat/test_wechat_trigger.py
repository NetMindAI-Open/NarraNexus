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
