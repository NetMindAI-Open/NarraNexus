"""
@file_name: secret_box.py
@author: NetMind.AI
@date: 2026-07-20
@description: Fernet encryption for skill env_config secrets, with lazy
migration of legacy base64-only values.

Key resolution order:
1. SKILL_SECRETS_KEY env var (cloud deployments inject it; must be a valid
   Fernet key). Invalid values fail fast so a misconfigured pod is loud.
2. Key file <key_dir>/skill_secrets.key, generated on first use with 0600
   perms (local/desktop; single-user machine, OS user is the boundary).

decrypt() accepts three shapes so old .skill_meta.json files keep working:
Fernet token (normal), legacy plain-base64 (pre-marketplace format, decoded
and flagged for rewrite), anything else (returned unchanged rather than
destroying a value we cannot interpret).
"""

import base64
import binascii
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from xyz_agent_context.settings import settings

_ENV_KEY_NAME = "SKILL_SECRETS_KEY"
_KEY_FILENAME = "skill_secrets.key"


def _default_key_dir() -> Path:
    # Keep the key file UNDER base_working_path (the mounted volume in cloud
    # compose — /opt/narranexus/workspaces), not beside it (/opt/narranexus,
    # which is NOT mounted and is lost on container rebuild). A dot-prefixed
    # dir so it never looks like an agent workspace. Cloud multi-pod should
    # still set SKILL_SECRETS_KEY (a per-pod file key can't cross pods) — see
    # .env.cloud.example — but this makes the file fallback survive a rebuild
    # on single-pod deploys instead of silently rotating the key.
    return Path(settings.base_working_path) / ".secrets"


class SecretBox:
    """Symmetric encryption for skill credential values."""

    # Fernet tokens always start with the version byte 0x80, base64url "gAAAA".
    TOKEN_PREFIX = "gAAAA"

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def load(cls, key_dir: Optional[Path] = None) -> "SecretBox":
        env_key = os.environ.get(_ENV_KEY_NAME)
        if env_key:
            try:
                return cls(env_key.encode("ascii"))
            except (ValueError, binascii.Error) as exc:
                raise ValueError(
                    f"{_ENV_KEY_NAME} is set but is not a valid Fernet key"
                ) from exc

        directory = Path(key_dir) if key_dir else _default_key_dir()
        key_file = directory / _KEY_FILENAME
        if key_file.exists():
            return cls(key_file.read_bytes().strip())

        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        key = Fernet.generate_key()
        key_file.touch(mode=0o600)
        key_file.write_bytes(key)
        os.chmod(key_file, 0o600)
        logger.info(f"SecretBox: generated new key at {key_file}")
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError):
            pass
        try:
            return base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            pass
        # Neither a Fernet token this key can open NOR legacy base64. Most
        # likely the key was rotated/lost (e.g. container rebuilt without
        # SKILL_SECRETS_KEY and the file key gone). Returning the raw value
        # would let a skill run with ciphertext as its "credential" and fail
        # opaquely downstream — LOUDLY log so it's diagnosable, not silent.
        if value.startswith(self.TOKEN_PREFIX):
            logger.error(
                "SecretBox: cannot decrypt a stored secret — the encryption "
                "key appears to have changed or been lost. Re-enter the "
                "affected skill credential (or set a stable SKILL_SECRETS_KEY)."
            )
        return value

    def encrypt_env_config(self, env: Dict[str, str]) -> Dict[str, str]:
        return {k: self.encrypt(v) for k, v in env.items()}

    def decrypt_env_config(self, env: Dict[str, str]) -> Tuple[Dict[str, str], bool]:
        """Return (plaintext dict, needs_rewrite).

        needs_rewrite is True when any value was stored in a pre-Fernet
        format — the caller should re-persist the encrypted form.
        """
        plain: Dict[str, str] = {}
        needs_rewrite = False
        for key, value in env.items():
            plain[key] = self.decrypt(value)
            if not value.startswith(self.TOKEN_PREFIX):
                needs_rewrite = True
        return plain, needs_rewrite


_default_box: Optional[SecretBox] = None


def get_secret_box() -> SecretBox:
    """Process-wide SecretBox using the default key resolution.

    Cached so the key file is read once per process. Tests that repoint
    settings.base_working_path must also reset ``_default_box`` to None.
    """
    global _default_box
    if _default_box is None:
        _default_box = SecretBox.load()
    return _default_box
