"""
@file_name: security.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle security helpers — zip extraction guards & path validation

PRD §8.7:
- zip-bomb caps (file size + decompressed total)
- path traversal protection (no '..', no absolute paths, no symlinks)
- sha256 integrity verification
"""

import hashlib
import io
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, List

# Caps live in `utils.file_safety` — the module both this gate and the
# installer (`skill_module._extract_zip_safely`) already depend on, so the
# two cannot drift apart. Imported as a MODULE, read at call time: see the
# read sites for why `from … import MAX_…` would undo the whole point.
from narranexus.platform.utils import file_safety as _file_safety


MAX_BUNDLE_BYTES = 500 * 1024 * 1024            # 500 MB on-disk
MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB after extract


# Sensitive path / filename patterns (PRD §8.12.9, also reused by §8.12.11)
SENSITIVE_PATH_PATTERNS = [
    ".env",
    ".env.",
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".docker/",
    ".kube/",
    ".git/config",
    ".netrc",
    ".git-credentials",
]

SENSITIVE_BASENAME_PATTERNS = [
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    ".pgpass",
]

SENSITIVE_BASENAME_GLOBS = [
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*_token*",
    "*_secret*",
]

VOLUME_PATH_PATTERNS = [
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    ".cache/",
    ".next/",
]


def safe_zip_member(name: str) -> PurePosixPath:
    """Validate a zip entry name; reject path traversal & absolute paths."""
    if not name:
        raise ValueError("empty zip member name")
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute():
        raise ValueError(f"absolute path in zip: {name}")
    parts = p.parts
    for part in parts:
        if part == "..":
            raise ValueError(f"path traversal in zip: {name}")
    return p


def extract_zip_safely(
    zip_path: Path,
    target_dir: Path,
    max_total_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> List[Path]:
    """Extract a zip archive into target_dir while enforcing size + path-safety caps.
    Returns the list of created files (relative-to-target paths)."""
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    total = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # Reject symlinks: external_attr's high 16 bits encode unix mode
            mode = (info.external_attr >> 16) & 0xFFFF
            if (mode & 0o170000) == 0o120000:
                raise ValueError(f"symlink in zip: {info.filename}")
            safe = safe_zip_member(info.filename)
            full = (target_dir / safe).resolve()
            if not str(full).startswith(str(target_dir) + os.sep) and full != target_dir:
                raise ValueError(f"escape after normalize: {info.filename}")
            if info.file_size > max_total_bytes:
                raise ValueError(f"single file too large: {info.filename} ({info.file_size}B)")
            total += info.file_size
            if total > max_total_bytes:
                raise ValueError(f"decompressed total exceeds cap ({max_total_bytes}B)")
            if info.is_dir():
                full.mkdir(parents=True, exist_ok=True)
                continue
            full.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(full, "wb") as dst:
                # stream copy (don't .read() the whole thing)
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            out_paths.append(full.relative_to(target_dir))
    return out_paths


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sensitive_path(rel_path: str) -> bool:
    """Match the bundle-export sensitive-path filter (default unchecked)."""
    p = rel_path.replace("\\", "/")
    parts = p.split("/")
    for pat in SENSITIVE_PATH_PATTERNS:
        if pat.endswith("/"):
            if any(part == pat[:-1] for part in parts):
                return True
        else:
            if any(part == pat or part.startswith(pat) for part in parts):
                return True
    base = parts[-1] if parts else ""
    if base in SENSITIVE_BASENAME_PATTERNS:
        return True
    from fnmatch import fnmatch
    for g in SENSITIVE_BASENAME_GLOBS:
        if fnmatch(base, g):
            return True
    return False


def is_volume_path(rel_path: str) -> bool:
    """Detect bulky-but-not-sensitive paths (default unchecked, no warning)."""
    p = rel_path.replace("\\", "/")
    parts = p.split("/")
    for pat in VOLUME_PATH_PATTERNS:
        if pat.endswith("/"):
            if any(part == pat[:-1] for part in parts):
                return True
    return False


def _validate_skill_archive(zf: zipfile.ZipFile, *, require_skill_md: bool) -> None:
    """Admission check for a skill archive — METADATA ONLY, never decompresses.

    Reads the central directory (`infolist()`) and nothing else. That is a
    deliberate constraint, not an oversight:

    - `testzip()` / any read of the entries decompresses the whole archive.
      Deflate reaches ~1030:1, so a 50 MB upload (our `max_upload_bytes`)
      expands to ~50 GB. On an `async def` route with no thread offload that
      pins the event loop and stalls every other user's requests and WS frames
      — our own bug becoming the interruption source (binding rule #16).
    - Metadata alone is enough for what this gate exists to do. The consumer
      that used to blow up, `scan_zip_for_sensitive`, only calls `infolist()`.

    What this therefore does NOT verify: per-entry CRC, i.e. an archive whose
    central directory is intact but whose data section is corrupt is admitted.
    That failure surfaces at install time on the importer, per-skill, already
    caught. Do not "upgrade" this to a CRC pass without moving it off the event
    loop AND bounding the decompressed size first.

    `file_size` is self-reported by the archive and a hostile one may understate
    it; this is the cheap first gate. Actual enforcement happens where bytes are
    really written — `extract_zip_safely` counts what it writes.

    Args:
        zf: An open ZipFile (already parsed, so structurally valid).
        require_skill_md: Reject an archive with no SKILL.md anywhere. Only the
            workspace-local registration path asks for this; the upload route
            does not, so that an archive which is merely unusable (rather than
            malformed) is still storable and reported by the importer instead.

    Raises:
        ValueError: With a message the end user can act on.
    """
    infos = zf.infolist()
    if len(infos) > _file_safety.MAX_SKILL_ARCHIVE_ENTRIES:
        raise ValueError(
            f"Skill archive has too many entries ({len(infos)}, "
            f"limit is {_file_safety.MAX_SKILL_ARCHIVE_ENTRIES})."
        )
    total = sum(i.file_size for i in infos)
    if total > _file_safety.MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES:
        limit_mb = _file_safety.MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES // (1024 * 1024)
        raise ValueError(
            f"Skill archive unpacks to {total // (1024 * 1024)} MB, "
            f"which exceeds the {limit_mb} MB limit."
        )
    # Bit 0 of the general-purpose flags = encrypted. Readable from metadata,
    # so we can say "needs a password" now instead of failing at install time.
    if any(i.flag_bits & 0x1 for i in infos):
        raise ValueError(
            "Skill archive is encrypted and cannot be installed. "
            "Upload an unencrypted .zip."
        )
    if require_skill_md and not any(
        n.lower().endswith("skill.md") for n in zf.namelist()
    ):
        raise ValueError("zip does not contain SKILL.md")


def validate_skill_archive_bytes(data: bytes, *, require_skill_md: bool = False) -> None:
    """`_validate_skill_archive` for an in-memory upload. Raises ValueError."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _validate_skill_archive(zf, require_skill_md=require_skill_md)
    except zipfile.BadZipFile as e:
        raise ValueError(
            f"Skill archive is not a readable zip file ({e}). "
            "Upload the .zip you installed the skill from."
        )


def validate_skill_archive_path(path: Path, *, require_skill_md: bool = False) -> None:
    """`_validate_skill_archive` for an on-disk archive. Raises ValueError."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            _validate_skill_archive(zf, require_skill_md=require_skill_md)
    except (zipfile.BadZipFile, OSError):
        # OSError covers unreadable / not-a-file paths. Pre-refactor this
        # variant only caught BadZipFile and let those escape; same shape as
        # the bytes variant now.
        raise ValueError("Not a valid zip file")


def scan_zip_for_sensitive(zip_path: Path) -> List[str]:
    """Return list of zip entries inside the archive matching sensitive patterns."""
    hits: List[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if is_sensitive_path(info.filename):
                hits.append(info.filename)
    return hits
