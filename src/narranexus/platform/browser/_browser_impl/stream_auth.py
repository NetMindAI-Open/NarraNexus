"""
@file_name: stream_auth.py
@author:
@date: 2026-09-22
@description: Agent-bound credentials for the backend-to-host stream.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
import time
from pathlib import Path

from narranexus.kernel.deployment import is_cloud_mode

HEADER = "x-narranexus-browser-stream"


def _secret() -> bytes:
    configured = os.environ.get("NARRANEXUS_BROWSER_STREAM_SECRET", "")
    if configured:
        if len(configured) < 32:
            raise RuntimeError("NARRANEXUS_BROWSER_STREAM_SECRET must contain at least 32 characters")
        return configured.encode()
    if is_cloud_mode():
        raise RuntimeError("NARRANEXUS_BROWSER_STREAM_SECRET is required for cloud browser streaming")
    root = Path(os.environ.get("NARRANEXUS_BROWSER_AUTH_DIR", "") or Path.home() / ".narranexus" / "browser-auth")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / "stream.key"
    if not target.exists():
        # Atomic publication prevents concurrent processes reading a partial key.
        fd, name = tempfile.mkstemp(dir=root, prefix=".stream-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(name, target)
            except FileExistsError:
                pass
        finally:
            os.unlink(name)
    value = target.read_bytes()
    if len(value) != 32:
        raise RuntimeError("invalid local browser stream credential")
    return value


def stream_token(agent_id: str) -> str:
    expires = int(time.time()) + 60
    signature = hmac.new(_secret(), f"{agent_id}\n{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_stream_token(agent_id: str, token: str) -> bool:
    try:
        expires, signature = token.split(".", 1)
        now = int(time.time())
        if not now <= int(expires) <= now + 60:
            return False
        expected = hmac.new(_secret(), f"{agent_id}\n{expires}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, OSError, RuntimeError):
        return False
