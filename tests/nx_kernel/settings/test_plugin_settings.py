"""
@file_name: test_plugin_settings.py
@author: Bin Liang
@date: 2026-09-03
@description: PluginSettings resolves env > stored row > default, types values, masks secrets and refuses undeclared keys.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.kernel.settings import PluginSettings
from narranexus.kernel.settings.plugin_settings import MemorySettingsStore

SCHEMA = SettingsSchema(
    {
        "api_key": SettingField("string", secret=True, required=True),
        "retries": SettingField("integer", default=3),
        "enabled": SettingField("boolean", default=True),
        "region": SettingField("enum", default="eu", choices=("eu", "us")),
    }
)


def test_precedence_env_over_stored_over_default():
    store = MemorySettingsStore()
    store.save("acme.weather", "retries", 5, secret=False)
    s = PluginSettings("acme.weather", SCHEMA, store=store, environ={"NXP_ACME_WEATHER__RETRIES": "9"})
    assert s.get("retries") == 9 and s.source_of("retries") == "env"
    s2 = PluginSettings("acme.weather", SCHEMA, store=store, environ={})
    assert s2.get("retries") == 5 and s2.source_of("retries") == "stored"
    s3 = PluginSettings("acme.weather", SCHEMA, store=MemorySettingsStore(), environ={})
    assert s3.get("retries") == 3 and s3.source_of("retries") == "default"


def test_set_writes_through_store_and_types_values():
    store = MemorySettingsStore()
    s = PluginSettings("acme.weather", SCHEMA, store=store, environ={})
    s.set("enabled", "off")
    assert s.get("enabled") is False
    assert store.load("acme.weather") == {"enabled": False}
    with pytest.raises(ValueError):
        s.set("region", "apac")
    s.unset("enabled")
    assert s.get("enabled") is True


def test_required_unset_and_undeclared_keys_fail_loud():
    s = PluginSettings("acme.weather", SCHEMA, store=MemorySettingsStore(), environ={})
    with pytest.raises(KeyError, match="required"):
        s.get("api_key")
    with pytest.raises(KeyError, match="not declared"):
        s.get("nope")


def test_snapshot_masks_secrets_unless_revealed():
    s = PluginSettings("acme.weather", SCHEMA, store=MemorySettingsStore(), environ={"NXP_ACME_WEATHER__API_KEY": "sk-1"})
    assert s.snapshot()["api_key"] == "••••••"
    assert s.snapshot(reveal_secrets=True)["api_key"] == "sk-1"
    assert s.snapshot()["region"] == "eu"
