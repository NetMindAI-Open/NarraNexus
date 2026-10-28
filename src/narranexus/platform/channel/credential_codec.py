"""
@file_name: credential_codec.py
@author: Bin Liang
@date: 2026-09-04
@description: Encrypt / decrypt the secret half of a generic channel credential (``channel_credentials.secret_json``).

Reuses the marketplace's Fernet ``SecretBox`` (env key on cloud, a
per-install key file locally) so channel secrets get real encryption at
rest instead of the per-channel base64 the old tables used. The box is
loaded lazily and can be pointed at a temporary key dir in tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

_BOX: Any = None
_KEY_DIR: Optional[Path] = None


def use_key_dir(key_dir: Optional[Path]) -> None:
    """Tests: load the box from ``key_dir`` (None restores the default lookup)."""
    global _BOX, _KEY_DIR
    _KEY_DIR = key_dir
    _BOX = None


def _box():
    global _BOX
    if _BOX is None:
        from narranexus.platform.marketplace._skill_marketplace_impl.secret_box import SecretBox

        _BOX = SecretBox.load(_KEY_DIR)
    return _BOX


def encode_secrets(secrets: dict[str, Any]) -> str:
    if not secrets:
        return ""
    return _box().encrypt(json.dumps(secrets, sort_keys=True, default=str))


def decode_secrets(value: str) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(_box().decrypt(value))
    return dict(loaded) if isinstance(loaded, dict) else {}


__all__ = ["decode_secrets", "encode_secrets", "use_key_dir"]
