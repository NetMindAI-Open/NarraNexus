"""
@file_name: test_channel_descriptors.py
@author: Bin Liang
@date: 2026-09-04
@description: Every builtin channel is one ChannelDescriptor in ingress.channels; the trigger map, the data-access CHANNELS table, WorkingSource and manyfold's provider map all read from it, and disabling a channel builtin removes it from each.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.module_system.channel_trigger_map import TriggerMapView
from narranexus.platform.module_system.slots import CHANNELS_SLOT
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.module_system.data_access.channel_store import _ChannelSpecs
from narranexus.platform.schema.hook_schema import WorkingSource

IM = {"lark", "slack", "telegram", "wechat", "narramessenger", "discord"}


def _regs() -> Registries:
    regs = Registries()
    load_builtins(regs, "backend")
    return regs


def _descriptors(regs) -> dict[str, ChannelDescriptor]:
    return {e.name: e.factory() for e in regs.registry_for(CHANNELS_SLOT).entries()}


def test_descriptors_agree_with_triggers_working_sources_and_the_seam_table():
    regs = _regs()
    desc = _descriptors(regs)
    assert set(desc) == IM | {"home_assistant"}
    inbound = {n for n, d in desc.items() if d.has_inbound}
    assert inbound == IM
    # the trigger map and the descriptor name the same classes
    trigger_classes = {e.name: e.meta["class_ref"] for e in regs.registry_for("ingress.triggers").entries() if e.meta.get("host") == "channels"}
    for name in IM:
        assert desc[name].trigger_ref == trigger_classes[name]
        assert desc[name].resolve(desc[name].trigger_ref).channel_name == name
        assert WorkingSource(name).is_from_human() and WorkingSource.is_channel(name)
    assert not desc["home_assistant"].has_inbound and "home_assistant" not in WorkingSource
    seam = _ChannelSpecs(regs)
    assert set(seam) == IM | {"home_assistant"}
    assert seam["lark"].read_method == "get_credential" and seam["lark"].unbind_service.endswith("_lark_service")
    assert seam["wechat"].bind is None and seam["narramessenger"].bind.takes == "db" and seam["telegram"].bind.has_test


def test_credential_schemas_split_secrets_from_identity():
    for d in _descriptors(_regs()).values():
        schema = d.credential_schema
        assert set(schema.secret_names()).isdisjoint(schema.public_names())
        if d.has_inbound:
            assert schema.secret_names(), d.name  # every IM channel has a secret
            assert schema.external_id_field in {f.name for f in schema.fields}


def test_descriptor_validation():
    with pytest.raises(ValueError):
        ChannelDescriptor(name="Bad Name", display_name="x")
    with pytest.raises(ValueError):
        ChannelDescriptor(name="ok", display_name="x", trigger_ref="no_colon")
    f = CredentialField("token", "secret", public=True)
    assert f.public is False  # secrets are never public
    with pytest.raises(ValueError):
        CredentialField("not an identifier")
    schema = CredentialSchema(fields=(f, CredentialField("bot_id")))
    assert schema.secret_names() == ("token",) and schema.public_names() == ("bot_id",)


@pytest.mark.parametrize("channel", sorted(IM))
def test_disabling_a_channel_builtin_removes_it_everywhere(channel, tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    store.update(lambda reg: reg.builtin_overrides.__setitem__(f"builtin.channels.{channel}", {"enabled": False}))
    regs = _regs()
    boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    assert channel not in _descriptors(regs)
    assert channel not in _ChannelSpecs(regs)
    assert channel not in TriggerMapView(regs)
    assert len(_ChannelSpecs(regs)) == 6  # five IM channels + home_assistant remain


def test_manyfold_provider_map_reads_the_registry(monkeypatch):
    from backend.routes.manyfold import sync

    assert sync._provider_working_source("lark") is WorkingSource.LARK
    assert sync._provider_working_source("Discord ") is WorkingSource.DISCORD
    assert sync._provider_working_source("home_assistant") is None  # credentials only
    assert sync._provider_working_source("nope") is None
