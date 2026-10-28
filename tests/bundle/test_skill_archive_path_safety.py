"""
@file_name: test_skill_archive_path_safety.py
@author: NarraNexus
@date: 2026-08-17
@description: SEC-07 — `skill_archives/{user_id}/{skill_name}.*` is composed in
             seven places across three files. This pins the single chokepoint
             (`skill_backup.archive_target`) that all of them must go through,
             plus the read-side guard that keeps an already-poisoned
             `archive_path` row from exfiltrating a file into an export.

Write side: every caller passes a name that ultimately comes from outside —
a multipart Form field (route), a bundle manifest (importer), an LLM tool
argument (MCP tools). None of them may be able to leave the user's directory.

Read side: rows written before the fix (QA left one on the dev env, id=20)
still carry `../` paths. `build_bundle` copies `archive_path` into the zip it
streams back, so containment has to be re-checked at read time too — sealing
the write path does not retroactively clean the DB.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


# ─── write side: the chokepoint ─────────────────────────────────────────────


@pytest.fixture
def archives_root(tmp_path, monkeypatch):
    from narranexus.platform.bundle import skill_backup

    root = tmp_path / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", root)
    return root


TRAVERSAL_NAMES = [
    "../qa-sec07-oneup-marker",
    "../../etc/cron.d/qa-sec07",
    "../other_user/stolen",
    "a/b",
    "a\\b",
    "/abs/path",
    "..",
    ".",
    "",
    "nul\x00byte",
]


@pytest.mark.parametrize("skill_name", TRAVERSAL_NAMES)
def test_archive_target_rejects_traversal(archives_root, skill_name):
    from narranexus.platform.bundle.skill_backup import archive_target

    with pytest.raises(ValueError) as exc:
        archive_target("u1", skill_name)
    assert "skill name" in str(exc.value).lower()


@pytest.mark.parametrize("suffix", [".zip", ".tar.gz", "_full.zip"])
def test_archive_target_composes_inside_the_user_dir(archives_root, suffix):
    from narranexus.platform.bundle.skill_backup import archive_target

    p = archive_target("u1", "legit-skill", suffix=suffix)
    assert p == archives_root / "u1" / f"legit-skill{suffix}"


def test_archive_target_is_pure(archives_root):
    """It must not mkdir. The route's "a 4xx leaves no trace" promise depends on
    validation being side-effect free — with the mkdir inside, every later 400
    (bad source_type, missing file, oversize) still littered a user dir."""
    from narranexus.platform.bundle.skill_backup import archive_target

    archive_target("u1", "legit-skill")
    assert not archives_root.exists(), "validation created directories"


def test_prepare_archive_target_creates_the_parent(archives_root):
    """The write-time variant is the one that may touch the filesystem."""
    from narranexus.platform.bundle.skill_backup import prepare_archive_target

    p = prepare_archive_target("u1", "legit-skill")
    assert p.parent.is_dir()
    assert not p.exists(), "only the dir, never the file"


def test_archive_target_rejects_a_traversing_user_id(archives_root):
    """user_id comes from JWT/header resolution, but it is a path segment too."""
    from narranexus.platform.bundle.skill_backup import archive_target

    with pytest.raises(ValueError) as exc:
        archive_target("../elsewhere", "legit-skill")
    assert "user id" in str(exc.value).lower()


def test_read_guard_rejects_symlinked_user_dir(archives_root):
    """Read side keeps the archives-root anchor too, so a `{root}/{uid}` symlink
    pointing out of the tree cannot make "inside the user's dir" vacuously true.
    Today the write side already refuses such a dir — this is the depth that
    stops mattering the moment some other path learns to write the column."""
    from narranexus.platform.bundle.skill_backup import is_within_user_archive_dir

    outside = archives_root.parent / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    archives_root.mkdir(parents=True, exist_ok=True)
    (archives_root / "u3").symlink_to(outside, target_is_directory=True)
    escaped = outside / "loot.zip"
    escaped.write_bytes(b"x")

    assert not is_within_user_archive_dir("u3", escaped)
    # …while an ordinary user dir still passes.
    (archives_root / "u4").mkdir()
    assert is_within_user_archive_dir("u4", archives_root / "u4" / "ok.zip")


def test_archive_target_rejects_symlink_escape(archives_root):
    """A symlinked user dir must not become a way out of the archives root."""
    from narranexus.platform.bundle.skill_backup import archive_target

    outside = archives_root.parent / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    archives_root.mkdir(parents=True, exist_ok=True)
    (archives_root / "u2").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        archive_target("u2", "legit-skill")


# ─── write side: every composition site routes through the chokepoint ───────


def test_no_site_composes_an_archive_path_by_hand():
    """Regression guard on the SHAPE of the bug, not on a known line.

    Scope, stated honestly so nobody over-trusts a green run. It flags a line
    that mentions `skill_name` AND looks like path composition (`/`, with or
    without spaces; `.joinpath(`; `os.path.join(`), anywhere in the bundle
    package or the bundle route. Composition in argument position counts —
    `copy2(src, base / f"{skill_name}.zip")` is caught, not just assignments.
    Exemptions are narrow and by shape, never by line number: the sanctioned
    builders (`archive_target` / `prepare_archive_target`) and log/label lines
    (`logger.…`, or an f-string fed to `append(`), which is where
    `f"{skill_name}@{old_aid}"` lives.

    What it still cannot see: composition that renames the variable first
    (`name = s.get("name")`), or that splits across lines. It catches the shape
    this bug class actually recurs in — it is not a proof of absence, so do not
    skip a manual sweep because it is green.
    """
    from tests._paths import engine_source_roots

    def _find_bundle_package() -> Path:
        """Locate the bundle package under any engine source root.

        Batch 6 moved this package from ``src/xyz_agent_context/bundle`` to
        ``src/narranexus/platform/bundle``; walking every engine source root
        (rather than hard-coding one path) means the next move fixes this
        guard by fixing ``tests/_paths.py`` instead of going silently blind.
        """
        for root in engine_source_roots():
            for candidate in root.rglob("bundle"):
                if candidate.is_dir() and (candidate / "skill_backup.py").is_file():
                    return candidate
        raise AssertionError(
            "could not find the bundle package (with skill_backup.py) under "
            "any engine source root — this guard has nothing left to scan"
        )

    suspects = [
        REPO_ROOT / "backend" / "routes" / "bundle.py",
        *sorted(_find_bundle_package().glob("*.py")),
    ]
    assert len(suspects) >= 10, (
        f"only {len(suspects)} suspect file(s) found — the bundle package "
        "moved again and this guard is scanning far fewer files than it should"
    )

    def _is_path_composition(line: str) -> bool:
        if "archive_target(" in line:  # the sanctioned builders
            return False
        if line.startswith("logger.") or "append(" in line:  # log / label lines
            return False
        return ("/" in line) or (".joinpath(" in line) or ("os.path.join(" in line)

    offenders = []
    for f in suspects:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "skill_name" in stripped and _is_path_composition(stripped):
                offenders.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {stripped}")
    assert not offenders, "hand-composed archive path(s):\n" + "\n".join(offenders)


# ─── read side: a poisoned row must not exfiltrate into an export ───────────


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
    (d / "SKILL.md").write_text("---\nname: arena\n---\n\nbody\n", encoding="utf-8")
    return d


def _find_canary(bundle: Path, needle: bytes, name_fragment: str) -> list[str]:
    """Every member of the bundle, one level of nesting deep, hunting for bytes
    or member names that should not be there.

    One level is enough for what we ship (`skills/**.zip` inside the outer
    `.nxbundle`) and keeps the failure message readable.
    """
    found: list[str] = []
    with zipfile.ZipFile(bundle) as z:
        for n in z.namelist():
            if n.endswith("/"):
                continue
            if name_fragment in n:
                found.append(n)
            data = z.read(n)
            if needle in data:
                found.append(n)
            if n.endswith(".zip"):
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as inner:
                        for m in inner.namelist():
                            if m.endswith("/"):
                                continue
                            if name_fragment in m or needle in inner.read(m):
                                found.append(f"{n}!{m}")
                except zipfile.BadZipFile:
                    pass
    return found


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("manifest.json"))


def _bundle_contains(bundle: Path, needle: bytes) -> bool:
    with zipfile.ZipFile(bundle) as z:
        return any(needle in z.read(n) for n in z.namelist() if not n.endswith("/"))


async def _export_with_poisoned_row(db_client, ws_root, tmp_path, poisoned_path: Path, tag: str):
    """Seed one poisoned `skill_archives` row, export, return (result, bundle)."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = f"agent_{tag}", "test_user"
    await _seed_agent(db_client, aid, f"Agent{tag}", uid)
    _seed_skill_on_disk(ws_root, aid, uid, "arena")

    await SkillArchiveRepository(db_client).upsert(
        user_id=uid,
        skill_name="arena",
        source_type="zip",
        sha256="deadbeef",
        archive_path=str(poisoned_path),
    )

    bundle = tmp_path / f"{tag}.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {
                    "agent_id": aid,
                    "skill_name": "arena",
                    "skill_dir": "arena",
                    "install_method": "zip",
                }
            ],
        ),
        bundle,
    )
    return result, bundle


def _assert_skipped(result, bundle):
    assert not _bundle_contains(bundle, b"SEC07_EXFILTRATION_CANARY"), (
        "a poisoned archive_path was copied into the export"
    )
    warnings = " ".join(result.get("warnings", []))
    assert "arena" in warnings and "archive" in warnings.lower()
    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    assert all(not e.get("archive_ref") for e in entries)


async def test_absolute_outside_path_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """`archive_path: "/etc/passwd"` shape — the read-side vector from /export."""
    secret = tmp_path / "victims-secret.zip"
    secret.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, secret, "outside"
    )
    _assert_skipped(result, bundle)


async def test_root_level_stray_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The dev env's actual id=20 shape, and why a ROOT-anchored guard is not
    enough: the stored string `{root}/{uid}/../marker.zip` resolves to
    `{root}/marker.zip` — inside the archives root, outside anyone's user dir.
    A root-anchored check waves this exact row through."""
    stray = archives_root / "qa-sec07-oneup-marker.zip"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, stray, "stray"
    )
    _assert_skipped(result, bundle)


async def test_other_users_archive_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Cross-user read: a row naming ANOTHER user's archive dir. Also inside the
    root, so likewise invisible to a root-anchored guard."""
    victim = archives_root / "other_user" / "private.zip"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, victim, "crossuser"
    )
    _assert_skipped(result, bundle)


async def test_legitimate_archive_row_still_exports(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The guard must not break the normal zip-method export path."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_sec07002", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent2", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    good = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")

    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid,
        skill_name="arena",
        source_type="zip",
        sha256="cafebabe",
        archive_path=str(good),
    )

    bundle = tmp_path / "clean.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {
                    "agent_id": aid,
                    "skill_name": "arena",
                    "skill_dir": "arena",
                    "install_method": "zip",
                }
            ],
        ),
        bundle,
    )

    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    assert entries and entries[0].get("archive_ref"), "legit archive was not packed"
    assert _bundle_contains(bundle, b"legit")


# ─── importing a zip-method skill must leave a usable archive row ───────────


async def test_imported_zip_skill_registers_archive(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Export zip-method → import → the receiving user must have a
    `skill_archives` row pointing at the archive on disk.

    This was silently broken: the importer handed its own already-in-place
    `tgt` to `backup_after_api_install`, which recomputed the same path and did
    `copy2(tgt, tgt)` → `SameFileError` → swallowed by that helper's broad
    `except` → no row written. Consequence: an imported skill could never be
    re-exported as `zip`, it degraded to `full_copy` — which ships secrets when
    the user picks full mode. Now the importer registers `tgt` directly.
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_sec07003", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent3", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    src = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")
    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid, skill_name="arena", source_type="zip", sha256="cafebabe",
        archive_path=str(src),
    )

    bundle = tmp_path / "zip-method.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {
                    "agent_id": aid,
                    "skill_name": "arena",
                    "skill_dir": "arena",
                    "install_method": "zip",
                }
            ],
        ),
        bundle,
    )

    # Drop the row so the import has to create it (models a fresh target env).
    await repo.remove(uid, "arena")
    assert await repo.get(uid, "arena") is None

    pre = await preflight(bundle, uid)
    summary = await confirm(pre["preflight_token"], uid)
    assert summary.get("skills_imported", 0) == 1, summary.get("warnings")

    row = await repo.get(uid, "arena")
    assert row is not None, "import left no skill_archives row (SameFileError swallowed?)"
    assert row.archive_path and Path(row.archive_path).exists()
    assert row.sha256 and row.sha256 != "pending"


async def test_shared_skill_import_records_a_real_sha(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Two agents sharing one zip skill: the imported row must carry a real
    digest, not the bundle's de-dup sentinel.

    `builder` emits one manifest entry per (agent, skill) and marks entries
    2..N `sha256: "shared"` because they point at one already-copied
    archive_ref. The importer registers every entry (upsert, last write wins),
    so taking the manifest value verbatim persisted the literal `"shared"` into
    `skill_archives.sha256` — a column whose only job is integrity.
    """
    import re

    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    uid = "test_user"
    aids = ["agent_sec07004", "agent_sec07005"]
    for i, aid in enumerate(aids):
        await _seed_agent(db_client, aid, f"SharedAgent{i}", uid)
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    src = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")
    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid, skill_name="arena", source_type="zip", sha256="cafebabe",
        archive_path=str(src),
    )

    bundle = tmp_path / "shared.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=aids,
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "arena",
                 "install_method": "zip"}
                for aid in aids
            ],
        ),
        bundle,
    )

    # Precondition: the bundle really does carry the sentinel we're guarding
    # against — otherwise this test would pass for the wrong reason.
    shas = [s.get("sha256") for s in _manifest(bundle).get("skills", [])
            if s.get("name") == "arena"]
    assert "shared" in shas, f"expected a de-dup sentinel in the manifest, got {shas}"

    await repo.remove(uid, "arena")
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    row = await repo.get(uid, "arena")
    assert row is not None
    assert re.fullmatch(r"[0-9a-f]{64}", row.sha256 or ""), (
        f"skill_archives.sha256 is not a digest: {row.sha256!r}"
    )


# ─── export side: skill_dir is a client string too ──────────────────────────


@pytest.mark.parametrize("method", ["zip", "full_copy"])
@pytest.mark.parametrize(
    "skill_dir",
    [
        "../../../../tmp/qa-export-escape",
        "../escape",
        "sub/dir",
        "sub\\dir",
        "/abs/path",
        "..",
        # SEC-08's real shape. From {base}/{uid}/{aid}/skills/ this resolves to
        # `base_working_path` itself — the parent of EVERY user's workspace —
        # and full_copy zips whatever directory it lands on. The first version
        # of this table omitted it, which is why the victim-canary assertion
        # below could never fail: nothing in the table actually reached a
        # victim. Keep both forms: the root, and a direct hop into a named
        # victim.
        "../../..",
        "../../../victim_user/agent_victim",
        # NOT "" — an absent skill_dir legitimately falls back to skill_name
        # (builder.py), so it is covered by the fallback test below instead.
    ],
)
async def test_export_rejects_a_traversing_skill_dir(
    db_client, tmp_workspace_root, tmp_path, archives_root, skill_dir, method
):
    """SEC-07 sealed `skill_name` and `archive_path`; `skill_dir` travels in the
    same request body and lands in a filesystem path too. It must not be able to
    put (or, since the corrupt-archive fix added an `unlink`, remove) a file
    outside the bundle staging dir."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = f"agent_dirtrav_{method}", "test_user"
    await _seed_agent(db_client, aid, "TravAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    # A second user's workspace, one `..` hop away from ours. `full_copy` reads
    # whatever directory `skill_dir` resolves to, so `../../..` used to land on
    # `base_working_path` itself and zip every user's tree into the download.
    victim = tmp_workspace_root / "victim_user" / "agent_victim" / "skills" / "private"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "creds.json").write_text("VICTIM_SECRET_CANARY", encoding="utf-8")

    good = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nESCAPE_CANARY\n")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="arena", source_type="zip",
        sha256="cafebabe", archive_path=str(good),
    )

    before = {p for p in tmp_path.rglob("*")}
    bundle = tmp_path / "trav.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena",
                 "skill_dir": skill_dir, "install_method": method}
            ],
            include_skill_secrets=True,   # worst case: no sensitive-file scrub
        ),
        bundle,
    )

    # 1. Degraded, and the warning names THIS gate — not just "something about
    #    arena", which `zip not found` / `archive path escapes` also satisfy.
    warnings = " ".join(result.get("warnings", []))
    assert "unusable skill_dir" in warnings, warnings
    # 2. The skill is absent from the manifest (the established "skipped" shape).
    assert not [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    # 3. The observable contract that actually matters: no other user's bytes.
    #    Must recurse. A packed workspace arrives as a NESTED zip member
    #    (`skills/{aid}/{dir}-full.zip`, deflated), so a single-level
    #    `z.read(n)` returns compressed bytes and the plaintext canary never
    #    appears — the first version of this assertion was blind for that
    #    reason on top of having no payload that reached a victim.
    #    Scans root-level members too: with the gate removed the escaping
    #    archive lands as `..-full.zip` in the staging root, not under skills/.
    leaks = _find_canary(bundle, b"VICTIM_SECRET_CANARY", "victim_user")
    assert not leaks, f"another user's workspace was packed: {leaks}"
    # 4. Nothing written outside the bundle staging dir.
    escaped = {
        p for p in (tmp_path.rglob("*"))
        if p not in before and p.suffix == ".zip" and "nxbundle" not in p.name
        and "skill_archives" not in str(p)
    }
    assert not escaped, f"export wrote outside the bundle: {escaped}"
    assert not Path("/tmp/qa-export-escape.zip").exists()


async def test_same_skill_dir_on_one_agent_gets_distinct_bundle_filenames(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Two different skills declaring the same `skill_dir` used to collide on
    `{dir}__{agent_id}.zip`: the second overwrote the first (whose manifest
    sha256 then described someone else's bytes), and a failure would delete it.
    Names must be unique per bundle."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_dupdir01", "test_user"
    await _seed_agent(db_client, aid, "DupDirAgent", uid)
    repo = SkillArchiveRepository(db_client)
    for name, canary in (("skill_a", "CANARY_A"), ("skill_b", "CANARY_B")):
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, name)
        p = prepare_archive_target(uid, name)
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(f"{name}/SKILL.md", f"---\nname: {name}\n---\n{canary}\n")
        await repo.upsert(user_id=uid, skill_name=name, source_type="zip",
                          sha256="c0ffee", archive_path=str(p))

    bundle = tmp_path / "dupdir.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                # both claim the SAME skill_dir on the SAME agent
                {"agent_id": aid, "skill_name": n, "skill_dir": "shared",
                 "install_method": "zip"}
                for n in ("skill_a", "skill_b")
            ],
        ),
        bundle,
    )

    skills = {s["name"]: s for s in _manifest(bundle).get("skills", [])}
    refs = {n: skills[n]["archive_ref"] for n in ("skill_a", "skill_b")}
    assert refs["skill_a"] != refs["skill_b"], f"both skills share one file: {refs}"
    with zipfile.ZipFile(bundle) as z:
        for n, canary in (("skill_a", b"CANARY_A"), ("skill_b", b"CANARY_B")):
            inner = z.read(refs[n])
            with zipfile.ZipFile(io.BytesIO(inner)) as iz:
                blob = b"".join(iz.read(m) for m in iz.namelist())
            assert canary in blob, f"{n}'s archive_ref points at the wrong bytes"


async def test_absent_skill_dir_falls_back_to_skill_name(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """`skill_dir=None`/"" is not an attack, it means "not specified" — the
    builder falls back to `skill_name`, which goes through the same gate."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.skill_backup import prepare_archive_target
    from narranexus.platform.repository import SkillArchiveRepository

    aid, uid = "agent_nodir01", "test_user"
    await _seed_agent(db_client, aid, "NoDirAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")
    p = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nFALLBACK_CANARY\n")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="arena", source_type="zip",
        sha256="c0ffee", archive_path=str(p),
    )

    bundle = tmp_path / "nodir.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "",
                 "install_method": "zip"}
            ],
        ),
        bundle,
    )
    entry = [s for s in _manifest(bundle).get("skills", []) if s["name"] == "arena"]
    assert entry and entry[0]["archive_ref"] == "skills/arena.zip", entry


# ─── the other write entry point gets the same gate ─────────────────────────


@pytest.mark.parametrize(
    "make_archive,expected",
    [
        pytest.param("entries", "too many entries", id="too-many-entries"),
        pytest.param("bomb", "unpacks to", id="declared-size-bomb"),
        pytest.param("encrypted", "encrypted", id="encrypted"),
        pytest.param("garbage", "not a valid zip", id="not-a-zip"),
        pytest.param("no_skill_md", "skill.md", id="missing-SKILL.md"),
    ],
)
async def test_archive_local_zip_enforces_the_shared_gate(
    tmp_path, archives_root, monkeypatch, make_archive, expected
):
    """`archive_local_zip` is the SECOND writer into `skill_archives`, and it
    now shares the upload route's validator. Before, it only looked for SKILL.md
    — so entry-count, declared-size and encrypted archives all walked in here
    while being rejected at the door two metres away. Only the route had test
    coverage for the new gates; this closes that.
    """
    import io as _io

    from narranexus.platform.bundle import skill_backup
    from narranexus.platform.utils import file_safety
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    monkeypatch.setattr(file_safety, "MAX_SKILL_ARCHIVE_ENTRIES", 5)
    monkeypatch.setattr(file_safety, "MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES", 1024 * 1024)

    aid, uid = "agent_localzip01", "test_user"
    ws = agent_workspace_path(aid, uid, base=str(tmp_path / "ws"))
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(skill_backup, "_agent_workspace_root", lambda a, u: ws)

    src = ws / "candidate.zip"
    if make_archive == "garbage":
        src.write_bytes(b"not a zip at all")
    elif make_archive == "entries":
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("SKILL.md", "---\nname: x\n---\n")
            for i in range(20):
                z.writestr(f"f{i}.txt", "x")
    elif make_archive == "bomb":
        with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("SKILL.md", "---\nname: x\n---\n")
            z.writestr("bomb.bin", b"\0" * (4 * 1024 * 1024))
    elif make_archive == "encrypted":
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", "---\nname: x\n---\n")
        raw = bytearray(buf.getvalue())
        raw[6] |= 0x01
        raw[raw.find(b"PK\x01\x02") + 8] |= 0x01
        src.write_bytes(bytes(raw))
    else:  # no_skill_md
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("readme.txt", "no skill manifest here")

    with pytest.raises(ValueError) as exc:
        await skill_backup.archive_local_zip(
            user_id=uid, agent_id=aid, skill_name="candidate", zip_file_path=str(src)
        )
    assert expected in str(exc.value).lower(), str(exc.value)
    assert not (archives_root / uid).exists(), "a rejected archive was still stored"


async def test_full_copy_cannot_pack_another_users_workspace(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """SEC-08's regression net, asserting ONE thing: another user's bytes are
    not in the artifact.

    Deliberately separate from `test_export_rejects_a_traversing_skill_dir`.
    That test asserts the warning text first, so under mutation it fails on
    line 1 and the leak assertion is never evaluated — it can report "12 red"
    while the P0's actual contract goes unchecked. A regression net for a
    cross-tenant read has to fail *because bytes leaked*, nothing else.

    Payload is `../../..` specifically: from `{base}/{uid}/{aid}/skills/` it
    resolves to `base_working_path` — the parent of every user's workspace —
    and its OUTPUT path (`{staging}/skills/{aid}/../../..-full.zip`) still
    lands inside the staging dir, so a regression really does ship the bytes.
    Deeper payloads fail on the write instead and would prove nothing.
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_sec08", "test_user"
    await _seed_agent(db_client, aid, "Sec08Agent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    victim = tmp_workspace_root / "victim_user" / "agent_victim" / "skills" / "private"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "creds.json").write_text("VICTIM_SECRET_CANARY", encoding="utf-8")

    bundle = tmp_path / "sec08.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena",
                 "skill_dir": "../../..", "install_method": "full_copy"}
            ],
            include_skill_secrets=True,   # worst case: sensitive-file scrub off
        ),
        bundle,
    )

    leaks = _find_canary(bundle, b"VICTIM_SECRET_CANARY", "victim_user")
    assert not leaks, f"SEC-08 regression — another user's workspace shipped: {leaks}"



async def test_archive_local_zip_refuses_a_symlinked_workspace(tmp_path, archives_root, monkeypatch):
    """The agent's workspace dir swapped for a link to another user's: a zip in
    the victim's workspace must not be copied into the caller's archives."""
    import os

    from narranexus.platform.bundle import skill_backup
    from narranexus.platform.settings import settings as core_settings
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    base = tmp_path / "ws"
    monkeypatch.setattr(core_settings, "base_working_path", str(base))
    aid, uid = "agent_localzip01", "test_user"
    victim = agent_workspace_path("agent_victim01", "victim_user", base=str(base))
    victim.mkdir(parents=True)
    with zipfile.ZipFile(victim / "skill.zip", "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n")
    own = agent_workspace_path(aid, uid, base=str(base))
    own.parent.mkdir(parents=True)
    os.symlink(victim, own)

    for p in ("skill.zip", str(own / "skill.zip"), str(victim / "skill.zip")):
        with pytest.raises(ValueError):
            await skill_backup.archive_local_zip(
                user_id=uid, agent_id=aid, skill_name="stolen", zip_file_path=p
            )
    assert not (archives_root / uid).exists(), "a victim's zip was archived"


async def test_full_copy_symlinked_skill_dir_warns_and_the_export_completes(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """One skill dir swapped for a link to another tenant's skill: that skill is
    skipped with a per-skill warning, the rest of the export still ships, and
    none of the victim's bytes do ("one bad skill must not fail the export")."""
    import os

    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    aid, uid = "agent_fclink01", "test_user"
    await _seed_agent(db_client, aid, "FcLinkAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "good")
    victim = tmp_workspace_root / "victim_user" / "agent_victim" / "skills" / "private"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "SKILL.md").write_text("---\nname: private\n---\n", encoding="utf-8")
    (victim / "creds.json").write_text("VICTIM_SECRET_CANARY", encoding="utf-8")
    os.symlink(victim, agent_workspace_path(aid, uid, base=str(tmp_workspace_root)) / "skills" / "linked")

    bundle = tmp_path / "fclink.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "good", "skill_dir": "good", "install_method": "full_copy"},
                {"agent_id": aid, "skill_name": "linked", "skill_dir": "linked", "install_method": "full_copy"},
            ],
            include_skill_secrets=True,
        ),
        bundle,
    )
    warnings = " ".join(result.get("warnings", []))
    assert "linked" in warnings and "full_copy failed" in warnings, warnings
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
    assert f"skills/{aid}/good-full.zip" in names
    assert f"skills/{aid}/linked-full.zip" not in names
    assert not _find_canary(bundle, b"VICTIM_SECRET_CANARY", "victim_user")
