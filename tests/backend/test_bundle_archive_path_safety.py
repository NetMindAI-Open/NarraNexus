"""
@file_name: test_bundle_archive_path_safety.py
@author: NarraNexus
@date: 2026-08-17
@description: SEC-07 — the bundle routes must never let a client string decide
             where an archive is written to, or which file is read back.

Two attack surfaces, both "user-supplied string used in path composition":

  1. POST /skills/archives/upload — `skill_name` is a multipart Form field
     spliced into `skill_archives/{user_id}/{skill_name}.zip`. `../` in it
     escapes the per-user directory; `../{victim_user_id}/x` writes into
     someone else's directory. Every rejection must be a 4xx (a ValueError
     leaking as a 500 is the #113 BadZipFile mistake repeated), and nothing
     may hit the disk on a rejected request.

  2. POST /export — `skills[].archive_path` used to be echoed by the client
     and copied verbatim into the exported bundle, i.e. arbitrary local file
     read for any authenticated user. The route must resolve archives
     server-side from the DB instead of trusting the request body.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── harness ────────────────────────────────────────────────────────────────


@pytest.fixture
def archives_root(tmp_path, monkeypatch):
    """Redirect SKILL_ARCHIVES_ROOT so nothing touches the real ~/.nexusagent."""
    from narranexus.platform.bundle import skill_backup

    root = tmp_path / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", root)
    return root


class _FakeRepo:
    """Captures upsert calls so tests can assert what would land in the DB."""

    calls: list[dict] = []

    def __init__(self, db):
        pass

    async def upsert(self, **kwargs):
        _FakeRepo.calls.append(kwargs)


@pytest.fixture
def client(monkeypatch):
    import backend.routes.bundle as bundle_mod

    async def fake_user_id(request):
        return "victim_neighbour"

    async def fake_db():
        return object()

    _FakeRepo.calls = []
    monkeypatch.setattr(bundle_mod, "_user_id_for_request", fake_user_id)
    monkeypatch.setattr(bundle_mod, "get_db_client", fake_db)
    monkeypatch.setattr(bundle_mod, "SkillArchiveRepository", _FakeRepo)

    app = FastAPI()
    app.include_router(bundle_mod.router, prefix="/api/bundle")
    return TestClient(app, raise_server_exceptions=False)


def _zip_bytes(name: str = "SKILL.md", body: str = "---\nname: x\n---\n") -> bytes:
    """A REAL zip. The archive routes now verify the bytes really are one, so
    `b"PK\\x03\\x04..."` stand-ins no longer stand in."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, body)
    return buf.getvalue()


def _upload(client, skill_name: str, content: bytes | None = None):
    return client.post(
        "/api/bundle/skills/archives/upload",
        data={"skill_name": skill_name, "source_type": "zip"},
        files={"file": ("whatever.zip", content if content is not None else _zip_bytes(),
                        "application/zip")},
    )


# ─── 1. upload: traversal must be rejected as 4xx, with no disk write ───────


@pytest.mark.parametrize(
    "skill_name",
    [
        "../qa-sec07-oneup-marker",          # the exact payload QA proved works
        "../../../../tmp/qa-sec07",          # deeper climb
        "../victim_user/stolen",             # cross-user overwrite
        "sub/dir",                           # plain separator
        "sub\\dir",                          # windows separator
        "/etc/cron.d/qa-sec07",              # absolute path
        "..",                                # bare dot-dot
        ".",
        "",                                  # empty → would create ".zip"
        "evil\x00.zip",                      # null byte
    ],
)
def test_upload_rejects_path_composition_payloads(client, archives_root, skill_name):
    r = _upload(client, skill_name)
    assert r.status_code == 400, f"{skill_name!r} returned {r.status_code}, body={r.text}"
    detail = r.json()["detail"].lower()
    assert "skill name" in detail, "the 4xx must name the offending field"


@pytest.mark.parametrize(
    "skill_name",
    ["../qa-sec07-oneup-marker", "../victim_user/stolen", "/etc/cron.d/qa-sec07"],
)
def test_upload_rejection_writes_nothing_anywhere(client, archives_root, tmp_path, skill_name):
    """A rejected upload must leave the filesystem and the DB untouched."""
    before = {p for p in tmp_path.rglob("*")}
    r = _upload(client, skill_name)
    assert r.status_code == 400
    after = {p for p in tmp_path.rglob("*")}
    assert after == before, f"rejected upload created {after - before}"
    assert _FakeRepo.calls == [], "rejected upload still wrote a DB row"


@pytest.mark.parametrize(
    "data,files",
    [
        # Valid skill_name, rejected for another reason. These are the branches
        # the first version of this suite missed: validation used to mkdir, so a
        # 400 here still littered `skill_archives/{user_id}/`.
        ({"skill_name": "legit-skill", "source_type": "svn"}, None),
        ({"skill_name": "legit-skill", "source_type": "zip"}, None),  # no file
        ({"skill_name": "legit-skill", "source_type": "github"}, None),  # no url
    ],
)
def test_upload_rejection_creates_no_directory_either(client, archives_root, data, files):
    """"A 4xx leaves no trace" must hold for EVERY 4xx, not just the bad-name one."""
    r = client.post("/api/bundle/skills/archives/upload", data=data, files=files)
    assert r.status_code == 400, r.text
    assert not archives_root.exists(), "a rejected upload created the archives tree"
    assert _FakeRepo.calls == []


def test_oversize_rejection_creates_no_directory(client, archives_root, monkeypatch):
    from backend.config import settings as backend_settings

    monkeypatch.setattr(backend_settings, "max_upload_bytes", 16)
    r = _upload(client, "legit-skill", content=b"x" * 64)
    assert r.status_code == 400
    assert not archives_root.exists(), "an oversize upload created the archives tree"


@pytest.mark.parametrize(
    "content",
    [
        b"PK\x03\x04not-really-a-zip",   # right magic bytes, garbage after
        b"just some text",               # no magic at all
        b"",                             # empty upload
    ],
)
def test_upload_rejects_bytes_that_are_not_a_zip(client, archives_root, content):
    """A corrupt archive used to be accepted here (200) and only blow up later in
    /export as a 500 out of `scan_zip_for_sensitive` — a user-controlled bad
    input surfacing as a server error on a different endpoint, one endpoint
    removed from the cause. Reject it at the door, like skills.py does."""
    r = _upload(client, "legit-skill", content=content)
    assert r.status_code == 400, r.text
    assert "zip" in r.json()["detail"].lower()
    assert not archives_root.exists(), "a rejected upload created the archives tree"
    assert _FakeRepo.calls == [], "a rejected upload still wrote a DB row"


def test_upload_rejects_a_decompression_bomb_without_decompressing_it(
    client, archives_root, monkeypatch
):
    """The declared-size gate, and the reason it is declared-size and not CRC.

    `testzip()` (the first version of this check) decompresses everything to
    verify CRCs: 199 KB of deflate expands to 200 MB, so a 50 MB upload — well
    within `max_upload_bytes` — reaches ~50 GB, synchronously, on the event
    loop, stalling every other user (binding rule #16).

    So this asserts two things: the bomb is refused, AND the request never
    decompressed a single entry. The second half is enforced directly by
    exploding if anything opens an entry — a timing assertion would not catch a
    regression at a test-sized payload, which is exactly when it would be
    reintroduced.
    """
    # Patch the single source of truth. If the gate ever goes back to a
    # `from … import MAX_…` copy, this stops taking effect and the test fails —
    # which is how that copy got caught in the first place.
    from narranexus.platform.utils import file_safety

    monkeypatch.setattr(file_safety, "MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES", 1024 * 1024)

    # Build the payload BEFORE arming the tripwire: `writestr` itself goes
    # through `ZipFile.open`, so patching first would trip on our own fixture.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomb.bin", b"\0" * (4 * 1024 * 1024))  # 4 MB, still 4x the patched cap
    bomb = buf.getvalue()
    assert len(bomb) < 1024 * 1024, "payload should be tiny compressed"

    def _boom(self, *a, **kw):
        raise AssertionError(
            "the admission gate decompressed an entry — it must read the "
            "central directory only"
        )

    monkeypatch.setattr(zipfile.ZipFile, "open", _boom)

    r = _upload(client, "legit-skill", content=bomb)
    assert r.status_code == 400, r.text
    assert "unpacks to" in r.json()["detail"].lower()
    assert not archives_root.exists()


def test_upload_rejects_too_many_entries(client, archives_root, monkeypatch):
    from narranexus.platform.utils import file_safety

    monkeypatch.setattr(file_safety, "MAX_SKILL_ARCHIVE_ENTRIES", 5)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(20):
            z.writestr(f"f{i}.txt", "x")
    r = _upload(client, "legit-skill", content=buf.getvalue())
    assert r.status_code == 400
    assert "too many entries" in r.json()["detail"].lower()
    assert not archives_root.exists()


def test_upload_rejects_an_encrypted_archive(client, archives_root):
    """An encrypted zip can never be installed. Say so now — with the password
    hint — instead of letting it fail on the far side of an import. Detected
    from the general-purpose flag bit, i.e. still metadata only."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n")
    raw = bytearray(buf.getvalue())
    raw[6] |= 0x01                       # local header flag
    idx = raw.find(b"PK\x01\x02")
    raw[idx + 8] |= 0x01                 # central directory flag
    r = _upload(client, "legit-skill", content=bytes(raw))
    assert r.status_code == 400
    assert "encrypted" in r.json()["detail"].lower()
    assert not archives_root.exists()


def test_upload_admits_a_crc_corrupt_archive_on_purpose(client, archives_root):
    """Documents a deliberate gap: the gate reads the central directory only,
    so an archive with intact metadata and a corrupt data section is stored.
    Verifying CRC means decompressing — see the bomb test. That failure is
    caught per-skill on the importer instead. If someone later adds a CRC pass,
    this test should be updated *deliberately*, not silently."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n" + "payload" * 50)
    raw = bytearray(buf.getvalue())
    raw[60] ^= 0xFF                      # corrupt the data section only
    r = _upload(client, "legit-skill", content=bytes(raw))
    assert r.status_code == 200, r.text


def test_upload_size_limit_is_checked_before_zip_validity(client, archives_root, monkeypatch):
    """An oversized payload should say so, not complain about zip structure —
    the cheap check runs first and gives the actionable message."""
    from backend.config import settings as backend_settings

    monkeypatch.setattr(backend_settings, "max_upload_bytes", 16)
    r = _upload(client, "legit-skill", content=b"x" * 64)
    assert r.status_code == 400
    assert "maximum size" in r.json()["detail"].lower()
    assert not (archives_root / "victim_neighbour" / "legit-skill.zip").exists()


def test_upload_happy_path_lands_inside_the_user_directory(client, archives_root):
    payload = _zip_bytes()
    r = _upload(client, "legit-skill", content=payload)
    assert r.status_code == 200, r.text
    target = archives_root / "victim_neighbour" / "legit-skill.zip"
    assert target.exists(), f"archive not at {target}"
    assert target.read_bytes() == payload, "stored bytes must be the upload, verbatim"
    # DB row must record the resolved path, not the raw client string.
    assert len(_FakeRepo.calls) == 1
    assert _FakeRepo.calls[0]["archive_path"] == str(target)
    assert _FakeRepo.calls[0]["skill_name"] == "legit-skill"


def test_upload_github_mode_also_validates_skill_name(client, archives_root):
    """The github branch skips the write but still keys the DB row by name."""
    r = client.post(
        "/api/bundle/skills/archives/upload",
        data={
            "skill_name": "../qa-sec07-github",
            "source_type": "github",
            "source_url": "https://github.com/owner/repo",
        },
    )
    assert r.status_code == 400
    assert _FakeRepo.calls == []


# ─── 2. export: client-supplied archive paths must not be trusted ───────────


def test_export_ignores_client_supplied_archive_path(client, monkeypatch, tmp_path):
    """`archive_path` / `manual_zip_path` in the request body are the arbitrary
    file-read vector — the route must not forward them to the builder at all."""
    import backend.routes.bundle as bundle_mod

    captured = {}

    async def fake_build(user_id, selection, out_path):
        captured["selection"] = selection
        Path(out_path).write_bytes(b"stub-bundle")
        return {"warnings": [], "manifest": {"integrity_sha256": "", "info": []}}

    monkeypatch.setattr(bundle_mod, "build_bundle", fake_build)

    secret = tmp_path / "secret.txt"
    secret.write_text("root:x:0:0")

    r = client.post(
        "/api/bundle/export",
        json={
            "agent_ids": ["agent_00000001"],
            "skills": [
                {
                    "skill_name": "arena",
                    "install_method": "zip",
                    "agent_id": "agent_00000001",
                    "archive_path": str(secret),
                    "manual_zip_path": str(secret),
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    spec = captured["selection"].skill_methods[0]
    assert "archive_path" not in spec, "route still forwards a client archive_path"
    assert "manual_zip_path" not in spec, "route still forwards a client manual_zip_path"
    assert str(secret) not in repr(spec)
