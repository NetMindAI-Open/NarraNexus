"""
@file_name: test_permission_state_helpers.py
@date: 2026-04-22
@description: Unit tests for LarkCredential.current_click_stage() and related
helpers introduced by the three-click authorization redesign.

"""

from __future__ import annotations

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredential,
)


def _cred(permission_state: dict | None = None) -> LarkCredential:
    """Minimal LarkCredential fixture with only permission_state varying."""
    return LarkCredential(
        agent_id="agent_test",
        app_id="cli_test",
        app_secret_ref="",
        brand="lark",
        profile_name="agent_test",
        permission_state=permission_state or {},
    )


class TestCurrentClickStage:
    def test_empty_state_is_not_started(self):
        assert _cred().current_click_stage() == "not_started"

    def test_admin_request_only_is_waiting_admin(self):
        cred = _cred({"admin_request_url": "https://x", "admin_request_device_code": "dc1"})
        assert cred.current_click_stage() == "waiting_admin"

    def test_admin_approved_without_authz_url_still_waiting_admin(self):
        # admin_approved_at alone doesn't advance stage — the mint of Click 3
        # URL (user_authz_url) is what flips us forward. If admin_approved_at
        # is set but user_authz_url isn't, something went wrong in the tool,
        # and we stay at waiting_admin so the tool can be retried.
        cred = _cred({
            "admin_request_url": "https://x",
            "admin_approved_at": "2026-04-22T10:00:00Z",
        })
        assert cred.current_click_stage() == "waiting_admin"

    def test_user_authz_url_is_waiting_user_click(self):
        cred = _cred({
            "admin_request_url": "https://x",
            "admin_approved_at": "2026-04-22T10:00:00Z",
            "user_authz_url": "https://y",
            "user_authz_device_code": "dc3",
        })
        assert cred.current_click_stage() == "waiting_user_click"

    def test_completed_short_circuits_earlier_fields(self):
        # Even if earlier fields are still populated, completed timestamp wins.
        cred = _cred({
            "admin_request_url": "https://x",
            "user_authz_url": "https://y",
            "user_oauth_completed_at": "2026-04-22T10:05:00Z",
        })
        assert cred.current_click_stage() == "completed"

    def test_none_permission_state_is_not_started(self):
        # Defensive: dataclass default is {}, but if something sets it to None
        # at runtime, don't crash.
        cred = LarkCredential(
            agent_id="a",
            app_id="cli_x",
            app_secret_ref="",
            brand="lark",
            profile_name="a",
            permission_state=None,  # type: ignore[arg-type]
        )
        assert cred.current_click_stage() == "not_started"


class TestUserOauthOk:
    def test_no_completed_at(self):
        assert _cred().user_oauth_ok() is False

    def test_with_completed_at(self):
        assert _cred({"user_oauth_completed_at": "2026-04-22T10:00:00Z"}).user_oauth_ok() is True


class TestReceiveEnabled:
    def test_no_secret(self):
        assert _cred().receive_enabled() is False

    def test_with_secret(self):
        cred = _cred()
        cred.app_secret_encoded = "base64stuff"
        assert cred.receive_enabled() is True
