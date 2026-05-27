"""
Test the Lark error translator — what cryptic lark-cli outputs map to what
user-facing structured messages.

Why these tests matter: the translator is what stands between "user sees
`99991672 App scope not enabled` and gives up" and "user sees 'Required
permission scope is not enabled — open the developer console, enable the
scope shown in the error, then publish a new version' and fixes it". A
regression in the table or matching order breaks that promise silently
(the bind still 'works', just goes back to cryptic).
"""
from __future__ import annotations

from xyz_agent_context.module.lark_module._lark_error_translator import (
    ErrorTranslation,
    translate,
)


# ── Numeric code lookups ────────────────────────────────────────────────────

def test_translate_invalid_credentials_99991663():
    t = translate(
        error_message="invalid app secret",
        error_data={"code": 99991663, "message": "invalid app secret"},
    )
    assert t.code == "99991663"
    assert "App ID or App Secret" in t.title
    assert "Credentials & Basic Info" in t.action_hint
    assert t.severity == "error"


def test_translate_missing_scope_99991672():
    t = translate(
        error_message="99991672 App scope not enabled",
        error_data={"code": 99991672, "console_url": "https://open.feishu.cn/app/cli_xxx"},
    )
    assert t.code == "99991672"
    assert "permission scope" in t.title.lower()
    assert "Create version & publish" in t.action_hint
    # console_url must round-trip so the UI can render a clickable link
    assert t.console_url == "https://open.feishu.cn/app/cli_xxx"


def test_translate_brand_mismatch_1000040351():
    t = translate(
        error_message="Incorrect domain name",
        error_data={"code": 1000040351, "message": "Incorrect domain name"},
    )
    assert t.code == "1000040351"
    assert "Platform mismatch" in t.title or "brand" in t.title.lower()
    # Action hint must mention both platforms so the user knows which to pick
    assert "Feishu" in t.action_hint and "Lark" in t.action_hint


def test_translate_extracts_leading_code_from_message_when_data_missing():
    # Some lark-cli paths only put the code in the message text, not in error_data
    t = translate(error_message="99991672 App scope not enabled")
    assert t.code == "99991672"
    assert "permission scope" in t.title.lower()


# ── Regex pattern fallbacks ─────────────────────────────────────────────────

def test_translate_lark_cli_not_found():
    t = translate(
        error_message="lark-cli not found. Install: npm install -g @larksuite/cli"
    )
    assert "lark-cli is not installed" in t.title
    assert "npm install" in t.action_hint


def test_translate_timeout():
    t = translate(error_message="CLI command timed out after 60s")
    assert "timed out" in t.title.lower()
    assert "open.feishu.cn" in t.action_hint or "open.larksuite.com" in t.action_hint


def test_translate_invalid_app_secret_pattern():
    # No numeric code, only message pattern — pattern table should still hit
    t = translate(error_message="Invalid app secret provided.")
    assert "App Secret" in t.title and "incorrect" in t.title.lower()


def test_translate_invalid_app_id_pattern():
    t = translate(error_message="invalid app_id 'foo_bar'")
    assert "App ID" in t.title
    assert "cli_" in t.action_hint


def test_translate_missing_scope_text_only():
    t = translate(error_message="missing scope: im:message")
    assert "scope" in t.title.lower()


# ── Generic fallback ────────────────────────────────────────────────────────

def test_translate_unknown_error_falls_back_with_raw_preserved():
    t = translate(
        error_message="quantum entanglement collapsed the binding",
        error_data={"code": 4242, "message": "quantum entanglement collapsed the binding"},
    )
    assert t.code == "4242"
    assert "binding failed" in t.title.lower()
    # Raw message must be preserved so the user still sees *something* specific
    assert "quantum entanglement" in t.raw_message
    # message field falls back to raw_msg (or generic) — never empty
    assert t.message


def test_translate_empty_input_does_not_crash():
    t = translate()
    assert isinstance(t, ErrorTranslation)
    assert t.title
    assert t.action_hint  # generic guidance is always given


def test_translate_console_url_only_in_error_data():
    # When console_url comes from error_data, it survives even if no code matches
    t = translate(
        error_message="some unexpected message",
        error_data={"console_url": "https://open.larksuite.com/app/cli_xxx/permission"},
    )
    assert t.console_url == "https://open.larksuite.com/app/cli_xxx/permission"


def test_to_dict_serialises_all_fields():
    t = translate(error_message="lark-cli not found")
    d = t.to_dict()
    assert set(d.keys()) == {
        "code", "severity", "title", "message",
        "action_hint", "console_url", "raw_message",
    }
