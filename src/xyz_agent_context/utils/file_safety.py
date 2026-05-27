"""
File safety helpers used by API upload and local package installation flows.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Iterable


def sanitize_filename(
    filename: str,
    *,
    label: str = "filename",
    allowed_extensions: Iterable[str] | None = None,
) -> str:
    """
    Validate a single path segment and optionally enforce an extension allowlist.
    """
    if not filename:
        raise ValueError(f"{label} is required")
    if "\x00" in filename:
        raise ValueError(f"Invalid {label}: null bytes are not allowed")

    normalized = filename.strip()
    safe_name = Path(normalized).name
    if safe_name != normalized or safe_name in {"", ".", ".."}:
        raise ValueError(f"Invalid {label}: path traversal not allowed")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(f"Invalid {label}: path separators are not allowed")

    if allowed_extensions is not None:
        allowed = {
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in allowed_extensions
        }
        suffix = Path(safe_name).suffix.lower()
        if suffix not in allowed:
            raise ValueError(
                f"Invalid {label}: only {', '.join(sorted(allowed))} files are supported"
            )

    return safe_name


def ensure_within_directory(base_dir: Path, filename: str, *, label: str = "filename") -> Path:
    """
    Build a safe file path under base_dir for a validated filename.
    """
    safe_name = sanitize_filename(filename, label=label)
    base_resolved = base_dir.resolve(strict=False)
    candidate = (base_dir / safe_name).resolve(strict=False)
    if candidate.parent != base_resolved:
        raise ValueError(f"Invalid {label}: path escapes target directory")
    return candidate


def enforce_max_bytes(size: int, max_bytes: int, *, label: str = "file") -> None:
    """
    Reject oversized payloads with a consistent error message.
    """
    if size > max_bytes:
        raise ValueError(
            f"{label} exceeds the maximum size of {max_bytes // (1024 * 1024)} MB"
        )


def validate_zip_member_path(member_name: str) -> PurePosixPath:
    """
    Validate a ZIP archive member path and reject zip-slip style traversal.

    Windows Explorer's "Send to > Compressed folder" writes backslash
    separators into ZIP entries (e.g. `skill\\SKILL.md`). The ZIP spec
    (APPNOTE 6.3 §4.4.17.1) requires forward slashes, but real-world
    archives violate this routinely — so we normalize backslashes to
    forward slashes BEFORE running the traversal/absolute-path checks.
    Traversal defense is unchanged: the same `..` / absolute / empty-part
    rules fire on the normalized path.
    """
    if not member_name:
        raise ValueError("Invalid archive entry: empty path")
    if "\x00" in member_name:
        raise ValueError("Invalid archive entry: null bytes are not allowed")

    normalized = member_name.replace("\\", "/")
    member_path = PurePosixPath(normalized)
    if member_path.is_absolute():
        raise ValueError(
            f"Invalid archive entry: absolute paths are not allowed "
            f"(offending entry: {member_name!r})"
        )
    if any(part in {"", ".", ".."} for part in member_path.parts):
        raise ValueError(
            f"Invalid archive entry: path traversal not allowed "
            f"(offending entry: {member_name!r})"
        )

    return member_path
