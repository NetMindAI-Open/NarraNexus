"""
@file_name: test_interpret_test_response.py
@author:
@date: 2026-07-16
@description: ProviderRegistry._interpret_test_response must NOT treat a
self-serviceable failure (balance/model/context) at 400/404/422 as "reachable".
Otherwise the readiness live-test resumes a balance-dead job into the same wall
(PR #116 review finding). Onboarding is unaffected — it only hard-rejects on
auth phrases.
"""
from types import SimpleNamespace

from xyz_agent_context.agent_framework.provider_registry import ProviderRegistry


def _resp(status, text=""):
    return SimpleNamespace(status_code=status, text=text)


def test_balance_400_is_not_ready():
    ok, msg = ProviderRegistry._interpret_test_response(
        _resp(400, "{'error': 'balance not enough'}")
    )
    assert ok is False
    assert "balance" in msg.lower()


def test_insufficient_balance_402_still_handled_elsewhere():
    # 402 falls to the generic else branch → not ready (unchanged).
    ok, _ = ProviderRegistry._interpret_test_response(_resp(402, "Insufficient Balance"))
    assert ok is False


def test_model_not_found_404_is_not_ready():
    ok, msg = ProviderRegistry._interpret_test_response(
        _resp(404, "The model `x` does not exist")
    )
    assert ok is False


def test_benign_400_still_reachable():
    # A 400 that is NOT a self-serviceable failure = auth passed, payload issue.
    ok, msg = ProviderRegistry._interpret_test_response(
        _resp(400, "invalid request: unknown parameter 'foo'")
    )
    assert ok is True


def test_200_ok_and_401_rejected_unchanged():
    assert ProviderRegistry._interpret_test_response(_resp(200))[0] is True
    assert ProviderRegistry._interpret_test_response(_resp(401))[0] is False
