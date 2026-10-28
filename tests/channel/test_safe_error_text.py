"""
@file_name: test_safe_error_text.py
@author: Bin Liang
@date: 2026-09-10
@description: The channel base's one sanitised error rendering (safe_error_text)
and its two caps: DISABLE_REASON_MAX_CHARS for the credential row the panel
shows, AUDIT_ERROR_MAX_CHARS for audit rows. Platform-level — no channel SDK
involved; the Telegram loop tests exercise it end to end.
"""
from __future__ import annotations


def test_safe_error_text_masks_urls_tokens_and_truncates():
    from narranexus.platform.channel.channel_trigger_base import (
        DISABLE_REASON_MAX_CHARS,
        safe_error_text,
    )

    exc = ConnectionError(
        "getUpdates failed (client_error:InvalidURL: "
        "https://api.telegram.org/bot7981632450:AAHsecretsecretsecretsecret/getUpdates " + "x" * 400 + ")"
    )
    reason = safe_error_text(exc)
    assert reason.startswith("ConnectionError: getUpdates failed")
    assert "7981632450:AAH" not in reason and "api.telegram.org" not in reason
    assert len(reason) <= DISABLE_REASON_MAX_CHARS

    plain = safe_error_text(PermissionError("getUpdates failed (HTTP 401: Unauthorized)"))
    assert plain == "PermissionError: getUpdates failed (HTTP 401: Unauthorized)"

    multiline = safe_error_text(RuntimeError("line one\nline two"))
    assert multiline == "RuntimeError: line one line two"


def test_safe_error_text_has_separate_caps_for_panel_and_audit():
    from narranexus.platform.channel.channel_trigger_base import (
        AUDIT_ERROR_MAX_CHARS,
        DISABLE_REASON_MAX_CHARS,
        safe_error_text,
    )

    exc = RuntimeError("word " * 150)  # spaces: no 32+ char run to mask
    assert len(safe_error_text(exc)) == DISABLE_REASON_MAX_CHARS == 200
    assert len(safe_error_text(exc, AUDIT_ERROR_MAX_CHARS)) == AUDIT_ERROR_MAX_CHARS == 500
