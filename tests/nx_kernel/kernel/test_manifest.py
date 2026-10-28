"""
@file_name: test_manifest.py
@author: Bin Liang
@date: 2026-09-03
@description: Manifest validation — shape, slot-awareness, arity, builtin prefix, api/minAppVersion gates.
"""
from __future__ import annotations

import json

import pytest

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.manifest import (
    Manifest,
    derive_activation_events,
    load_manifest,
    parse_manifest,
)
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.slots import SlotTree


def _tree() -> SlotTree:
    # The kernel tree plus every builtin's declarations (ui.pages / ui.panels
    # come from builtin.ui, the stage slots from builtin.turn).
    return slot_tree_with_builtins()


def _base(**overrides) -> dict:
    data = {
        "id": "acme.weather",
        "version": "1.2.0",
        "displayName": "Weather",
        "provides": {"model.providers": ["backend.provider:WeatherDriver"]},
    }
    data.update(overrides)
    return data


def test_minimal_manifest_parses_and_exposes_helpers():
    m = parse_manifest(_base(), tree=_tree())
    assert m.id == "acme.weather" and m.display_name == "Weather"
    assert not m.is_builtin and str(m.semantic_version) == "1.2.0"
    assert m.provided_slots() == ("model.providers",)
    assert m.quality == "bronze" and m.install.deps == "eager"
    assert derive_activation_events(m) == ("onStartup",)


def test_full_manifest_round_trip_including_declares_and_permissions():
    data = _base(
        publisher={"name": "acme", "url": "https://github.com/acme"},
        license="MIT",
        minAppVersion="1.19.0",
        api={"provider": 0},
        dependencies={"builtin.providers": ">=1.0"},
        afterDependencies=["builtin.ui"],
        hosts=["backend", "frontend"],
        backend={"package": "backend", "pip": ["httpx>=0.27"], "activate": True},
        frontend={"entry": "frontend/dist/plugin.js", "locales": "frontend/locales"},
        provides={
            "model.providers": ["backend.provider:WeatherDriver"],
            "ui.pages": ["frontend:WeatherPage"],
            "acme.weather.sources": ["backend.sources:Default"],
        },
        declares={"acme.weather.sources": {"arity": "many", "contract": "backend.contracts:WeatherSource"}},
        permissions={"network": ["api.weather.com"], "subprocess": False, "env": ["WEATHER_KEY"]},
        install={"deps": "on_demand"},
        size={"backend_deps_mb": 12, "frontend_kb": 340},
        quality="silver",
    )
    m = parse_manifest(data, tree=_tree(), host_version="1.19.5")
    assert m.hosts == ("backend", "frontend") and m.backend.activate is True
    assert m.effective_hosts() == ("backend", "frontend")
    assert parse_manifest(_base(), tree=_tree()).effective_hosts() == ("backend", "mcp", "workers", "frontend")
    assert m.permissions.network == ("api.weather.com",)
    (declared,) = m.declared_slots()
    assert (declared.path, declared.arity, declared.owner) == ("acme.weather.sources", "many", "acme.weather")
    assert declared.stability.value == "alpha"
    assert derive_activation_events(m) == ("onStartup", "onPage:acme.weather")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "weather"}, "id must be"),
        ({"id": "Acme.Weather"}, "id must be"),
        ({"version": "v1"}, "version"),
        ({"minAppVersion": "one"}, "minAppVersion"),
        ({"dependencies": {"builtin.providers": ">= banana"}}, "dependencies"),
        ({"quality": "platinum"}, "quality"),
        ({"unknownKey": 1}, "unknownKey"),
        ({"provides": {"model.providers": "backend.provider:WeatherDriver"}}, "many-arity slot; give a list"),
        ({"provides": {"turn.pipeline.act.framework": ["backend:Loop"]}}, "one-arity slot; give a single symbol"),
        ({"provides": {"model.providers": ["not a symbol"]}}, "module.path:Symbol"),
        ({"provides": {"nope.slot": ["backend:X"]}}, "not a declared slot"),
        ({"provides": {"kernel.auth": "backend:Sso"}}, "distribution-only"),
        ({"api": {"provider": 99}}, "upgrade the host"),
        ({"api": {"unicorn": 0}}, "not a contract kind"),
        ({"redeclares": ["turn.pipeline.act"]}, "descendant of a slot this plugin provides"),
        ({"declares": {"Bad Path": {"arity": "one", "contract": "x:Y"}}}, "declares"),
        ({"declares": {"other.plugin.slot": {"arity": "one", "contract": "x:Y"}}}, "own namespace"),
        ({"declares": {"acme.weather": {"arity": "one", "contract": "x:Y"}}}, "own namespace"),
        ({"declares": {"acme.x": {"arity": "one", "contract": "nope"}}}, "module.path:Symbol"),
    ],
)
def test_invalid_manifests_fail_loud_and_name_the_field(overrides, message):
    with pytest.raises(ManifestError, match=message):
        parse_manifest(_base(**overrides), tree=_tree())


def test_builtin_prefix_is_reserved_unless_allowed():
    # api must version the kind of every slot it provides into (model.providers
    # -> "provider"); for a builtin that is a hard error, so the fixture states it.
    data = _base(id="builtin.weather", api={"provider": 0})
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(data, tree=_tree())
    assert parse_manifest(data, tree=_tree(), allow_builtin=True).is_builtin


def test_distribution_only_manifest_may_provide_a_distribution_only_slot():
    data = _base(provides={"kernel.auth": "backend.sso:Provider"}, distributionOnly=True)
    m = parse_manifest(data, tree=_tree())
    assert m.distribution_only and m.provides["kernel.auth"] == "backend.sso:Provider"


def test_min_app_version_gate():
    data = _base(minAppVersion="2.0.0")
    with pytest.raises(ManifestError, match="minAppVersion 2.0.0 exceeds host 1.19.0"):
        parse_manifest(data, tree=_tree(), host_version="1.19.0")
    parse_manifest(data, tree=_tree(), host_version="2.0.0")


def test_provider_of_a_composite_may_declare_its_children():
    data = _base(
        id="builtin.turn",
        provides={"turn.pipeline": "backend.turn:PIPELINE"},
        declares={"turn.pipeline.recall": {"arity": "one", "contract": "backend.contracts:RecallStrategy", "default": "builtin.turn"}},
    )
    m = parse_manifest(data, tree=_tree(), allow_builtin=True)
    (slot,) = m.declared_slots()
    assert (slot.path, slot.owner, slot.default) == ("turn.pipeline.recall", "builtin.turn", "builtin.turn")


def test_redeclares_must_be_under_a_provided_composite_slot():
    tree = _tree()
    data = _base(provides={"turn.pipeline": "backend.turn:Pipeline"}, redeclares=["turn.pipeline.act.framework"])
    m = parse_manifest(data, tree=tree)
    assert m.redeclares == ("turn.pipeline.act.framework",)
    with pytest.raises(ManifestError, match="not a known slot"):
        parse_manifest(_base(provides={"turn.pipeline": "backend.turn:Pipeline"}, redeclares=["turn.pipeline.act.nope"]), tree=tree)


def test_load_manifest_from_disk_reports_unreadable_or_bad_json(tmp_path):
    path = tmp_path / "narranexus-plugin.json"
    with pytest.raises(ManifestError, match="cannot read manifest"):
        load_manifest(path, tree=_tree())
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="cannot read manifest"):
        load_manifest(path, tree=_tree())
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert load_manifest(path, tree=_tree()).id == "acme.weather"


def test_manifest_is_immutable():
    from pydantic import ValidationError

    m = parse_manifest(_base(), tree=_tree())
    with pytest.raises(ValidationError, match="frozen"):
        m.version = "9.9.9"  # type: ignore[misc]
    assert isinstance(m, Manifest)


def test_declared_slot_carries_kind_and_case_insensitivity_and_rejects_unknown_kinds():
    data = _base(declares={"acme.weather.sources": {"arity": "many", "contract": "x:Y", "kind": "provider", "caseInsensitive": True}})
    (slot,) = parse_manifest(data, tree=_tree()).declared_slots()
    assert (slot.kind, slot.case_insensitive, slot.api_version) == ("provider", True, 0)
    with pytest.raises(ManifestError, match="contract kind"):
        parse_manifest(_base(declares={"acme.weather.sources": {"arity": "many", "contract": "x:Y", "kind": "nope"}}), tree=_tree())


def test_default_provider_of_a_kernel_root_may_declare_its_children():
    # ``prompt``'s declared default is builtin.prompts, so prompt.* is its to declare — the
    # ownership rule of the tree (the provider of a composite owns its children); a stranger may not.
    data = _base(id="builtin.prompts", provides={}, api={"prompt": 0}, declares={"prompt.sections": {"arity": "many", "contract": "x:Section", "kind": "prompt"}})
    (slot,) = parse_manifest(data, tree=_tree(), allow_builtin=True).declared_slots()
    assert (slot.path, slot.owner) == ("prompt.sections", "builtin.prompts")
    with pytest.raises(ManifestError, match="own namespace"):
        parse_manifest(_base(provides={}, declares={"prompt.sections": {"arity": "many", "contract": "x:Section"}}), tree=_tree())


def test_a_provider_of_a_many_arity_slot_may_not_declare_its_children():
    # Every provider of model.providers could otherwise claim its children and the
    # second claimant would fail at boot with a RegistryConflict it never caused.
    data = _base(declares={"model.providers.acme": {"arity": "many", "contract": "x:Y"}})
    with pytest.raises(ManifestError, match="ONE-arity"):
        parse_manifest(data, tree=_tree())
