"""
@file_name: test_executor_infra_failure.py
@date: 2026-07-22
@description: Unit tests for classify_executor_infra_failure — the classifier
for PLATFORM-side executor infrastructure failures (subprocess OOM kill,
executor/broker unreachable). Distinct from classify_self_serviceable (which
covers user-config-fixable failures): these are not fixed by changing settings,
so the owner-facing guidance is "retry / split the task", and — like the
self-serviceable class — a helper-LLM fallback reply MUST NOT paper over them.

Two recognition channels:
  - OOM: the subprocess died from a signal, surfaced as a returncode string
    ("exit code -9" = SIGKILL/OOM, "exit code -6" = SIGABRT). String match is
    the only signal available (it's a child-process returncode).
  - Unreachable: the executor boundary raised the typed ExecutorUnreachableError
    — matched by exception class NAME, not fragile substring matching.

Critical negative case: a USER's LLM-provider connection blip (APIConnectionError,
"Connection error to api.openai.com") must NOT be classified here — that is a
transient the circuit breaker should retry, not an executor-infra fatal.
"""

import pytest

from xyz_agent_context.agent_framework.llm_failure import (
    classify_executor_infra_failure,
    classify_self_serviceable,
    EXECUTOR_INFRA_REASON_OOM,
    EXECUTOR_INFRA_REASON_UNREACHABLE,
)


@pytest.mark.parametrize(
    "error_type,error_message,expected",
    [
        # OOM — SIGKILL (exit code -9): the canonical executor OOM kill
        (
            "RuntimeError",
            "Command failed with exit code -9",
            EXECUTOR_INFRA_REASON_OOM,
        ),
        # OOM — SIGABRT (exit code -6): previously uncovered
        (
            "RuntimeError",
            "AGENT-LOOP-FATAL RuntimeError: Command failed with exit code -6",
            EXECUTOR_INFRA_REASON_OOM,
        ),
        # unreachable — matched by the typed exception class name
        (
            "ExecutorUnreachableError",
            "Executor unreachable at http://nx-exec-abc:8020: ClientConnectorError",
            EXECUTOR_INFRA_REASON_UNREACHABLE,
        ),
        # unreachable — broker down at cold start (same typed exception)
        (
            "ExecutorUnreachableError",
            "broker at http://broker:9000 unreachable: ConnectError",
            EXECUTOR_INFRA_REASON_UNREACHABLE,
        ),
    ],
)
def test_executor_infra_is_classified(error_type, error_message, expected):
    assert classify_executor_infra_failure(error_type, error_message) == expected


@pytest.mark.parametrize(
    "error_type,error_message",
    [
        # A USER's LLM-provider connection error — transient, NOT executor infra.
        ("APIConnectionError", "Connection error to api.openai.com"),
        ("ConnectionError", "Connection reset by peer"),
        ("APITimeoutError", "Read timed out after 30s"),
        # A normal non-zero process exit (a tool the agent ran failed) is NOT an
        # OOM signal kill — positive exit codes must not match.
        ("RuntimeError", "Command failed with exit code 1"),
        ("RuntimeError", "process exited with code 127"),
        # Self-serviceable config errors belong to the OTHER classifier.
        ("ContextWindowExceededError", "inputs 75307 > 32769"),
        ("billing_error", "insufficient balance"),
        # generic / our-own bug — untouched residual bucket
        ("Exception", "some unexpected internal error"),
    ],
)
def test_non_executor_infra_is_not_classified(error_type, error_message):
    assert classify_executor_infra_failure(error_type, error_message) is None


def test_none_and_empty_return_none():
    assert classify_executor_infra_failure(None, None) is None
    assert classify_executor_infra_failure("", "") is None


@pytest.mark.parametrize(
    "error_type,error_message",
    [
        ("RuntimeError", "Command failed with exit code -9"),
        ("RuntimeError", "Command failed with exit code -6"),
        ("ExecutorUnreachableError", "Executor unreachable at :8020"),
    ],
)
def test_infra_failures_are_not_self_serviceable(error_type, error_message):
    # Cross-contamination guard: an infra failure fed to the self-serviceable
    # classifier must return None (the two classifiers stay disjoint).
    assert classify_self_serviceable(error_type, error_message) is None


@pytest.mark.parametrize(
    "error_type,error_message",
    [
        ("ContextWindowExceededError", "inputs 75307 > 32769"),
        ("billing_error", "insufficient balance"),
        ("unknown", "The model `gpt-nope` does not exist"),
    ],
)
def test_self_serviceable_failures_are_not_infra(error_type, error_message):
    # ...and the reverse: a self-serviceable config error is not executor infra.
    assert classify_executor_infra_failure(error_type, error_message) is None
