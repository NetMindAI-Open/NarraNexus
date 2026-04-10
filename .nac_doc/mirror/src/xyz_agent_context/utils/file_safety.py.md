# file_safety.py

Validation helpers that guard against path traversal and oversized uploads before any file is written to disk.

## Why it exists

Two flows in the application accept user-supplied filenames: the API upload endpoints (where users attach files to agent context) and the local package installation flow (where module packages are unpacked from ZIP archives). Without validation, a malicious or malformed filename like `../../etc/passwd` or an archive entry like `../../../important_file` could escape the intended directory. `file_safety.py` centralizes the checks so they are applied consistently and are easy to audit.

## Upstream / Downstream

**Called by:** `backend/routes/` upload handlers (to validate uploaded file names before saving), module package installation code (to validate ZIP entry paths before extraction).

**Depends on:** stdlib `pathlib` only.

## Design decisions

**`Path(filename).name` as the normalization step.** `Path("../../etc/passwd").name` returns `"passwd"` on all platforms. Comparing the normalized result back against the original input catches any traversal attempt that Python's path handling resolves.

**`allowed_extensions` as an optional allowlist.** By default, any extension is accepted. Callers that only expect specific file types (e.g., only `.py` or `.zip`) pass an explicit list. Extensions are normalized to lowercase with a leading dot for comparison.

**`ensure_within_directory` uses `resolve(strict=False)`.** `strict=False` allows checking paths that do not yet exist on disk. The comparison `candidate.parent != base_resolved` is the safety assertion: the constructed path's parent must equal the base directory exactly.

**`validate_zip_member_path` rejects empty parts and `..`.** ZIP-slip attacks typically use entries like `../../evil`. The validator rejects any member path containing an empty part, `.`, or `..` in any segment, not just the final one.

## Gotchas

**`sanitize_filename` strips the directory component, not just traversal dots.** A filename like `subdir/file.txt` has `Path("subdir/file.txt").name == "file.txt"`, which does not equal the original, so it is rejected with "path traversal not allowed". Filenames must be plain names without any directory separators, even legitimate ones.

**`enforce_max_bytes` takes the size in bytes, not the file object.** The caller must determine the size before calling this function (e.g., `len(content)` or from a `Content-Length` header). There is no streaming check.

**New-contributor trap.** On Windows, `Path(filename).name` handles both forward and backward slashes. On Linux, a filename containing a backslash is treated as a single path segment with a backslash in its name, so the check `if "/" in normalized or "\\" in normalized` provides the cross-platform safety that `Path.name` alone does not.
