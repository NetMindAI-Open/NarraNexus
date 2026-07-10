"""
@file_name: test_trigger_startup_alignment.py
@author: Bin Liang
@date: 2026-07-02 (rewritten 2026-07-08 for the consolidated supervisor)
@description: Guard test — the ONE channel-trigger supervisor must be wired into
              every startup path, and every channel trigger class must be
              registered so the supervisor actually launches it (CLAUDE.md #7).

Historical context: IM channels used to ship one ``run_*_trigger.py`` entrypoint
each, and a startup path forgetting one meant a bound channel silently received
nothing (bit Slack/Telegram, NarraMessenger, WeChat in turn). Those per-channel
entrypoints were consolidated into a single supervisor
(``module/run_channel_triggers.py``) that runs every ``ChannelTriggerBase``
subclass listed in ``CHANNEL_TRIGGER_MAP``.

The failure class therefore MOVED, and this guard moved with it:

  1. Every channel trigger class (subclass of ``ChannelTriggerBase`` found on
     disk) MUST be in ``CHANNEL_TRIGGER_MAP`` — else the supervisor never
     starts it, reproducing the old silent-drop outage one layer up.
  2. The single supervisor entrypoint MUST appear in every startup path:
       - ``run.sh``                        (bash local / container)
       - ``scripts/dev-local.sh``          (tmux dev mode)
       - ``scripts/.dev-local-safe.sh``    (tmux safe variant)
       - ``scripts/deploy-cloud.sh``       (systemd cloud VM)
       - ``tauri/src-tauri/src/state.rs``  (desktop dmg — BOTH factories)
  3. ``run.sh`` stop path MUST pkill the supervisor, or restarts leak it.

The cloud compose file (``NarraNexus-deploy`` repo) is guarded by that repo's
own check script.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = REPO_ROOT / "src" / "xyz_agent_context" / "module"

SUPERVISOR_ENTRYPOINT = "xyz_agent_context.module.run_channel_triggers"

STARTUP_FILES = {
    "run.sh": REPO_ROOT / "run.sh",
    "scripts/dev-local.sh": REPO_ROOT / "scripts" / "dev-local.sh",
    "scripts/.dev-local-safe.sh": REPO_ROOT / "scripts" / ".dev-local-safe.sh",
    "scripts/deploy-cloud.sh": REPO_ROOT / "scripts" / "deploy-cloud.sh",
    "tauri/src-tauri/src/state.rs": REPO_ROOT / "tauri" / "src-tauri" / "src" / "state.rs",
}

# ``class Foo(ChannelTriggerBase):`` — the on-disk source of truth for "this is
# a channel trigger". Excludes job_trigger (not a ChannelTriggerBase subclass).
_SUBCLASS_RE = re.compile(r"class\s+(\w+)\s*\(\s*ChannelTriggerBase\s*\)")


def discover_channel_trigger_classes() -> set[str]:
    """Class names of every ``ChannelTriggerBase`` subclass under module/*."""
    found: set[str] = set()
    for path in MODULE_DIR.glob("*_module/*_trigger.py"):
        text = path.read_text(encoding="utf-8")
        found.update(_SUBCLASS_RE.findall(text))
    return found


def test_discovery_finds_the_known_channel_triggers():
    """Sanity: the filesystem scan finds the known channels (guards the glob)."""
    classes = discover_channel_trigger_classes()
    assert "LarkTrigger" in classes
    assert "WeChatTrigger" in classes
    assert len(classes) >= 6


def test_every_channel_trigger_is_registered_in_map():
    """A ChannelTriggerBase subclass that isn't registered would never be
    launched by the supervisor — the modern form of the old silent-drop outage.

    Checks the registration INTENT (REGISTERED_TRIGGER_CLASS_NAMES), NOT the
    runtime CHANNEL_TRIGGER_MAP: the map defensively drops channels whose
    optional dependency is missing in this env (e.g. matrix-nio), and a missing
    dep must not read as "forgot to register."
    """
    from xyz_agent_context.module.channel_trigger_map import (
        REGISTERED_TRIGGER_CLASS_NAMES,
    )

    on_disk = discover_channel_trigger_classes()
    missing = on_disk - set(REGISTERED_TRIGGER_CLASS_NAMES)
    assert not missing, (
        "ChannelTriggerBase subclasses not registered in _TRIGGER_SPECS "
        "(the supervisor will never start them):\n" + "\n".join(sorted(missing))
    )


def test_map_values_are_channel_trigger_subclasses():
    from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
    from xyz_agent_context.module.channel_trigger_map import CHANNEL_TRIGGER_MAP

    for name, cls in CHANNEL_TRIGGER_MAP.items():
        assert issubclass(cls, ChannelTriggerBase), f"{name} -> {cls} not a trigger"
        # Map key must equal the class's own declared channel_name.
        assert cls.channel_name == name


def test_supervisor_wired_into_every_startup_path():
    missing = [
        label
        for label, path in STARTUP_FILES.items()
        if SUPERVISOR_ENTRYPOINT not in path.read_text(encoding="utf-8")
    ]
    assert not missing, (
        "Consolidated channel-trigger supervisor not launched by every startup "
        "path (CLAUDE.md #7):\n" + "\n".join(missing)
    )


def test_state_rs_wires_supervisor_in_both_factories():
    """The desktop app has two ServiceDef factories; each must launch the
    supervisor."""
    text = STARTUP_FILES["tauri/src-tauri/src/state.rs"].read_text(encoding="utf-8")
    bundled_at = text.find("fn bundled_services")
    dev_at = text.find("fn dev_services")
    assert 0 <= bundled_at < dev_at, "state.rs factory layout changed — update this test"
    sections = {
        "bundled_services": text[bundled_at:dev_at],
        "dev_services": text[dev_at:],
    }
    missing = [
        factory
        for factory, body in sections.items()
        if SUPERVISOR_ENTRYPOINT not in body
    ]
    assert not missing, "state.rs factories missing the supervisor:\n" + "\n".join(missing)


def test_run_sh_kills_supervisor_on_stop():
    """run.sh stop must pkill the supervisor, or restarts leak a stale poller."""
    text = STARTUP_FILES["run.sh"].read_text(encoding="utf-8")
    assert re.search(r'pkill -f "?run_channel_triggers"?', text), (
        "run.sh stop path missing pkill for run_channel_triggers"
    )
