"""
@file_name: test_channel_import_has_no_side_effects.py
@author: Bin Liang
@date: 2026-09-07
@description: Importing a channel plugin package must not write any process-global table, and READING the channel views must not either.

Two shapes of import-time global write were live in this tree until 2026-09-07,
and both produced behaviour that depended on which modules a process happened to
have imported:

1. ``MessageSourceRegistry.register(...)`` at module scope in six channel
   modules, ``builtin.job`` and ``platform/message_bus`` — each wrapped in
   ``except ValueError: pass``, so two plugins claiming one source name was
   "whoever imported first wins" instead of a conflict. A channel excluded from
   a distribution still registered its handler if anything imported it; and in a
   process where nothing had, a delivered Lark reply resolved the DEFAULT
   handler and was recorded as NO-REPLY, silently.
2. ``WorkingSource.register(...)`` from three places, one of them a READ:
   ``_ChannelSpecs._build`` / ``TriggerMapView._build`` registered enum members
   on a cache miss, so whether ``WorkingSource("lark")`` resolved depended on
   whether anyone had consulted ``SUPPORTED_CHANNELS`` yet — from an HTTP
   request path.

These tests are the falsifiable form of "registration happens only at boot".
Reverting either fix turns them red.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from narranexus.contracts.channel import ChannelDescriptor
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution

CHANNEL_PACKAGES = [
    "narranexus_plugins.lark_module",
    "narranexus_plugins.slack_module",
    "narranexus_plugins.telegram_module",
    "narranexus_plugins.wechat_module",
    "narranexus_plugins.discord_module",
    "narranexus_plugins.narramessenger_module",
]


def _run(body: str) -> str:
    """Run ``body`` in a FRESH interpreter — the only way to observe import-time
    side effects, since this session's conftest has already booted the builtins."""
    env = dict(os.environ)
    # This session's sys.path carries the plugin ``src`` roots (pytest ``pythonpath``);
    # the child needs them to import a plugin package at all.
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("package", CHANNEL_PACKAGES)
def test_importing_a_channel_package_registers_no_message_source(package):
    """No boot, no handlers — whatever gets imported."""
    out = _run(f"""
        import {package}  # noqa: F401
        from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
        print(sorted(MessageSourceRegistry.handlers()))
    """)
    assert out == "[]"


def test_importing_the_platform_message_bus_registers_no_message_source():
    """``platform/message_bus/__init__`` used to register the bus handler, so
    importing ``agent_framework`` alone left one entry in the table while every
    kernel slot was empty."""
    out = _run("""
        import narranexus.platform.message_bus  # noqa: F401
        from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
        print(sorted(MessageSourceRegistry.handlers()))
    """)
    assert out == "[]"


def test_importing_the_job_plugin_registers_no_message_source():
    out = _run("""
        import narranexus_plugins.job_module  # noqa: F401
        from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
        print(sorted(MessageSourceRegistry.handlers()))
    """)
    assert out == "[]"


def test_reading_supported_channels_does_not_mutate_the_working_source_enum():
    """The read path is pure.

    ``SUPPORTED_CHANNELS`` membership is an HTTP-request-path operation; building
    its view used to register a ``WorkingSource`` (and its ``TriggerType`` twin)
    per descriptor. A fresh process that reads the channel map must come out with
    the same enum it went in with."""
    out = _run("""
        from narranexus.contracts.channel import ChannelDescriptor
        from narranexus.kernel.plugins.registries import Registries
        from narranexus.kernel.plugins.registry import Contribution
        from narranexus.platform.module_system.data_access.channel_store import _ChannelSpecs
        from narranexus.platform.schema.hook_schema import WorkingSource

        before = set(WorkingSource.channel_values())
        regs = Registries()
        d = ChannelDescriptor(
            name="acme", display_name="Acme", trigger_ref="pkg.mod:T",
            credential_manager_ref="pkg.mod:Mgr",
        )
        regs.registry_for("ingress.channels").register_contribution(
            Contribution("acme", lambda: d), owner="acme.chat"
        )
        specs = _ChannelSpecs(regs)
        assert "acme" in specs, "the descriptor must still be readable"
        print(sorted(set(WorkingSource.channel_values()) - before))
    """)
    assert out == "[]"


def test_the_working_source_is_registered_when_the_channel_contributes():
    """…and it IS registered where the channel enters the registry.

    ``contributions_from`` is what a channel plugin's ``contribution.py`` calls,
    and the manifest loader imports that at boot for installed, enabled plugins
    only — so the enum member exists after boot and only for channels this
    deployment actually ships. Registering the ``TriggerType`` twin in the same
    call is part of the contract (``narrative/models.py``)."""
    out = _run("""
        from narranexus.contracts.channel import ChannelDescriptor
        from narranexus.platform.channel.contributions import contributions_from
        from narranexus.platform.narrative.models import TriggerType
        from narranexus.platform.schema.hook_schema import WorkingSource

        d = ChannelDescriptor(name="acme", display_name="Acme", trigger_ref="pkg.mod:T")
        contributions_from(d, "acme.chat")
        print(WorkingSource("acme").value, TriggerType("acme").value)
    """)
    assert out == "acme acme"


def test_a_credentials_only_channel_gets_no_working_source():
    """``has_inbound`` is the gate: a channel that cannot receive never labels a
    turn, so inventing an enum member for it would put a value in the vocabulary
    that no event can ever carry."""
    out = _run("""
        from narranexus.contracts.channel import ChannelDescriptor
        from narranexus.platform.channel.contributions import contributions_from
        from narranexus.platform.schema.hook_schema import WorkingSource

        d = ChannelDescriptor(name="acme", display_name="Acme", transport="none")
        contributions_from(d, "acme.chat")
        print("acme" in WorkingSource.channel_values())
    """)
    assert out == "False"


def test_a_disabled_channels_handler_is_absent_from_the_view():
    """``remove_owner`` is what boot does to a builtin disabled in registry.json;
    the handler must go with it. The old class-level dict kept it forever."""
    from narranexus.platform.channel.message_source_handler import MessageSourceView

    regs = Registries()
    descriptor = ChannelDescriptor(
        name="lark", display_name="Lark", trigger_ref="pkg.mod:T",
        reply_tools=("lark_cli",), row_prefix_template="[Lark]",
    )
    regs.registry_for("ingress.channels").register_contribution(
        Contribution("lark", lambda: descriptor), owner="builtin.channels.lark"
    )
    view = MessageSourceView(regs)
    assert "lark" in view.handlers()

    regs.registry_for("ingress.channels").remove_owner("builtin.channels.lark")
    assert "lark" not in view.handlers()
    assert view.get("lark").name == "default"
