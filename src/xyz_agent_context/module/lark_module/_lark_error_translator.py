"""
@file_name: _lark_error_translator.py
@date: 2026-05-27
@description: Translate raw lark-cli / Feishu OpenAPI errors into user-friendly,
actionable structured errors for the frontend bind UX.

Why this exists: the previous bind flow shoved the raw lark-cli stderr / JSON
error.message straight to the frontend, which painted it inside a red alert
div. Users saw things like `"99991672 App scope not enabled"` or `"Credential
verification failed"` with no idea what to do. The translator turns these into
a structured `{title, message, action_hint, console_url}` shape the UI can
render as a clear "what happened + what to do + click here" card.

Lookup strategy (priority order):
  1. **Numeric error code** (e.g. 99991672) — most reliable, comes from
     lark-cli JSON error.code or is the leading number of the error.message.
  2. **Message regex** — fallback for errors lark-cli emits without a code
     (CLI not installed, network timeout, validation rejects).
  3. **Generic fallback** — keeps the raw message so the user still has *some*
     signal even when we don't recognise the error.

The mapping is intentionally curated, not exhaustive. We translate the errors
we have *seen happen* in real binds (per investigation 2026-05-27); unknown
codes pass through to the generic fallback. Adding a new entry when a user
reports a confusing error is a one-line change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class ErrorTranslation:
    """Structured, frontend-ready representation of a Lark bind failure."""

    code: str = ""                 # Numeric Feishu code as string, or "" if unknown
    severity: str = "error"        # 'error' | 'warning' | 'info'
    title: str = ""                # Short headline (e.g. "App Secret is incorrect")
    message: str = ""              # 1-2 sentence explanation
    action_hint: str = ""          # Step user can take to fix it
    console_url: str = ""          # Click-through to the relevant Lark console page
    raw_message: str = ""          # The original lark-cli message, kept for diagnostics

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Curated code → translation table ───────────────────────────────────────
# Source codes:
#   99991663  invalid app_id / app_secret (token endpoint rejects)
#   99991672  app scope not enabled (one or more required scopes not granted)
#   1000040351 incorrect domain name (brand mismatch — feishu vs lark)
#   1254040   rate-limited
#   1254301   permission denied on API call
# The table is the curated subset we have actually seen / handle specifically.
_CODE_TABLE: dict[str, dict[str, str]] = {
    "99991663": {
        "title": "App ID or App Secret is incorrect",
        "message": (
            "The Lark/Feishu open platform rejected the credentials. "
            "Either the App ID does not exist or the App Secret does not match it."
        ),
        "action_hint": (
            "Open the Lark developer console, go to your app's "
            "Credentials & Basic Info page, and copy BOTH the App ID and the "
            "App Secret exactly. Watch out for trailing spaces."
        ),
    },
    "99991672": {
        "title": "Required permission scope is not enabled",
        "message": (
            "Your app is missing one or more permission scopes that NarraNexus needs."
        ),
        "action_hint": (
            "In the developer console: Permissions & Scopes → enable the scope shown "
            "in the error, then click \"Create version & publish\" to apply. The bind "
            "may need to be retried after the scope takes effect."
        ),
    },
    "1000040351": {
        "title": "Platform mismatch (Feishu vs Lark)",
        "message": (
            "You selected one platform but the App ID is registered on the other. "
            "Feishu apps live on open.feishu.cn (mainland China), Lark apps live "
            "on open.larksuite.com (International). They are not interchangeable."
        ),
        "action_hint": (
            "Unbind the current binding, then re-bind and pick the correct platform "
            "(Feishu for mainland China, Lark for International)."
        ),
    },
    "1254040": {
        "title": "Rate limit hit — please retry shortly",
        "message": (
            "Lark/Feishu rate-limited this request. This usually clears within a minute."
        ),
        "action_hint": "Wait ~60 seconds and click Bind again.",
    },
    "1254301": {
        "title": "Permission denied by Lark/Feishu",
        "message": (
            "The Lark/Feishu API rejected the call as unauthorised. Most often this "
            "means a required scope is enabled but not yet published, or the bot is "
            "not added to the relevant chat."
        ),
        "action_hint": (
            "Verify scopes are *published* in the developer console (not just "
            "enabled in draft), and that the bot has been invited to the chats it "
            "needs to read."
        ),
    },
}


# ── Pattern fallbacks (for errors with no numeric code) ────────────────────
# Each entry: (compiled regex, translation dict).
# Patterns are matched against the raw error message in order.
_PATTERN_TABLE: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (
        re.compile(r"lark-cli\s+not\s+found", re.I),
        {
            "title": "lark-cli is not installed on the server",
            "message": (
                "NarraNexus uses the lark-cli command-line tool to talk to "
                "Lark/Feishu. It is missing from this NarraNexus install."
            ),
            "action_hint": (
                "On the NarraNexus host, run: npm install -g @larksuite/cli "
                "(requires Node.js ≥ 18). For local dmg installs this is "
                "auto-installed on first launch; if you're seeing this, check "
                "the desktop app logs for the install step."
            ),
        },
    ),
    (
        re.compile(r"timed?\s*out", re.I),
        {
            "title": "Request to Lark/Feishu timed out",
            "message": (
                "NarraNexus could not reach the Lark/Feishu server within the "
                "expected time."
            ),
            "action_hint": (
                "Check network connectivity from the NarraNexus host to "
                "open.feishu.cn (Feishu) or open.larksuite.com (Lark). "
                "Retry; this is often transient."
            ),
        },
    ),
    (
        re.compile(r"invalid\s+app[_\-\s]*secret", re.I),
        {
            "title": "App Secret is incorrect",
            "message": "The App Secret you pasted does not match the App ID.",
            "action_hint": (
                "Copy the App Secret from the developer console's Credentials "
                "& Basic Info page again. Watch out for invisible whitespace."
            ),
        },
    ),
    (
        re.compile(r"invalid\s+app[_\-\s]*id", re.I),
        {
            "title": "App ID is invalid",
            "message": (
                "The App ID either does not exist on the selected platform or "
                "is malformed."
            ),
            "action_hint": (
                "App IDs should start with `cli_`. Copy it from the developer "
                "console's Credentials & Basic Info page. If the ID is correct, "
                "confirm you selected the right platform (Feishu vs Lark)."
            ),
        },
    ),
    (
        re.compile(r"missing[_\s]*scope", re.I),
        {
            "title": "Required permission scope is missing",
            "message": (
                "Your app does not have the permission scope this operation needs."
            ),
            "action_hint": (
                "In the developer console: Permissions & Scopes → enable the "
                "scope shown in the error, then publish a new version."
            ),
        },
    ),
]


# ── Public API ─────────────────────────────────────────────────────────────

def translate(
    error_message: str = "",
    error_data: dict[str, Any] | None = None,
) -> ErrorTranslation:
    """Translate a raw lark-cli / OpenAPI error into a structured, user-facing
    explanation.

    `error_data` is the parsed JSON error object lark-cli returns (with `code`,
    `message`, `console_url`). Pass it when available — it gives the highest-
    fidelity match via the numeric `code`. `error_message` is the plain-string
    fallback when only stderr is available.

    Always returns a populated ErrorTranslation — never raises. Unknown errors
    get a generic-but-honest fallback so the user still sees something.
    """
    error_data = error_data or {}
    raw_msg = (
        error_message
        or (error_data.get("message") if isinstance(error_data, dict) else "")
        or ""
    ).strip()
    console_url = ""
    if isinstance(error_data, dict):
        console_url = str(error_data.get("console_url") or "")

    # 1) Numeric code lookup (most reliable)
    code = ""
    if isinstance(error_data, dict):
        raw_code = error_data.get("code")
        if raw_code is not None:
            code = str(raw_code)
    if not code:
        # Try to extract a leading numeric code from the message text.
        m = re.match(r"^\s*(\d{4,12})\b", raw_msg)
        if m:
            code = m.group(1)

    if code and code in _CODE_TABLE:
        entry = _CODE_TABLE[code]
        return ErrorTranslation(
            code=code,
            severity="error",
            title=entry["title"],
            message=entry["message"],
            action_hint=entry["action_hint"],
            console_url=console_url,
            raw_message=raw_msg,
        )

    # 2) Regex pattern fallback
    for pattern, entry in _PATTERN_TABLE:
        if pattern.search(raw_msg):
            return ErrorTranslation(
                code=code,
                severity="error",
                title=entry["title"],
                message=entry["message"],
                action_hint=entry["action_hint"],
                console_url=console_url,
                raw_message=raw_msg,
            )

    # 3) Generic fallback — preserve the raw message so the user has *something*
    return ErrorTranslation(
        code=code,
        severity="error",
        title="Lark/Feishu binding failed",
        message=raw_msg or "An unknown error occurred while binding the Lark bot.",
        action_hint=(
            "Check the App ID and App Secret are copied exactly, the selected "
            "platform (Feishu/Lark) matches the app, and required permission "
            "scopes are enabled in the developer console."
        ),
        console_url=console_url,
        raw_message=raw_msg,
    )
