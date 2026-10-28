"""
@file_name: test_isolation_prefixes.py
@author: Bin Liang
@date: 2026-09-07
@description: The per-plugin isolation prefixes (table ext_<id>__, env NXP_<ID>__<KEY>) are injective — a sibling plugin id can never be a prefix of another's tables or settings — because plugin ids never contain '__' or end in '_'; and api[kind] uses floor..current semantics so a bump opens a deprecation window instead of breaking every plugin at once.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import API_VERSIONS, MIN_SUPPORTED_VERSIONS, ManifestError, STABILITY, plugin_id_slug
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.table import table_prefix_for
from narranexus.kernel.plugins.manifest import PLUGIN_ID_RE


# Ids the grammar accepts whose slugs are prefixes of each other in every way
# that matters: same stem, extended stem, an underscore where another id has a
# dot or a dash, a longer namespace. Two hardcoded pairs proved nothing about
# the RULE (the double-underscore terminator); this is the rule's cross product.
INJECTIVE_IDS = (
    "acme.weather",
    "acme.weather_x",
    "acme.weather.radar",
    "acme.x",
    "acme.x_y",
    "acme-corp.my-plugin",
    "acme_corp.my_plugin_x",
    "builtin.channels.lark",
    "builtin.channels.lark_v2",
)


def _distinct_slug_pairs():
    for a in INJECTIVE_IDS:
        for b in INJECTIVE_IDS:
            if a != b and plugin_id_slug(a) != plugin_id_slug(b):
                yield a, b


def test_table_prefix_of_a_sibling_id_is_never_a_prefix():
    """Table isolation IS the prefix relation: if one plugin's prefix is a
    prefix of another's table name, ``ctx.db``'s confinement check lets the
    sibling read and write those rows. ``table_prefix_for``'s own docstring
    names the exploit (``ext_acme_weather_`` vs ``acme.weather_x``), so the
    single-underscore form must be red here, not merely differently worded."""
    assert table_prefix_for("acme.weather") == "ext_acme_weather__"
    pairs = list(_distinct_slug_pairs())
    assert len(pairs) >= 60  # the cross product really ran
    for a, b in pairs:
        pa, pb = table_prefix_for(a), table_prefix_for(b)
        assert pa != pb, (a, b)
        assert not pb.startswith(pa) and not (pb + "items").startswith(pa), (a, b)


def test_settings_env_names_cannot_collide_across_plugins():
    """Same rule for the settings namespace: a colliding env name means one
    plugin's secret is readable through the other plugin's schema."""
    schema = SettingsSchema(fields={"c_d": SettingField("string"), "d": SettingField("string")})
    assert schema.env_name("a.b", "c_d") == "NXP_A_B__C_D"
    assert schema.env_name("a.b_c", "d") == "NXP_A_B_C__D"
    assert schema.env_name("a.b", "c_d") != schema.env_name("a.b_c", "d")
    names = {schema.env_name(pid, key): (pid, key) for pid in INJECTIVE_IDS for key in ("k", "k_x")}
    assert len(names) == len(INJECTIVE_IDS) * 2  # injective: no two (id, key) share an env name
    for name, (pid, _) in names.items():
        for other in INJECTIVE_IDS:
            if plugin_id_slug(other) == plugin_id_slug(pid):
                continue
            prefix = f"NXP_{plugin_id_slug(other).upper()}__"
            assert not name.startswith(prefix), (name, other)


@pytest.mark.parametrize("bad", ["acme.a__b", "acme.a_", "acme._a", "acme..b", "Acme.b", "acme"])
def test_plugin_ids_that_would_break_injectivity_are_rejected(bad):
    assert PLUGIN_ID_RE.match(bad) is None


@pytest.mark.parametrize("good", ["acme.weather", "acme.weather_x", "builtin.channels.lark", "acme-corp.my-plugin"])
def test_plain_plugin_ids_are_accepted(good):
    assert PLUGIN_ID_RE.match(good)


def test_api_version_floor_semantics(monkeypatch):
    from narranexus.kernel.plugins import manifest as m

    class _M:
        id = "acme.x"
        api = {"provider": 0}

    monkeypatch.setitem(API_VERSIONS, "provider", 2)
    monkeypatch.setitem(MIN_SUPPORTED_VERSIONS, "provider", 1)
    with pytest.raises(ManifestError, match="no longer supported"):
        m._check_api_versions(_M())
    _M.api = {"provider": 1}
    m._check_api_versions(_M())  # inside the window
    _M.api = {"provider": 2}
    m._check_api_versions(_M())
    _M.api = {"provider": 3}
    with pytest.raises(ManifestError, match="upgrade the host"):
        m._check_api_versions(_M())


def test_stability_is_declared_per_kind():
    # The set equality below is the assertion; ``isinstance(s, Stability)`` was
    # structurally impossible to fail (STABILITY is typed and duplicated
    # test_base.py) and is gone.
    assert set(STABILITY) == set(API_VERSIONS) == set(MIN_SUPPORTED_VERSIONS)
    assert all(MIN_SUPPORTED_VERSIONS[k] <= API_VERSIONS[k] for k in API_VERSIONS)


def test_is_builtin_id_is_the_one_predicate():
    from narranexus.contracts.distribution import BUILTIN_PREFIX, is_builtin_id

    assert BUILTIN_PREFIX == "builtin." and is_builtin_id("builtin.chat") and not is_builtin_id("acme.builtin")
    # nothing outside the contract spells the prefix any more
    import subprocess

    out = subprocess.run(["git", "grep", "-n", 'startswith("builtin\\.")', "--", "src", "backend", "plugins"], capture_output=True, text=True).stdout
    assert [l for l in out.splitlines() if "distribution_scaffold" not in l] == [], out
