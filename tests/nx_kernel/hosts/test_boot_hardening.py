"""
@file_name: test_boot_hardening.py
@author: Bin Liang
@date: 2026-09-07
@description: boot() write-back discipline: inspect=True never writes (no boot marker, no state); a corrupt registry.json boots builtins; an isolated plugin leaves no partial registrations; a refused table counts as a crash; the last-known-good snapshot moves only on mark_healthy; the stage-2 deadline records "slow", never a crash.
"""
from __future__ import annotations

import time
from pathlib import Path

from narranexus.contracts.settings import SettingsSchema  # noqa: F401 — the good symbol the bad plugin provides first
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.registries import Registries

from .conftest import make_plugin, register


def _record(path: Path) -> PluginRecord:
    return PluginRecord(path=str(path), installed_version="1.0.0")


def test_inspect_boot_writes_nothing(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    before = store.path.read_text()
    for _ in range(3):
        report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, inspect=True)
        assert report.user_plugin_ids == ("acme.ok",)
    assert not (plugin_home / ".booting-backend").exists()
    assert store.path.read_text() == before  # no transitions, no crash counts
    assert not store.read().safe_mode


def test_corrupt_registry_boots_builtins_only(plugin_home: Path):
    store = RegistryStore(path=plugin_home / "registry.json", lkg=plugin_home / "registry.lkg.json")
    store.path.write_text("{")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    assert report.builtins is not None and report.builtins.loaded
    assert report.user_plugin_ids == ()


# A plugin whose LAST provides entry is a missing symbol, with a valid
# registry entry, a valid hook impl and a valid service exposed BEFORE it —
# so isolation has all three kinds of partial state to withdraw.
HALF_BODY = (
    "from narranexus.contracts.settings import SettingsSchema, SettingField\n"
    "from narranexus.kernel.plugins.hooks import hookimpl\n"
    "from narranexus.kernel.plugins.registry import Contribution\n"
    "from narranexus.contracts.services import ServiceRef\n"
    "SETTINGS = Contribution('schema', lambda: SettingsSchema(fields=(SettingField('token', 'string'),)))\n"
    "HOOKS = (hookimpl('onDidStartRun')(lambda run_id: None),)\n"
    "HALF_SERVICE = ServiceRef('acme.half.thing')\n"
    "SERVICES = ((HALF_SERVICE, object()),)\n"
)
HALF_PROVIDES = {
    "backend.settings": ["nxplugins.acme_half:SETTINGS"],
    "backend.hooks": ["nxplugins.acme_half:HOOKS"],
    "backend.services": ["nxplugins.acme_half:SERVICES"],
}
HALF_API = {"settings": 0, "route": 0, "hook": 0, "services": 0, "table": 0}


def _half_service_ref():
    from narranexus.contracts.services import ServiceRef

    return ServiceRef("acme.half.thing")


def test_isolated_plugin_leaves_no_partial_registrations(plugin_home: Path):
    make_plugin(
        plugin_home, "acme.half", body=HALF_BODY,
        extra={"provides": {**HALF_PROVIDES, "backend.routes": ["nxplugins.acme_half:NOPE"]}, "api": HALF_API},
    )
    store = register(plugin_home, "acme.half", plugin_home / "acme.half")
    registries = Registries()
    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
    assert "acme.half" in report.isolated
    # All THREE things remove_owner does, not just the registry entries:
    # deleting hooks.block / services.release_owner from it used to stay green.
    assert "acme.half" not in {e.owner for e in registries.registry_for("backend.settings").entries()}
    assert registries.hooks.caller("onDidStartRun").owners() == ()
    assert registries.services.try_require(_half_service_ref()) is None


def test_a_refused_table_withdraws_everything_the_plugin_registered(plugin_home: Path):
    """A table refusal is an isolation, and isolation means NOTHING of the plugin runs.

    The refusal happens AFTER load(), so the plugin's routes, hooks and
    services are already registered; ``boot`` used to record it in
    ``report.isolated`` and leave all of them live — an owner-controlled way to
    keep a route mounted while the report says the plugin is isolated. Deleting
    the ``registries.remove_owner`` call in boot's table loop turns this red.
    """
    body = HALF_BODY + (
        "from narranexus.contracts.route import RouterSpec\n"
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "ROUTES = (Contribution('api', lambda: RouterSpec(router=object(), prefix='/api/x/acme.half')),)\n"
        "TABLES = (Contribution('items', lambda: TableSpec('ext_acme_half__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),))),)\n"
    )
    make_plugin(
        plugin_home, "acme.half", body=body,
        extra={
            "provides": {
                **HALF_PROVIDES,
                "backend.routes": ["nxplugins.acme_half:ROUTES"],
                "backend.tables": ["nxplugins.acme_half:TABLES"],
            },
            "api": HALF_API,
        },
    )
    store = register(plugin_home, "acme.half", plugin_home / "acme.half")
    registries = Registries()

    def refuse(spec, owner):
        raise ValueError("migration refused this table")

    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store, register_table=refuse)
    assert "acme.half" in report.isolated
    owners = {owner for entries in registries.snapshot().values() for owner in entries.values()}
    assert "acme.half" not in owners, registries.snapshot()
    assert registries.hooks.caller("onDidStartRun").owners() == ()
    assert registries.services.try_require(_half_service_ref()) is None


def test_refused_table_counts_as_a_crash(plugin_home: Path):
    body = (
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "TABLES = Contribution('items', lambda: TableSpec('ext_acme_tables__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),)))\n"
    )
    make_plugin(plugin_home, "acme.tables", body=body, extra={"provides": {"backend.tables": ["nxplugins.acme_tables:TABLES"]}, "api": {"table": 0}})
    store = register(plugin_home, "acme.tables", plugin_home / "acme.tables")

    def refuse(spec, owner):
        raise ValueError("migration refused this table")

    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, register_table=refuse)
    assert "acme.tables" in report.isolated
    assert store.read().plugins["acme.tables"].crash_count == 1


def test_a_clean_boot_after_a_crash_leaves_the_crashed_state(plugin_home: Path):
    """After a crash the record is "crashed"; the next boot that loads the plugin
    cleanly must advance it (→ registered → validated → enabled) and drop the stale
    error — otherwise the factory shows "crashed" forever for a working plugin."""
    body = (
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "TABLES = Contribution('items', lambda: TableSpec('ext_acme_again__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),)))\n"
    )
    make_plugin(plugin_home, "acme.again", body=body, extra={"provides": {"backend.tables": ["nxplugins.acme_again:TABLES"]}, "api": {"table": 0}})
    store = register(plugin_home, "acme.again", plugin_home / "acme.again")

    def refuse(spec, owner):
        raise ValueError("migration refused this table")

    boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, register_table=refuse)
    assert store.read().plugins["acme.again"].state == "crashed"

    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, register_table=lambda spec, owner: None)
    assert "acme.again" not in report.isolated
    record = store.read().plugins["acme.again"]
    assert record.state == "enabled" and record.last_error is None
    # The crash budget counts consecutive crashes: a recovered plugin starts again at zero,
    # otherwise one unrelated crash months later trips the two-strikes auto-disable.
    assert record.crash_count == 0


def test_last_known_good_moves_only_when_the_host_is_healthy(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    assert store.read().plugins["acme.ok"].state == "enabled"
    assert store.read_lkg() is None  # the boot wrote, but nothing proved it healthy yet
    report.mark_healthy()
    lkg = store.read_lkg()
    assert lkg is not None and lkg.plugins["acme.ok"].state == "enabled"
    assert not (plugin_home / ".booting-backend").exists()


def test_stage2_deadline_records_slow_not_crashed(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, stage2_deadline_s=-1.0)
    assert report.user_plugin_ids == () and "acme.ok" in report.slow and report.isolated == {}
    rec = store.read().plugins["acme.ok"]
    assert rec.crash_count == 0 and rec.state == "slow" and rec.enabled


def test_a_plugin_that_blocks_on_import_is_recorded_slow_inside_the_deadline(plugin_home: Path):
    """The stage-2 deadline bounds the IMPORTS load() performs, not only the
    preparation loop: a plugin whose module hangs at import is left unloaded
    and recorded slow, and boot() returns within the window (deleting the
    bounded importer makes this hang for 5 s and fail)."""
    make_plugin(plugin_home, "acme.ok", body="import time\ntime.sleep(5)\nX = 1\n",
                extra={"provides": {"model.clients": ["nxplugins.acme_ok:X"]}, "api": {"llm_client": 0}})
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    started = time.perf_counter()
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, stage2_deadline_s=1.0)
    assert time.perf_counter() - started < 3.0
    assert "acme.ok" in report.slow and "acme.ok" not in report.isolated and report.user_plugin_ids == ()
    rec = store.read().plugins["acme.ok"]
    assert rec.crash_count == 0 and rec.state == "slow"


def test_two_plugins_with_one_slug_cannot_both_hold_tables(plugin_home: Path):
    """``acme.a-b`` and ``acme.a_b`` share the ``ext_acme_a_b__`` prefix (and the
    ``nxplugins.acme_a_b`` package): the second one is isolated at prepare time
    instead of reading and writing the first one's rows (the installer refuses
    such an id up front; this is the boot's own guard for a hand-edited registry)."""
    body = (
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "TABLES = (Contribution('items', lambda: TableSpec('ext_acme_a_b__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),))),)\n"
    )
    store = None
    for pid in ("acme.a-b", "acme.a_b"):
        make_plugin(plugin_home, pid, body=body, extra={"provides": {"backend.tables": [f"nxplugins.{pid.replace('.', '_').replace('-', '_')}:TABLES"]}, "api": {"table": 0}})
        store = register(plugin_home, pid, plugin_home / pid) if store is None else store
        if pid == "acme.a_b":
            from narranexus.kernel.plugins.lifecycle import PluginRecord
            store.register(pid, PluginRecord(path=str(plugin_home / pid), installed_version="1.0.0"))
    registered: list[str] = []
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, register_table=lambda spec, owner: registered.append(owner))
    assert registered == ["acme.a-b"]
    assert "acme.a_b" in report.isolated and "already installed" in report.isolated["acme.a_b"]
