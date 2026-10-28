"""
@file_name: test_platform_kinds.py
@author: Bin Liang
@date: 2026-09-03
@description: The platform-service and content kind contracts validate their own invariants and are versioned.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.contracts import API_VERSIONS, STABILITY, Stability
from narranexus.contracts.bundle import BundleSpec
from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.skill import SkillSpec
from narranexus.contracts.table import ColumnSpec, IndexSpec, TableSpec, table_prefix_for
from narranexus.contracts.tool import ToolProvider, ToolSpec
from narranexus.contracts.ui import Theme
from narranexus.contracts.worker import WorkerSpec

NEW_KINDS = ("hook", "route", "table", "worker", "settings", "tool", "mcp_server", "bundle", "skill", "theme")


def test_every_new_kind_is_versioned_and_stable():
    for kind in NEW_KINDS:
        assert API_VERSIONS[kind] == 0
        assert STABILITY[kind] in (Stability.ALPHA, Stability.BETA, Stability.STABLE)


def test_router_spec_prefix_rules():
    RouterSpec(router=object(), prefix="/api/x/acme.weather")
    assert plugin_route_prefix("acme.weather") == "/api/x/acme.weather"
    with pytest.raises(ValueError):
        RouterSpec(router=object(), prefix="/weather")
    with pytest.raises(ValueError):
        RouterSpec(router=object(), prefix="/api/x/acme/")
    with pytest.raises(ValueError):
        RouterSpec(router=object(), prefix="/api/x/acme", auth="admin")  # type: ignore[arg-type]


def test_table_spec_invariants_and_owner_prefix():
    col = ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True)
    spec = TableSpec("ext_acme_weather__items", (col,), indexes=(IndexSpec("idx_items_id", ("id",)),))
    spec.check_owner("acme.weather")
    assert table_prefix_for("acme.weather") == "ext_acme_weather__"
    with pytest.raises(ValueError, match="prefixed"):
        TableSpec("items", (col,)).check_owner("acme.weather")
    TableSpec("events", (col,)).check_owner("builtin.chat")  # builtins keep core names
    with pytest.raises(ValueError, match="both sqlite_type and mysql_type"):
        ColumnSpec("x", "TEXT", "")
    with pytest.raises(ValueError, match="unknown columns"):
        TableSpec("ext_a_b_t", (col,), indexes=(IndexSpec("i", ("nope",)),))
    with pytest.raises(ValueError, match="duplicate"):
        TableSpec("ext_a_b_t", (col, col))


def test_worker_spec_rules():
    async def factory(ctx):  # pragma: no cover - never called
        raise AssertionError

    WorkerSpec("sync", factory)
    with pytest.raises(ValueError):
        WorkerSpec("a:b", factory)
    with pytest.raises(ValueError):
        WorkerSpec("sync", factory, host="mcp")  # type: ignore[arg-type]


def test_settings_schema_types_and_env_names():
    schema = SettingsSchema(
        {
            "api_key": SettingField("string", secret=True),
            "retries": SettingField("integer", default=3),
            "ratio": SettingField("number", default=0.5),
            "enabled": SettingField("boolean", default=True),
            "region": SettingField("enum", default="eu", choices=("eu", "us")),
        }
    )
    assert schema.env_name("acme.weather", "api_key") == "NXP_ACME_WEATHER__API_KEY"
    assert schema.fields["retries"].coerce("7") == 7
    assert schema.fields["enabled"].coerce("off") is False
    assert schema.fields["ratio"].coerce("0.25") == 0.25
    with pytest.raises(ValueError):
        schema.fields["region"].coerce("apac")
    with pytest.raises(ValueError):
        SettingField("enum")
    with pytest.raises(ValueError):
        SettingField("integer", secret=True)
    with pytest.raises(ValueError):
        SettingField("integer", default="x")
    with pytest.raises(ValueError):
        SettingsSchema({"Bad Key": SettingField("string")})


def test_tool_spec_and_provider_protocol():
    class P:
        def list_tools(self):
            return (ToolSpec("weather_now", "current weather"),)

    assert isinstance(P(), ToolProvider)
    assert P().list_tools()[0].always_visible is False
    with pytest.raises(ValueError):
        ToolSpec("bad name", "x")


def test_mcp_server_spec_transport_rules_and_config():
    stdio = McpServerSpec("local", "stdio", command="uvx", args=("weather",), env={"KEY": "v"})
    assert stdio.to_config() == {"type": "stdio", "command": "uvx", "args": ["weather"], "env": {"KEY": "v"}}
    http = McpServerSpec("remote", "streamable_http", url="https://x/mcp", headers={"A": "b"})
    assert http.to_config() == {"type": "streamable_http", "url": "https://x/mcp", "headers": {"A": "b"}}
    with pytest.raises(ValueError):
        McpServerSpec("bad", "stdio", url="https://x")
    with pytest.raises(ValueError):
        McpServerSpec("bad", "sse", command="x")
    with pytest.raises(ValueError):
        McpServerSpec("bad", "grpc", url="x")  # type: ignore[arg-type]


def test_bundle_and_skill_specs():
    BundleSpec("acme.team", Path("/x/acme.nxbundle"), "a" * 64)
    with pytest.raises(ValueError):
        BundleSpec("acme.team", Path("/x/acme.zip"), "a" * 64)
    with pytest.raises(ValueError):
        BundleSpec("acme.team", Path("/x/acme.nxbundle"), "zz")
    assert SkillSpec(Path("/x/skill")).manifest_path == Path("/x/skill/SKILL.md")
    with pytest.raises(ValueError):
        SkillSpec(Path("/x"), kind="other")  # type: ignore[arg-type]
    assert Theme("acme.dark", "Acme Dark", {"--nm-ink": "#fff"}, dark=True).dark is True
