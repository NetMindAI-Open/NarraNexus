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
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, List


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
