"""
@file_name: test_corrupt_archive_export.py
@author: NarraNexus
@date: 2026-08-18
@description: A corrupt archive must degrade to a warning, not a 500.

Found on the dev env while verifying the SEC-07 fix: uploading bytes that are
not a zip was accepted (200), and the failure surfaced one endpoint away —
`POST /api/bundle/export` raised `BadZipFile` out of `scan_zip_for_sensitive`
and became a 500 whose message ("Failed to build the export") names neither the
skill nor the archive. Same shape as #113.

The upload gate (see `tests/backend/test_bundle_archive_path_safety.py`) stops
new bad archives. This file covers the other half: rows that are ALREADY bad —
written before the gate existed, truncated by a half-finished write, or
corrupted on disk — must not be able to take a whole export down with them.
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


@pytest.fixture
def archives_root(tmp_path, monkeypatch):
    from narranexus.platform.bundle import skill_backup

    root = tmp_path / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", root)
    return root


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from narranexus.platform.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from narranexus.platform.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")
    from narranexus.platform.utils.db import db_factory

    db_factory._clients_by_loop.clear()
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id, agent_name, user_id="test_user"):
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert(
            "users",
            {
                "user_id": user_id,
                "user_type": "local",
                "role": "user",
                "display_name": "Test User",
            },
        )
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "created_by": user_id,
            "agent_description": "d",
            "agent_type": "default",
        },
    )


def _seed_skill_on_disk(ws_root: Path, agent_id: str, user_id: str, skill_dir: str):
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    d = agent_workspace_path(agent_id, user_id, base=str(ws_root)) / "skills" / skill_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {skill_dir}\n---\n\nbody\n", encoding="utf-8")
    return d


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("manifest.json"))


async def test_corrupt_archive_is_skipped_with_a_warning_not_a_500(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """One unreadable archive → that skill is skipped, the export still builds,
    and the warning names the skill so the user can act on it."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_corrupt01", "test_user"
    await _seed_agent(db_client, aid, "CorruptAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "brokenskill")

    bad = prepare_archive_target(uid, "brokenskill")
    bad.write_bytes(b"PK\x03\x04definitely not a zip")

    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="brokenskill", source_type="zip",
        sha256="deadbeef", archive_path=str(bad),
    )

    bundle = tmp_path / "corrupt.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "brokenskill",
                 "skill_dir": "brokenskill", "install_method": "zip"}
            ],
        ),
        bundle,
    )

    assert bundle.exists(), "the export must still be produced"
    warnings = " ".join(result.get("warnings", []))
    assert "brokenskill" in warnings, f"warning must name the skill: {warnings!r}"
    assert "zip" in warnings.lower()
    # Dropped from the manifest entirely — the same shape as the "zip not found"
    # path right above it in the builder, so the importer sees one convention.
    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "brokenskill"]
    assert entries == [], f"corrupt archive must not be packed, got {entries}"
    with zipfile.ZipFile(bundle) as z:
        assert not [n for n in z.namelist() if "brokenskill" in n], "bad bytes shipped anyway"


async def test_one_corrupt_archive_does_not_sink_the_healthy_ones(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The reason this matters: before the fix, a single bad row failed the
    WHOLE export, so one user's stale archive blocked everything else too."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_corrupt02", "test_user"
    await _seed_agent(db_client, aid, "MixedAgent", uid)
    for name in ("brokenskill", "goodskill"):
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, name)

    repo = SkillArchiveRepository(db_client)

    bad = prepare_archive_target(uid, "brokenskill")
    bad.write_bytes(b"not a zip at all")
    await repo.upsert(user_id=uid, skill_name="brokenskill", source_type="zip",
                      sha256="deadbeef", archive_path=str(bad))

    good = prepare_archive_target(uid, "goodskill")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("goodskill/SKILL.md", "---\nname: goodskill\n---\nHEALTHY_CANARY\n")
    await repo.upsert(user_id=uid, skill_name="goodskill", source_type="zip",
                      sha256="cafebabe", archive_path=str(good))

    bundle = tmp_path / "mixed.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": n, "skill_dir": n, "install_method": "zip"}
                for n in ("brokenskill", "goodskill")
            ],
        ),
        bundle,
    )

    skills = {s.get("name"): s for s in _manifest(bundle).get("skills", [])}
    assert "goodskill" in skills, f"healthy skill was dropped too: {sorted(skills)}"
    assert skills["goodskill"].get("archive_ref"), "healthy skill lost its archive"
    assert "brokenskill" not in skills, "corrupt skill should be dropped, not packed"
    with zipfile.ZipFile(bundle) as z:
        blob = b"".join(z.read(n) for n in z.namelist() if not n.endswith("/"))
    assert b"HEALTHY_CANARY" in blob


async def test_tarball_archive_gets_a_message_that_points_at_the_real_mistake(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """A github-installed skill's archive is a real `.tar.gz`. Exporting it with
    `install_method="zip"` must not tell the user their archive is broken — it
    isn't; the method is wrong. `archive_rows_by_skill` keeps `source_type` so
    the warning can say so."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_tarball01", "test_user"
    await _seed_agent(db_client, aid, "TarballAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "githubskill")

    tgz = prepare_archive_target(uid, "githubskill", suffix=".tar.gz")
    tgz.write_bytes(b"\x1f\x8b\x08\x00fake but genuinely a tarball, not a zip")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="githubskill", source_type="github",
        source_url="https://github.com/o/r", sha256="abc123", archive_path=str(tgz),
    )

    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "githubskill",
                 "skill_dir": "githubskill", "install_method": "zip"}
            ],
        ),
        tmp_path / "tarball.nxbundle",
    )
    warnings = " ".join(result.get("warnings", []))
    assert "githubskill" in warnings
    assert "github" in warnings and "url" in warnings, (
        f"warning should point at the method/source mismatch, got: {warnings!r}"
    )
    assert "not a readable zip" not in warnings, "blames the file for a method error"


async def test_failure_midway_through_copy_leaves_no_partial_archive(
    db_client, tmp_workspace_root, tmp_path, archives_root, monkeypatch
):
    """The degrade path covers copy2/file_sha256, not just the scan — and it
    cleans up. A half-written `{skill_dir}.zip` would otherwise be picked up by
    the `tgt_zip.exists()` branch for the next entry with that dir name and
    silently push it onto the `__{agent_id}` filename."""
    from narranexus.platform.bundle import builder as builder_mod
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_copyfail01", "test_user"
    await _seed_agent(db_client, aid, "CopyFailAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "flakyskill")

    good = prepare_archive_target(uid, "flakyskill")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("flakyskill/SKILL.md", "---\nname: flakyskill\n---\nok\n")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="flakyskill", source_type="zip",
        sha256="cafebabe", archive_path=str(good),
    )

    def _half_copy(src, dst, *a, **kw):
        Path(dst).write_bytes(b"PARTIAL")      # leave a stub behind, then fail
        raise OSError("disk went away mid-copy")

    # builder_mod.shutil IS the stdlib module object, so this patches copy2
    # process-wide for the duration; monkeypatch restores it at teardown.
    monkeypatch.setattr(builder_mod.shutil, "copy2", _half_copy)

    bundle = tmp_path / "copyfail.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "flakyskill",
                 "skill_dir": "flakyskill", "install_method": "zip"}
            ],
        ),
        bundle,
    )
    assert bundle.exists(), "a copy failure must not fail the whole export"
    assert "flakyskill" in " ".join(result.get("warnings", []))
    with zipfile.ZipFile(bundle) as z:
        leftovers = [n for n in z.namelist() if "flakyskill" in n and n.endswith(".zip")]
    assert leftovers == [], f"partial archive shipped: {leftovers}"


async def test_full_copy_failure_degrades_like_the_zip_branch(
    db_client, tmp_workspace_root, tmp_path, archives_root, monkeypatch
):
    """`full_copy` had no degrade path at all — this PR's "one bad skill must
    not fail the whole export" only held for `zip`. `_zip_dir` walks the entire
    skill directory, so one unreadable file, a full disk, or a non-UTF-8
    `.skill_meta.json` reached the route as a 500 naming neither skill nor file.
    """
    from narranexus.platform.bundle import builder as builder_mod
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_fullcopyfail", "test_user"
    await _seed_agent(db_client, aid, "FullCopyAgent", uid)
    for name in ("brokenskill", "goodskill"):
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, name)

    real_zip_dir = builder_mod._zip_dir

    def _flaky_zip_dir(src, dst, scrub_secrets=False):
        if "brokenskill" in str(src):
            Path(dst).write_bytes(b"PARTIAL")     # leave a stub, then fail
            raise OSError("file vanished mid-walk")
        return real_zip_dir(src, dst, scrub_secrets)

    monkeypatch.setattr(builder_mod, "_zip_dir", _flaky_zip_dir)

    bundle = tmp_path / "fullcopy.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": n, "skill_dir": n,
                 "install_method": "full_copy"}
                for n in ("brokenskill", "goodskill")
            ],
        ),
        bundle,
    )

    assert bundle.exists(), "a full_copy failure must not fail the whole export"
    warnings = " ".join(result.get("warnings", []))
    assert "brokenskill" in warnings and "full_copy failed" in warnings, warnings
    skills = {s["name"]: s for s in _manifest(bundle).get("skills", [])}
    assert "goodskill" in skills and skills["goodskill"].get("archive_ref")
    assert "brokenskill" not in skills, "skipped skill must not appear in the manifest"
    with zipfile.ZipFile(bundle) as z:
        assert not [n for n in z.namelist() if "brokenskill" in n], "partial archive shipped"


@pytest.mark.parametrize(
    "archive_ref",
    [
        "../../../../etc/hosts.zip",
        "../outside.zip",
        "/etc/passwd",
        "skills/../../../outside.zip",
    ],
)
async def test_import_rejects_an_archive_ref_that_escapes_the_bundle(
    db_client, tmp_workspace_root, tmp_path, archives_root, archive_ref
):
    """Mirror image of the export-side `skill_dir` hole: `archive_ref` comes from
    the manifest of the bundle being imported — from whoever produced it — and
    was joined onto `work_dir` verbatim. A traversing ref let the import read a
    file outside the unpack dir and copy it into the importing user's own
    `skill_archives`, from where a later export ships it back out.

    Driven through the real preflight+confirm path, not by calling the helper:
    a unit test of the helper stays green if a call site forgets to use it (it
    did, on the first draft of this test).
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_refescape", "test_user"
    await _seed_agent(db_client, aid, "RefAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")
    good = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nok\n")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="arena", source_type="zip",
        sha256="c0ffee", archive_path=str(good),
    )

    bundle = tmp_path / "ref.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[{"agent_id": aid, "skill_name": "arena",
                            "skill_dir": "arena", "install_method": "zip"}],
        ),
        bundle,
    )

    # A file the importer must never reach, plus a manifest pointing at it.
    secret = tmp_path / "outside.zip"
    with zipfile.ZipFile(secret, "w") as z:
        z.writestr("SKILL.md", "---\nname: stolen\n---\nIMPORT_ESCAPE_CANARY\n")

    tampered = tmp_path / "tampered.nxbundle"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "manifest.json":
                m = json.loads(data)
                for s in m.get("skills", []):
                    s["archive_ref"] = archive_ref
                data = json.dumps(m).encode()
            dst.writestr(item, data)

    pre = await preflight(tampered, uid)

    # Plant the canary where the traversing ref ACTUALLY resolves. Without this
    # the ref points at nothing, the import fails with "archive missing", and
    # the test passes whether or not the gate exists — the first draft did
    # exactly that and stayed green under mutation.
    work_dir = Path(
        (await db_client.get_one("bundle_preflight_sessions",
                                 {"token": pre["preflight_token"]}))["work_dir"]
    )
    target = (work_dir / archive_ref) if not archive_ref.startswith("/") else Path(archive_ref)
    if not archive_ref.startswith("/"):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(secret, target)
        assert target.exists() and not target.resolve().is_relative_to(work_dir.resolve()), (
            "fixture bug: the canary must sit OUTSIDE work_dir"
        )

    summary = await confirm(pre["preflight_token"], uid)

    # The skill must be recorded as failed, never installed from outside.
    assert summary.get("skills_imported", 0) == 0, summary
    failures = " ".join(
        f"{f.get('skill')} {f.get('reason')}" for f in summary.get("skill_install_failures", [])
    )
    assert "arena" in failures, summary.get("warnings")

    # And nothing outside the unpack dir ended up in the importer's archives.
    for p in (archives_root).rglob("*"):
        if p.is_file():
            assert b"IMPORT_ESCAPE_CANARY" not in p.read_bytes(), f"escaped file landed at {p}"


@pytest.mark.parametrize("bad_aid", ["../../../../etc", "../sneaky", "a/b", ".."])
async def test_import_rejects_a_traversing_agent_id_in_the_manifest(
    db_client, tmp_workspace_root, tmp_path, archives_root, bad_aid
):
    """`manifest["agents"]` entries become `work_dir/agents/{aid}/agent.json`
    and `.../channel_credentials.json`. Same class as `archive_ref`, same
    manifest, and it was still open after `archive_ref` was gated — which is
    the per-field-gating mistake this PR keeps re-learning. Validated once at
    manifest ingress, and the whole bundle is rejected rather than the offending
    agent skipped: `aid` is also the id_map key, so a filtered list would
    desync id_map from the per-agent writes.
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight

    aid, uid = "agent_manifestaid", "test_user"
    await _seed_agent(db_client, aid, "ManifestAgent", uid)

    bundle = tmp_path / "aid.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid]), bundle)

    tampered = tmp_path / f"aid-{abs(hash(bad_aid))}.nxbundle"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "manifest.json":
                m = json.loads(data)
                m["agents"] = [bad_aid]
                data = json.dumps(m).encode()
            dst.writestr(item, data)

    with pytest.raises(ValueError) as exc:
        await preflight(tampered, uid)
    assert "agent id" in str(exc.value).lower(), str(exc.value)
