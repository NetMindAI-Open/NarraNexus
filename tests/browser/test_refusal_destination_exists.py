"""
@file_name: test_refusal_destination_exists.py
@author:
@date: 2026-09-22
@description: The place our refusals send the user must actually exist.

From a real report (2026-09-22): the agent correctly refused with "install the
browser from Settings → Browser", and there was no Browser section in
Settings. The agent was right and the app was missing the destination — which,
from the user's seat, is indistinguishable from the agent making things up.

A unit test on either side alone could not catch that: the backend's message
was well-formed and the frontend's components all rendered. Only the link
between them was broken, so that link is what gets asserted here.
"""
from __future__ import annotations

import re
from pathlib import Path

from narranexus.platform.browser.browser_service import BrowserService

REPO = Path(__file__).resolve().parents[2]
BROWSER_SETTINGS = REPO / "frontend/src/components/settings/BrowserSettings.tsx"

#: Settings exists on TWO surfaces and they register separately. The first fix
#: for this bug only added the section to the modal, so `/app/settings` — the
#: one the user actually had open — still had nothing. Both are asserted, or
#: the next person fixes half of it again.
SETTINGS_SURFACES = (
    REPO / "frontend/src/pages/settings/registerBuiltinSections.ts",  # /app/settings
    REPO / "frontend/src/components/settings/SettingsModal.tsx",      # the modal
)


def _absent_service() -> BrowserService:
    async def dl(*, on_progress, cancelled):  # pragma: no cover - never called
        raise AssertionError("must not download in a test")

    return BrowserService(locate=lambda: None, probe=lambda _p: None, downloader=dl)


def test_refusal_points_at_a_section_registered_on_every_settings_surface():
    refusal = _absent_service().require_ready()
    assert refusal is not None

    where = refusal["human_action"]["where"]  # e.g. "settings/browser"
    section = where.split("/")[-1]

    for surface in SETTINGS_SURFACES:
        body = surface.read_text(encoding="utf-8")
        # The page uses `SETTINGS_SECTIONS.register('id', ...)`, the modal uses
        # `{ id: 'id', ... }` — accept either spelling of "this id is listed".
        registered = f"register('{section}'" in body or f"id: '{section}'" in body
        assert registered, (
            f"the refusal sends the user to {where!r}, but {surface.name} does "
            f"not register a settings section with id {section!r} — on that "
            f"surface the user looks for a button that is not there"
        )


def test_that_section_actually_offers_the_install_control():
    """Reaching an empty section is the same dead end as reaching none."""
    body = BROWSER_SETTINGS.read_text(encoding="utf-8")
    assert "browser-settings-install" in body
    assert "installBrowserRuntime" in body


def test_the_install_control_goes_through_the_authenticated_client():
    """A raw fetch here misses the identity headers local mode requires, and
    the resulting 401 is indistinguishable in the UI from "no browser
    installed" — which is what shipped on 2026-09-22."""
    body = BROWSER_SETTINGS.read_text(encoding="utf-8")
    assert "fetch(" not in body, "call /api/browser/* through lib/api, not fetch()"
    assert "api.getBrowserRuntime" in body


def test_every_refusal_action_has_somewhere_to_go():
    """All three refusal shapes (install / reinstall / wait) must carry a
    destination — a message with no `where` leaves the user guessing."""
    svc = _absent_service()
    refusal = svc.require_ready()
    assert refusal is not None
    assert refusal["human_action"]["where"]
    assert refusal["human_action"]["action"] in (
        "install_browser",
        "reinstall_browser",
        "wait_for_install",
    )


def test_agent_instructions_do_not_name_a_path_the_app_does_not_have():
    """The prompt text is the other place a destination gets written down, and
    it drifts silently because nothing imports it."""
    from narranexus_plugins.browser_module.prompts import BROWSER_MODULE_INSTRUCTIONS

    modal = SETTINGS_SURFACES[1].read_text(encoding="utf-8")
    # Any "Settings → X" / "Settings > X" the prompt mentions must be a real section.
    for match in re.finditer(r"Settings\s*(?:→|->|>)\s*([A-Za-z ]+)", BROWSER_MODULE_INSTRUCTIONS):
        named = match.group(1).strip().lower().replace(" ", "")
        assert f"id: '{named}'" in modal, (
            f"the agent prompt tells users to open Settings → {match.group(1).strip()}, "
            f"but no settings section with id {named!r} exists"
        )


# ── the OTHER destination: a login the agent cannot complete ───────────────

APP_SHELL = REPO / "frontend/src/components/layout/MainLayout.tsx"
NOTICES = REPO / "frontend/src/components/layout/BrowserNotices.tsx"


def test_the_login_notice_is_mounted_where_the_user_reads_the_request():
    """The agent asks for a login in chat. A notice that lives only inside the
    browser panel (which may not be open) is a destination the message does
    not travel to — the same dead end this file exists to prevent."""
    shell = APP_SHELL.read_text(encoding="utf-8")
    assert "<BrowserNotices />" in shell, "login notices must be mounted in the app shell, not only in a panel"
    notice = NOTICES.read_text(encoding="utf-8")
    assert "getBrowserNotices" in notice
    assert "BrowserLoginNotice" in notice
