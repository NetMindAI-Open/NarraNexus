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

decrypt() outcomes:
- Fernet token this key can open → plaintext (normal).
- legacy plain-base64 (pre-marketplace format) → decoded plaintext, flagged
  for rewrite.
- a Fernet-SHAPED token this key CANNOT open (the key rotated or was lost) →
  raises SecretDecryptError. It FAILS CLOSED — returning the ciphertext let a
  skill run with it as its credential and fail opaquely downstream (2026-08-01).
- anything else (a genuinely plain, non-token value) → returned unchanged,
  rather than destroying a value we can read.
"""

import base64
import binascii
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from narranexus.platform.settings import settings

_ENV_KEY_NAME = "SKILL_SECRETS_KEY"
_KEY_FILENAME = "skill_secrets.key"


class SecretDecryptError(Exception):
    """A stored secret looks like a Fernet token but this key cannot open it.

    Raised (not swallowed) so callers FAIL CLOSED: the value is ciphertext
    encrypted under a key that rotated or was lost, and returning it would let
    a skill run with ciphertext as its credential and fail opaquely downstream
    (the 2026-08-01 incident). Callers skip the affected var and surface a
    re-enter-credential prompt instead.
    """


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
        # Neither a Fernet token this key can open NOR legacy base64. A value
        # SHAPED like a Fernet token (gAAAA…) that we can't open means the key
        # was rotated/lost (container rebuilt without SKILL_SECRETS_KEY and the
        # file key gone). FAIL CLOSED — raise (instead of returning the raw
        # ciphertext) so a skill never runs with ciphertext as its credential
        # (2026-08-01 incident). Deliberately does NOT log: decrypt() runs on
        # every skill scan / status query now, and a per-scan ERROR with no
        # skill/var context would drown out the ONE loud, contextful ERROR the
        # injection path emits (get_all_skill_env_vars) — the ops signal.
        if value.startswith(self.TOKEN_PREFIX):
            raise SecretDecryptError(
                "stored secret is a Fernet token this key cannot decrypt"
            )
        # A genuinely plain, non-token value (e.g. someone stored plaintext) —
        # pass it through unchanged rather than destroying a value we can read.
        return value

    def encrypt_env_config(self, env: Dict[str, str]) -> Dict[str, str]:
        return {k: self.encrypt(v) for k, v in env.items()}

    def decrypt_env_config(
        self, env: Dict[str, str]
    ) -> Tuple[Dict[str, str], bool, List[str]]:
        """Return (plaintext dict, needs_rewrite, failed_keys).

        - plaintext dict: only the values that DECRYPTED — an undecryptable
          value is never placed here (no ciphertext leaks to the caller).
        - needs_rewrite: True when any value was stored in a pre-Fernet format
          — the caller should re-persist the encrypted form.
        - failed_keys: var names whose stored value is unusable (ciphertext
          this key cannot open, or a corrupt non-string entry); the caller
          skips them and prompts a re-enter.

        TOTAL by contract: both callers (the status-query helper
        ``configured_env_var_names`` and the injection path
        ``get_all_skill_env_vars``) feed this a raw, agent-writable
        ``env_config``. A malformed meta must NOT crash them — a non-dict
        ``env`` yields empty results, and a non-string value is reported as
        ``failed`` (fail-closed) rather than raising deep in ``decrypt``.
        """
        plain: Dict[str, str] = {}
        needs_rewrite = False
        failed: List[str] = []
        if not isinstance(env, dict):
            return plain, needs_rewrite, failed
        for key, value in env.items():
            if not isinstance(value, str):
                failed.append(key)  # corrupt meta → unusable cred → skip it
                continue
            if not value:
                continue  # blank → simply absent, not injected, not an error
            try:
                plain[key] = self.decrypt(value)
            except SecretDecryptError:
                failed.append(key)
                continue
            if not value.startswith(self.TOKEN_PREFIX):
                needs_rewrite = True
        return plain, needs_rewrite, failed


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
