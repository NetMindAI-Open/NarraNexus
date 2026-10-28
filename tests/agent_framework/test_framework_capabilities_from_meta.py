"""
@file_name: test_framework_capabilities_from_meta.py
@author: Bin Liang
@date: 2026-09-07
@description: Live steering, native history replay, the cloud gate and step_3's provider-viability check all read the framework REGISTRY — a third-party framework is not silently downgraded.

Round-2 P2-I3 / G2-I7 / A2-5: four host-side tables were keyed on builtin
framework NAMES (``_STEER_CAPABLE_FRAMEWORKS``, ``NATIVE_REPLAY_FRAMEWORKS``,
``CLOUD_ALLOWED_FRAMEWORKS``, ``if framework != "nexus_power"``), so a plugin
framework that really drained a steering inlet got ``capabilities() == set()``
over the executor hop, was always flattened, and could never run on cloud —
each one a silent degradation with no error anywhere. Restore any of the four
frozensets and the matching test below goes red.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.framework import CAPABILITY_VOCABULARY, FrameworkMeta
from narranexus.platform.agent_framework.loop.driver import (
    framework_capabilities,
    framework_has_capability,
    framework_registry,
)
from narranexus.platform.agent_framework.loop.history_projection import (
    framework_supports_native_replay,
)
from narranexus.platform.agent_framework.loop.remote_driver import RemoteAgentLoopDriver
from narranexus.platform.agent_framework.providers import cloud_policy


class _Driver:
    def capabilities(self):
        return set()


def _register(name: str, meta: FrameworkMeta):
    return framework_registry().register(
        name, lambda: (lambda **kw: _Driver()), owner="x.acme", meta={"framework": meta}
    )


@pytest.fixture
def steerable():
    """A third-party framework whose driver drains a steering inlet."""
    meta = FrameworkMeta(
        "acme_turbo", "Acme Turbo",
        capabilities=frozenset({"steering", "native_replay"}),
        uses_shared_cli_login=False,
    )
    d = _register("acme_turbo", meta)
    try:
        yield meta
    finally:
        d.dispose()


@pytest.fixture
def plain():
    """A third-party framework that declares nothing beyond the base contract."""
    meta = FrameworkMeta("acme_plain", "Acme Plain")
    d = _register("acme_plain", meta)
    try:
        yield meta
    finally:
        d.dispose()


def _remote(framework: str) -> RemoteAgentLoopDriver:
    return RemoteAgentLoopDriver(framework=framework, working_path=".", executor_url="http://127.0.0.1:1")


# --- the vocabulary is enforced on the STATIC declaration too ---------------

def test_native_replay_is_part_of_the_vocabulary():
    assert "native_replay" in CAPABILITY_VOCABULARY


def test_a_meta_cannot_declare_a_capability_outside_the_vocabulary():
    with pytest.raises(ValueError, match="CAPABILITY_VOCABULARY"):
        FrameworkMeta("acme_bad", "Acme Bad", capabilities=frozenset({"teleportation"}))


def test_builtin_metas_state_what_their_drivers_ship():
    assert framework_capabilities("nexus_power") == frozenset({"event_log", "steering", "native_replay"})
    assert framework_capabilities("claude_code") == frozenset()
    assert framework_capabilities("codex_cli") == frozenset()
    # Fail-closed on a name nobody registered — never a guess.
    assert framework_capabilities("ghost") == frozenset()


def test_nexus_power_meta_matches_driver_capabilities():
    """The static twin and the runtime authority must agree, or the remote hop
    and the in-process path disagree about the same framework."""
    from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import NexusAgent

    assert NexusAgent(working_path="/tmp").capabilities() == set(framework_capabilities("nexus_power"))


# --- (b) live steering over the executor hop --------------------------------

def test_a_third_party_framework_that_declares_steering_gets_it(steerable):
    assert _remote("acme_turbo").capabilities() == {"steering"}


def test_a_third_party_framework_without_steering_is_refused(plain):
    assert _remote("acme_plain").capabilities() == set()


def test_the_builtins_keep_their_previous_answers():
    assert _remote("nexus_power").capabilities() == {"steering"}
    assert _remote("claude_code").capabilities() == set()
    assert _remote("codex_cli").capabilities() == set()
    # An unregistered framework must not be claimed steerable: the injection
    # then resurfaces as a fresh turn instead of queueing forever.
    assert _remote("ghost").capabilities() == set()


def test_the_remote_shell_only_claims_what_the_hop_carries(steerable):
    """``acme_turbo`` also declares ``native_replay``; the executor has no way to
    carry that, so the remote driver must not report it."""
    assert "native_replay" not in _remote("acme_turbo").capabilities()


# --- native history replay ---------------------------------------------------

def test_native_replay_follows_the_declaration(steerable, plain):
    assert framework_supports_native_replay("nexus_power")
    assert framework_supports_native_replay("acme_turbo")
    assert not framework_supports_native_replay("acme_plain")
    assert not framework_supports_native_replay("claude_code")
    assert not framework_supports_native_replay("ghost")


def test_framework_has_capability_is_the_one_accessor(steerable):
    assert framework_has_capability("acme_turbo", "steering")
    assert not framework_has_capability("acme_turbo", "plan")


# --- (a) the cloud gate ------------------------------------------------------

@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.cloud_policy.is_cloud_mode", lambda: True
    )


def test_cloud_gate_reads_the_meta_not_a_name_table(cloud, steerable, plain):
    """``acme_turbo`` drives the provider API with the bound card's key
    (``uses_shared_cli_login=False``), so cloud may offer it. ``acme_plain``
    leaves the field at its fail-closed default and is refused."""
    assert cloud_policy.framework_allowed_in_cloud("acme_turbo", False) is True
    assert cloud_policy.framework_allowed_in_cloud("acme_plain", False) is False
    assert "acme_turbo" in cloud_policy.cloud_allowed_frameworks()
    assert "acme_plain" not in cloud_policy.cloud_allowed_frameworks()


def test_the_builtins_keep_their_cloud_answers(cloud):
    assert cloud_policy.framework_allowed_in_cloud("nexus_power", False) is True
    assert cloud_policy.framework_allowed_in_cloud("claude_code", False) is True  # operator-provisioned card
    assert cloud_policy.framework_allowed_in_cloud("codex_cli", False) is False
    assert cloud_policy.framework_allowed_in_cloud("ghost", False) is False


def test_the_cli_login_exemption_is_operator_owned(cloud, monkeypatch):
    """A framework must never be able to attest its own cloud safety, so the
    exemption for a CLI-login framework lives in operator config; emptying it
    closes the door without a code change."""
    monkeypatch.setenv(cloud_policy.ENV_CLI_LOGIN_EXEMPT, "")
    assert cloud_policy.framework_allowed_in_cloud("claude_code", False) is False
    assert cloud_policy.framework_allowed_in_cloud("nexus_power", False) is True  # not an exemption, a property


def test_the_403_text_names_the_frameworks_that_qualify(cloud, steerable):
    detail = cloud_policy.framework_locked_detail()
    assert "NexusPower" in detail and "Claude Code" in detail and "Acme Turbo" in detail
    assert "Codex CLI" not in detail


# --- step_3's viability check ------------------------------------------------

def test_step_3_viability_check_applies_to_every_slot_driven_framework(steerable, plain):
    """The check mirrors the hard-fail conditions of a driver that resolves its
    provider from the agent slot. It used to be ``framework != "nexus_power"``,
    so a third-party framework of the same shape skipped it and bricked the turn."""
    from types import SimpleNamespace

    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        _framework_override_viable,
    )

    oauth = SimpleNamespace(model="claude-x", auth_type="oauth")
    none_ = SimpleNamespace(model="", auth_type="api_key")
    assert _framework_override_viable("acme_turbo", claude=oauth, codex=none_) is False
    assert _framework_override_viable("nexus_power", claude=oauth, codex=none_) is False
    # CLI-backed frameworks bring their own credential: unchanged pass-through.
    assert _framework_override_viable("acme_plain", claude=oauth, codex=none_) is True
    assert _framework_override_viable("claude_code", claude=oauth, codex=none_) is True
    assert _framework_override_viable("ghost", claude=oauth, codex=none_) is True
