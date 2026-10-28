"""
@file_name: test_plugin_bundles.py
@author: Bin Liang
@date: 2026-09-03
@description: content.bundles contributions appear as marketplace templates, resolve from disk, and never shadow a registry template.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from narranexus.contracts.bundle import BundleSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


def _bundle(tmp_path: Path, name="acme.team"):
    path = tmp_path / f"{name}.nxbundle"
    path.write_bytes(b"bundle-bytes")
    return path, hashlib.sha256(b"bundle-bytes").hexdigest()


def _registries(spec: BundleSpec):
    registries = Registries()
    registries.registry_for("content.bundles").register_contribution(Contribution("t", lambda: spec), owner="acme.plugin")
    return registries


@pytest.fixture
def svc(monkeypatch, tmp_path):
    from narranexus_plugins.teams import marketplace_service as mod

    monkeypatch.setenv("SKILL_MARKETPLACE_LOCAL_REGISTRY", "1")

    class _Catalog:
        def __init__(self):
            self.rows = []

        async def list_enabled(self):
            return self.rows

        async def get(self, tid):
            return next((r for r in self.rows if r.template_id == tid), None)

    catalog = _Catalog()
    service = mod.TeamMarketplaceService(db_client=object(), store=object())

    async def _catalog():
        return catalog

    monkeypatch.setattr(service, "_catalog", _catalog)
    return service, catalog


@pytest.mark.asyncio
async def test_plugin_bundle_listed_resolved_and_pinned(svc, tmp_path, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    service, _ = svc
    path, digest = _bundle(tmp_path)
    monkeypatch.setattr(pc, "_registries", lambda r=None: _registries(BundleSpec("acme.team", path, digest, "Acme Team", "d")))
    listed = await service.list_templates()
    (tpl,) = listed["templates"]
    assert tpl["template_id"] == "acme.team" and tpl["source"] == "plugin" and tpl["author"] == "acme.plugin"
    assert "_path" not in tpl and tpl["bundle_sha256"] == digest
    assert await service.expected_sha256("acme.team") == digest
    (tmp_path / "out").mkdir()  # callers hand over an existing work dir (install_preflight's mkdtemp)
    dest = await service.resolve_bundle("acme.team", tmp_path / "out")
    assert dest.read_bytes() == b"bundle-bytes"


@pytest.mark.asyncio
async def test_registry_template_wins_over_plugin_with_same_id(svc, tmp_path, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc
    from narranexus_plugins.teams.marketplace_schema import TeamTemplate

    service, catalog = svc
    path, digest = _bundle(tmp_path)
    catalog.rows.append(TeamTemplate(template_id="acme.team", name="Official", bundle_sha256="f" * 64, store_key="k"))
    monkeypatch.setattr(pc, "_registries", lambda r=None: _registries(BundleSpec("acme.team", path, digest)))
    listed = await service.list_templates()
    assert [t["name"] for t in listed["templates"]] == ["Official"]
    assert await service.expected_sha256("acme.team") == "f" * 64


@pytest.mark.asyncio
async def test_no_plugins_no_change(svc, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    service, _ = svc
    monkeypatch.setattr(pc, "_registries", lambda r=None: Registries())
    assert (await service.list_templates())["templates"] == []
