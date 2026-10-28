"""
@file_name: test_cli_error_self_serviceable.py
@date: 2026-09-09
@description: ``self_serviceable`` on ``response.error`` — can the USER clear
this error on their own (wait out / upgrade a rate limit, re-login, top up),
or is it something only the platform / provider can fix?

PR #379 made the subscription 429 text reach the user verbatim, but the
payload still gave the frontend / inbox no way to tell "wait or upgrade" apart
from "the platform broke". The classification is keyed on the CLI error enum
(``narranexus.contracts.agent_events.cli_error_self_serviceable``): rate_limit
/ authentication_failed / billing_error → True; server_error / invalid_request
/ unknown and the adapter's own no_output → False. It rides every
``response.error`` the claude adapter emits and ``ResponseProcessor`` carries
it onto ``ErrorMessage`` for the wire.

Each test goes red when its half is removed: the classifier, the adapter's
event builders, output_transfer's inline-error path, or the processor's
forwarding.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.agent_events import (
    _SELF_SERVICEABLE_CLI_ERROR_TYPES,
    CLI_ERROR_TYPES,
    cli_error_self_serviceable,
)
from narranexus.platform.agent_framework.loop.output_transfer import output_transfer
from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _raw_exception_error,
)
from narranexus.platform.agent_runtime.execution_state import ExecutionState
from narranexus.platform.agent_runtime.response_processor import ResponseProcessor
from narranexus.platform.schema import ErrorMessage
from narranexus.platform.schema.runtime_message import (
    AUTH_EXPIRED_ERROR_TYPE,
    EXECUTOR_INFRA_ERROR_TYPE,
    SELF_SERVICEABLE_ERROR_TYPE,
)
from narranexus_plugins.frameworks_claude_code.sdk import (
    _inline_assistant_error_event,
    _zero_output_error_event,
)

from tests.agent_framework.test_claude_transient_retry import AssistantMessage, TextBlock


# ── classifier ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error_type, expected",
    [
        ("rate_limit", True),
        ("authentication_failed", True),
        ("billing_error", True),
        ("server_error", False),
        ("invalid_request", False),
        ("unknown", None),
        ("no_output", False),
        ("", None),
        (None, None),
    ],
)
def test_cli_error_self_serviceable(error_type, expected):
    assert cli_error_self_serviceable(error_type) is expected


# The expected classification is written out here, independently of the
# module's own set: a test that iterates the set under test is a tautology.
_EXPECTED_CLASSIFICATION = {
    "rate_limit": True,
    "authentication_failed": True,
    "billing_error": True,
    "invalid_request": False,
    "server_error": False,
    "unknown": None,
}


def test_every_cli_enum_is_classified_explicitly():
    """A new CLI enum must be placed on purpose, never fall through: the
    full CLI_ERROR_TYPES set must equal this hand-written map (an enum added
    to the contract without a row here goes red), and the True side must be
    exactly the three the user can clear alone."""
    assert {e: cli_error_self_serviceable(e) for e in CLI_ERROR_TYPES} == _EXPECTED_CLASSIFICATION
    assert {e for e in CLI_ERROR_TYPES if cli_error_self_serviceable(e)} == {
        "rate_limit", "authentication_failed", "billing_error",
    }


def test_self_serviceable_set_is_a_subset_of_the_cli_enums():
    """A typo in the set ("rate_limits") would classify nothing and fail
    nothing without this: every member must be a real CLI enum."""
    assert _SELF_SERVICEABLE_CLI_ERROR_TYPES <= CLI_ERROR_TYPES
    assert _SELF_SERVICEABLE_CLI_ERROR_TYPES == {
        "rate_limit", "authentication_failed", "billing_error",
    }


# ── adapter event builders ───────────────────────────────────────────────


def test_inline_error_event_carries_the_flag():
    rate = _inline_assistant_error_event("rate_limit", [], "Opus is at capacity")
    assert rate["data"]["self_serviceable"] is True
    assert rate["data"]["error_type"] == "rate_limit"

    server = _inline_assistant_error_event("server_error", ["529 overloaded"], "")
    assert server["data"]["self_serviceable"] is False

    # The CLI did not classify it either: no verdict, not a fabricated False.
    unknown = _inline_assistant_error_event("unknown", [], "")
    assert "self_serviceable" not in unknown["data"]


def test_zero_output_event_is_not_self_serviceable():
    assert _zero_output_error_event([])["data"]["self_serviceable"] is False


def test_output_transfer_inline_error_carries_the_flag():
    (event,) = output_transfer(
        AssistantMessage([TextBlock("")], error="rate_limit"),
        transfer_type="claude_agent_sdk",
        streaming=True,
    )
    assert event["data"]["type"] == "response.error"
    assert event["data"]["self_serviceable"] is True

    (event,) = output_transfer(
        AssistantMessage([TextBlock("")], error="server_error"),
        transfer_type="claude_agent_sdk",
        streaming=True,
    )
    assert event["data"]["self_serviceable"] is False

    (event,) = output_transfer(
        AssistantMessage([TextBlock("")], error="unknown"),
        transfer_type="claude_agent_sdk",
        streaming=True,
    )
    assert "self_serviceable" not in event["data"]


# ── ResponseProcessor → ErrorMessage ─────────────────────────────────────


def _process(data: dict) -> ErrorMessage:
    rp = ResponseProcessor()
    results = list(rp.process(
        {"type": "raw_response_event", "data": {"type": "response.error", **data}},
        ExecutionState(),
    ))
    msgs = [r.message for r in results if isinstance(r.message, ErrorMessage)]
    assert len(msgs) == 1
    return msgs[0]


def test_recoverable_error_forwards_the_flag_to_the_wire():
    msg = _process({
        "error_type": "rate_limit",
        "error_message": "You've hit your limit · resets 4pm",
        "self_serviceable": True,
    })
    assert msg.severity == "recoverable"
    assert msg.self_serviceable is True

    msg = _process({
        "error_type": "server_error",
        "error_message": "529 overloaded",
        "self_serviceable": False,
    })
    assert msg.severity == "recoverable"
    assert msg.self_serviceable is False


def test_driver_without_the_flag_leaves_it_unset():
    """Other drivers (codex / nexus_power) do not classify; unknown stays None,
    never a fabricated False."""
    msg = _process({"error_type": "turn.failed", "error_message": "boom"})
    assert msg.self_serviceable is None


def test_fatal_user_fixable_classes_are_flagged_true():
    auth = _process({"error_type": "unauthorized", "error_message": "log out and sign in again"})
    assert auth.error_type == AUTH_EXPIRED_ERROR_TYPE
    assert auth.self_serviceable is True

    cfg = _process({
        "error_type": "unknown",
        "error_message": "inputs 75307 tokens exceed the 32769 context window",
    })
    assert cfg.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert cfg.self_serviceable is True


# ── step_3 raw-exception exit (mirrors the processor's inline exit) ──────


def test_step3_raw_exception_config_actionable_is_flagged_true():
    err = _raw_exception_error(
        "This turn could not run: ...", SELF_SERVICEABLE_ERROR_TYPE,
        "fatal", "context_window",
    )
    assert err.error_type == SELF_SERVICEABLE_ERROR_TYPE
    assert err.action_reason == "context_window"
    assert err.severity == "fatal"
    assert err.self_serviceable is True


def test_step3_raw_exception_executor_infra_stays_unclassified():
    """The platform-side control group: never True, and not a fabricated
    False either (the field's contract is True / None for now)."""
    err = _raw_exception_error(
        "execution environment ran out of memory", EXECUTOR_INFRA_ERROR_TYPE,
        "recovered_after_reply", "executor_oom",
    )
    assert err.error_type == EXECUTOR_INFRA_ERROR_TYPE
    assert err.action_reason == "executor_oom"
    assert err.self_serviceable is None


def test_error_message_serialises_the_flag():
    wire = ErrorMessage(error_message="x", error_type="rate_limit", self_serviceable=True).model_dump()
    assert wire["self_serviceable"] is True
    assert ErrorMessage(error_message="x").model_dump()["self_serviceable"] is None
