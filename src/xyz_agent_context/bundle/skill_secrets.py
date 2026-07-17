"""
@file_name: skill_secrets.py
@author: NetMind.AI
@date: 2026-07-13
@description: Skill-secret scrubbing for bundle export (opt-in gating).

A skill can carry auth in two on-disk shapes under its ``skills/<dir>/`` folder:
- **env-var creds** in ``.skill_meta.json``'s ``env_config`` (base64 values,
  injected as env vars at runtime);
- **file creds** (``credentials.json``, ``*_token*`` …) that the sensitive-path
  filter already recognises.

Unless the exporter opts into ``include_skill_secrets`` (the "full mode"
companion of ``include_channel_credentials``), skill secrets must NOT leave
silently. This module is the single place that knows how to strip them:

- ``scrub_skill_meta`` blanks the ``env_config`` VALUES (keeps the keys +
  ``requires`` + ``study_result`` so the imported skill still knows which env
  vars it needs — just not their secrets).
- the workspace packer and the full_copy zipper both route ``.skill_meta.json``
  through it, and drop sensitive files, when secrets are not opted in.

(A broader ``portable_secrets`` abstraction unifying channel + skill secret
export/import is a future refactor — see the plan doc; for now channel and skill
each own their concrete gating.)
"""

import json
from pathlib import Path
from typing import Optional

SKILL_META_FILENAME = ".skill_meta.json"


def dir_is_builtin(skill_dir: Path) -> bool:
    """True if a skill directory is a built-in (shipped with the app).

    Built-in skills are re-materialized from the repo on every run and tagged
    ``builtin: true`` in their ``.skill_meta.json``, so they must never be
    treated as user data to back up or export. Single source of truth for both
    the backup path (``skill_backup``) and the export builder; ``skill_module``
    imports it too so the flag semantics can't drift across the three callers.
    """
    meta_file = skill_dir / SKILL_META_FILENAME
    if not meta_file.exists():
        return False
    try:
        return bool(json.loads(meta_file.read_text(encoding="utf-8")).get("builtin"))
    except Exception:
        return False


def scrub_skill_meta(raw: str) -> Optional[str]:
    """Blank the ``env_config`` values in a ``.skill_meta.json`` payload.

    Keeps every other field (including the ``env_config`` KEYS, so the import
    side still shows which vars need reconfiguring) and returns the scrubbed
    JSON text. Returns ``None`` when there is nothing to scrub (unparseable, or
    no non-empty ``env_config``) so the caller can keep the original bytes.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    env = data.get("env_config")
    if not isinstance(env, dict) or not any(env.values()):
        return None
    data["env_config"] = {k: "" for k in env}
    return json.dumps(data, indent=2, ensure_ascii=False)
