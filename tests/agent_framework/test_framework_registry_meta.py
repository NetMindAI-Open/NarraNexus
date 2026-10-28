"""
@file_name: test_framework_registry_meta.py
@author: Bin Liang
@date: 2026-09-07
@description: The framework registry (FrameworkMeta) is the ONLY source of framework-specific facts — a third-party framework is selectable everywhere a builtin is, and unknown names never degrade to the default.

Deleting the registry-derived helpers in ``loop/driver.py`` (or restoring any
of the old name tables: ``_KNOWN_AGENT_FRAMEWORKS``, ``_SUPPORTED_AGENT_FRAMEWORKS``,
``AGENT_FRAMEWORK_REQUIRED_PROTOCOLS``, ``CLI_FRAMEWORK_BY_OAUTH_SOURCE``,
``FRAMEWORK_DISPLAY_NAMES``, ``_FRAMEWORK_PACKAGE``, ``_LOGIN_MARKERS``) makes
these fail.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta, InstallComponent
from narranexus.platform.agent_framework.loop.driver import (
    default_framework_for_protocol,
    framework_for_oauth_source,
    framework_installed,
    framework_meta,
    framework_registry,
    get_agent_loop_driver,
    FrameworkNotInstalledError,
)
from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
from narranexus.platform.agent_framework.providers.driver.resolver import (
    LLMConfigNotConfigured,
    _agent_framework_from_slot,
    _resolve_slot_target,
)
from narranexus.platform.agent_framework.providers.model_identity import _display_for
from narranexus.platform.agent_framework.providers.user_service import UserProviderService
from narranexus.platform.agent_framework.providers.framework_binding import (
    framework_can_drive_provider,
    get_slot_required_protocols,
)
from narranexus.platform.schema.provider_schema import ProviderProtocol


class _Driver:
    def capabilities(self):
        return set()


@pytest.fixture
def acme_cli():
    """A third-party, openai-locked framework registered like a plugin would."""
    meta = FrameworkMeta(
        "acme_cli", "Acme CLI", protocol="openai", oauth_source="acme_oauth",
        runtime_name="Acme Agent Runtime",
    )
    disposable = framework_registry().register(
        "acme_cli", lambda: (lambda **kw: _Driver()), owner="x.acme", meta={"framework": meta}
    )
    try:
        yield meta
    finally:
        disposable.dispose()


def _card(protocol: str) -> ProviderCard:
    return ProviderCard.from_row({
        "provider_id": "p1", "user_id": "u", "name": "p", "source": "user",
        "protocol": protocol, "auth_type": "api_key",
    })


def test_builtin_metas_carry_framework_facts():
    assert framework_meta("claude_code").protocol == "anthropic"
    assert framework_meta("claude_code").oauth_source == "claude_oauth"
    assert framework_meta("codex_cli").agent_protocols == ("openai",)
    assert framework_meta("nexus_power").agent_protocols == ("anthropic", "openai")
    assert framework_meta("nexus_power").oauth_source is None
    with pytest.raises(UnknownEntry):
        framework_meta("ghost")


def test_third_party_framework_drives_resolver_by_protocol(acme_cli):
    # An openai-locked framework gets the codex config shape without any edit
    # to the resolver.
    assert _resolve_slot_target("agent", "acme_cli", _card("openai")) == ("build_codex_config", "codex")
    assert _agent_framework_from_slot({"agent_framework": "acme_cli"}) == "acme_cli"


def test_unknown_slot_framework_fails_loud_instead_of_defaulting():
    with pytest.raises(LLMConfigNotConfigured, match="ghost"):
        _agent_framework_from_slot({"agent_framework": "ghost"})
    # No framework on the slot → the bound default (not an error).
    assert _agent_framework_from_slot({}) == "nexus_power"


def test_default_framework_for_protocol_prefers_locked_then_agnostic(monkeypatch):
    from narranexus.platform.agent_framework import plugin_paths

    # Every on-demand plugin installed (cloud image / a desktop that installed them).
    monkeypatch.setattr(plugin_paths, "package_installed", lambda fw, pkg: True)
    assert default_framework_for_protocol("anthropic") == "claude_code"
    assert default_framework_for_protocol("openai") == "codex_cli"
    assert default_framework_for_protocol("weird") == "nexus_power"


def test_default_framework_for_protocol_skips_uninstalled_plugins(monkeypatch):
    """Lightweight local build: Claude Code / Codex are registered but their
    plugins are not installed. A fresh card must land on a framework that can
    actually run (the host-shipped nexus_power), never on the uninstalled one."""
    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setattr(plugin_paths, "package_installed", lambda fw, pkg: False)
    assert default_framework_for_protocol("anthropic") == "nexus_power"
    assert default_framework_for_protocol("openai") == "nexus_power"

    # Only Claude Code installed → anthropic cards pair with it again, openai
    # cards still fall through to the installed protocol-agnostic framework.
    monkeypatch.setattr(plugin_paths, "package_installed", lambda fw, pkg: fw == "claude_code")
    assert default_framework_for_protocol("anthropic") == "claude_code"
    assert default_framework_for_protocol("openai") == "nexus_power"


def test_framework_not_installed_error_names_both_ways_out():
    """A binding stored before onboarding skipped uninstalled frameworks is not
    rewritten; the run error must tell that user where to move it, not only
    where to install the plugin."""
    msg = str(FrameworkNotInstalledError("claude_code"))
    assert "Settings → Plugins" in msg
    assert "Settings → Model Defaults" in msg
    assert "'claude_code'" in msg


def test_oauth_source_ownership_is_registry_derived(acme_cli):
    assert framework_for_oauth_source("claude_oauth") == "claude_code"
    assert framework_for_oauth_source("acme_oauth") == "acme_cli"
    assert framework_for_oauth_source("user") is None
    assert framework_for_oauth_source(None) is None
    assert framework_can_drive_provider("acme_cli", source="acme_oauth", auth_type="oauth", protocol="openai")
    assert not framework_can_drive_provider("codex_cli", source="acme_oauth", auth_type="oauth", protocol="openai")


def test_slot_protocols_follow_registered_framework(acme_cli):
    assert get_slot_required_protocols("agent", agent_framework="acme_cli") == [ProviderProtocol.OPENAI]
    assert get_slot_required_protocols("agent", agent_framework=None) == [
        ProviderProtocol.ANTHROPIC, ProviderProtocol.OPENAI,
    ]
    with pytest.raises(ValueError, match="ghost"):
        get_slot_required_protocols("agent", agent_framework="ghost")


def test_supported_frameworks_include_third_party(acme_cli):
    assert "acme_cli" in UserProviderService.supported_agent_frameworks()


def test_framework_installed_is_registry_driven(acme_cli, monkeypatch):
    assert framework_installed("nexus_power") is True
    assert framework_installed("acme_cli") is True  # no install recipe → host-shipped
    assert framework_installed("ghost") is False
    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setattr(plugin_paths, "package_installed", lambda fw, pkg: False)
    assert framework_installed("claude_code") is False
    with pytest.raises(FrameworkNotInstalledError):
        get_agent_loop_driver("claude_code")


def test_install_probe_reads_meta_probe_package(monkeypatch):
    seen: list[tuple[str, str]] = []
    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setattr(plugin_paths, "package_installed", lambda fw, pkg: seen.append((fw, pkg)) or True)
    install = FrameworkInstall(
        components=(InstallComponent(kind="pip", requirement="acme==1"),),
        probe_package="acme_sdk", user_version_source="pip_pkg", size_hint="1 MB",
    )
    d = framework_registry().register(
        "acme_pip", lambda: (lambda **kw: _Driver()), owner="x.acme",
        meta={"framework": FrameworkMeta("acme_pip", "Acme", install=install)},
    )
    try:
        assert framework_installed("acme_pip") is True
    finally:
        d.dispose()
    assert seen == [("acme_pip", "acme_sdk")]


def test_agent_self_description_comes_from_meta(acme_cli):
    assert _display_for("claude_code") == "Claude Agent SDK"
    assert _display_for("nexus_power") == "NexusPower-beta"
    assert _display_for("acme_cli") == "Acme Agent Runtime"
    assert _display_for("some_future_cli") == "some_future_cli"


def test_plugin_spec_carries_login_marker():
    from backend.integrations.plugins.registry import build_plugin_specs

    specs = build_plugin_specs()
    assert specs["claude_code"].login_marker == (".claude", ".credentials.json")
    assert specs["codex_cli"].login_marker == (".codex", "auth.json")
