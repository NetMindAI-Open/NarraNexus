"""
@file_name: test_auth_login_scope_allowlist.py
@date: 2026-04-22
@description: Validates that `auth login --scope X` is allowed for
incremental scope top-ups, while bare `auth login` / `auth login
--recommend` remain blocked (must route through lark_permission_advance).
"""

from __future__ import annotations

import pytest

from narranexus_plugins.lark_module._lark_command_security import (
    validate_command,
)


class TestAuthLoginBlocked:
    @pytest.mark.parametrize("cmd", [
        "auth login",
        "auth login --domain all",
        "auth login --recommend",
        "auth login --recommend --json --no-wait",
        "auth login --domain all --recommend --json",
    ])
    def test_bare_or_recommend_forms_blocked(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok is False
        assert (
            "lark_permission_advance" in reason
            or "three-click" in reason
            or "reserved" in reason
        )


class TestAuthLoginWithScopeAllowed:
    @pytest.mark.parametrize("cmd", [
        'auth login --scope "im:message:send_as_user"',
        'auth login --scope im:message:send_as_user',
        'auth login --scope "im:message:send_as_bot" --json --no-wait',
        'auth login --scope "contact:user.base:readonly calendar:calendar" --json',
    ])
    def test_scope_targeted_login_allowed(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok, f"expected allow, got block: {reason}"


class TestAuthLoginDeviceCodePollAllowed:
    """`auth login --device-code <D>` is the canonical POLL step of the
    incremental-auth dance (mint via `--no-wait`, then poll via
    `--device-code`). It does NOT take `--scope` — the scope was named
    at mint time. Our security validator used to require `--scope` on
    every `auth login` form, which incorrectly blocked the poll — agent
    then worked around by wedging `--device-code` into a --scope call,
    producing the garbled `auth login --device-code --as ...` commands
    and the loop observed with agent_7f357515e25a / agent_bbddea03706e
    on 2026-04-23."""

    @pytest.mark.parametrize("cmd", [
        "auth login --device-code Oy4P4ZdufyfihQ1w",
        "auth login --device-code Oy4P4Z --json",
        'auth login --device-code "Oy4P4ZdufyfihQ1w-rIkY1b72Z_IonajGq-OOOOOOOOO_8VlGWOOOOOt"',
    ])
    def test_device_code_poll_without_scope_allowed(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok, (
            f"`auth login --device-code <D>` is the standard POLL step "
            f"of incremental auth and must be allowed. Got block: {reason}"
        )

    def test_device_code_combined_with_scope_still_allowed(self):
        """Defensive: a belt-and-suspenders form where the agent passes
        both `--scope X` and `--device-code D` must also be allowed
        (matches the pre-fix workaround that agents learned during the
        loop; blocking it would regress agents mid-flight)."""
        ok, reason = validate_command(
            'auth login --scope "space:document:retrieve" --device-code Oy4P4Z'
        )
        assert ok, f"expected allow, got block: {reason}"


class TestAuthLoginRecommendWithScopeStillBlocked:
    def test_recommend_plus_scope_is_still_blocked(self):
        """Defense against an Agent trying to sneak --recommend past the gate
        by also adding --scope. --recommend is reserved for initial three-click."""
        ok, reason = validate_command(
            'auth login --scope "im:chat:readonly" --recommend --json'
        )
        assert ok is False
        assert "reserved" in reason.lower() or "recommend" in reason.lower()


class TestOtherAuthSubcommandsUnaffected:
    @pytest.mark.parametrize("cmd", [
        "auth status",
        "auth status --json",
        "auth check",
        "auth scopes",
        "auth list",
    ])
    def test_readonly_subcommands_still_allowed(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok, f"expected allow, got block: {reason}"

    def test_auth_logout_still_blocked(self):
        ok, reason = validate_command("auth logout")
        assert ok is False

    def test_unknown_auth_subcommand_rejected(self):
        ok, reason = validate_command("auth hack")
        assert ok is False
        assert "auth hack" in reason or "not allowed" in reason
