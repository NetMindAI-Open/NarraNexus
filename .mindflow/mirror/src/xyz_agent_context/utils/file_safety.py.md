---
code_file: src/xyz_agent_context/utils/file_safety.py
last_verified: 2026-05-27
stub: false
---

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

**`validate_zip_member_path` normalizes backslashes before checking.** Windows Explorer's "Send to → Compressed folder" produces ZIP archives whose entry names contain `\` separators (e.g. `skill\SKILL.md`). The ZIP spec (APPNOTE 6.3 §4.4.17.1) requires `/`, but real-world archives violate this routinely. We `replace("\\", "/")` BEFORE running the traversal/absolute-path checks so legitimate Windows-zipped skill packages install cleanly. The same `..` / absolute / empty-part rules then fire on the normalized path — traversal defense is unchanged.

**Traversal errors include the offending entry name.** Both `absolute paths` and `path traversal` ValueErrors carry the raw `member_name` in their message (e.g. `(offending entry: '../../etc/evil.txt')`). This is so prod operators reading rejection logs can immediately see which zip entry was bad without needing the raw archive.

## Gotchas

**`sanitize_filename` strips the directory component, not just traversal dots.** A filename like `subdir/file.txt` has `Path("subdir/file.txt").name == "file.txt"`, which does not equal the original, so it is rejected with "path traversal not allowed". Filenames must be plain names without any directory separators, even legitimate ones. (Note: `sanitize_filename` still blanket-rejects `\` in its input because it validates a single-segment filename, not a zip path. Only `validate_zip_member_path` normalizes backslashes — those two callers have different semantics on purpose.)

**`enforce_max_bytes` takes the size in bytes, not the file object.** The caller must determine the size before calling this function (e.g., `len(content)` or from a `Content-Length` header). There is no streaming check.

**Windows drive-letter paths are still relative after normalization.** A zip entry like `C:\foo\bar` normalizes to `C:/foo/bar`, which `PurePosixPath.is_absolute()` reports `False`. In practice, Windows-created skill zips don't contain drive-letter paths (the zip stores paths relative to the source), so this is a non-issue — but if it ever appears, the entry would be extracted into a `C:` subdirectory under the target. Not a security risk (still confined), just ugly.
