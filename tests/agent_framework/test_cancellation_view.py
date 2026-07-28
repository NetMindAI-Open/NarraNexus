"""
@file_name: test_cancellation_view.py
@date: 2026-07-27
@description: Tests for CancellationView — the single normalization point
for cooperative-cancellation checks across drivers.

Background: three styles coexisted at driver call sites —
``token.is_cancelled`` (bool property, the real CancellationToken API),
``token.is_set()`` (asyncio.Event style; CodexSDKv2 guessed this and its
check was therefore DEAD CODE against a real token), and ``None``.
CancellationView collapses them into one ``requested()`` answer.
"""
from __future__ import annotations

import asyncio

from xyz_agent_context.agent_framework.loop.cancellation_view import (
    CancellationView,
)
from xyz_agent_context.agent_runtime.cancellation import CancellationToken


def test_none_token_never_requests():
    assert CancellationView(None).requested() is False


def test_real_cancellation_token_property_style():
    token = CancellationToken()
    view = CancellationView(token)
    assert view.requested() is False
    token.cancel("test stop")
    assert view.requested() is True


def test_event_style_is_set_method():
    event = asyncio.Event()
    view = CancellationView(event)
    assert view.requested() is False
    event.set()
    assert view.requested() is True


def test_object_with_neither_api_is_never_cancelled():
    class Opaque:
        pass

    assert CancellationView(Opaque()).requested() is False


def test_codex_dead_check_regression():
    """The exact incident this class exists for: CodexSDKv2 read
    ``getattr(token, "is_set", lambda: False)()`` — always False for a
    real CancellationToken, so in-process codex turns could never be
    interrupted. The view must see the property."""
    token = CancellationToken()
    token.cancel("stop requested")
    # old broken pattern, kept here as documentation of the bug:
    assert getattr(token, "is_set", lambda: False)() is False
    # the view gets it right:
    assert CancellationView(token).requested() is True


def test_property_wins_over_method_when_both_exist():
    """If an exotic token exposes both APIs, the documented
    CancellationToken API (is_cancelled property) is authoritative."""

    class Both:
        is_cancelled = True

        @staticmethod
        def is_set() -> bool:
            return False

    assert CancellationView(Both()).requested() is True
