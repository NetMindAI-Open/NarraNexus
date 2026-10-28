"""
@file_name: launcher.py
@author:
@date: 2026-09-22
@description: Start the local Chromium and find its CDP endpoint.

The argv built here is a security surface, not a formatting detail:

* the debugging port is bound to **loopback only**. An unbound one is remote
  code execution on the user's machine for anyone on the same network;
* **web security stays on**. Disabling it would let any page the agent visits
  read every other origin the profile holds cookies for — which is the whole
  value of the inherited login state, handed to whoever the agent browses to;
* the profile directory is explicit and per-profile, so logins persist across
  sessions and two identities never share a cookie jar.

The browser is **headed by default**. That is the ego-lite lesson from the
2026-09-21 spike, paid for directly: screencast needs a compositor, and a
browser that never paints emits exactly one frame and then nothing — a failure
indistinguishable from "the page is not changing".

Endpoint discovery retries. The port accepts connections before the browser
can answer on it, so a single attempt is flaky in precisely the way that
yields a blank panel on a slow machine.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

#: Anything outside this becomes "default" — a profile name arrives from
#: config and must never steer the browser's data dir somewhere else.
_SAFE_PROFILE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_launch_args(
    *,
    executable: Path,
    port: int,
    profile: Path,
    headless: bool = False,
    extra: Optional[Sequence[str]] = None,
) -> list[str]:
    """argv for one browser process.

    Args:
        executable: The browser binary.
        port: CDP port, bound to loopback.
        profile: ``--user-data-dir``; where the login state lives.
        headless: Opt-in only — see module docstring on why headed is default.
        extra: Appended verbatim, never replacing the flags above.
    """
    args = [
        str(executable),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # Quieter startup; none of these weaken the sandbox or web security.
        "--disable-background-networking",
        "--disable-breakpad",
        "--disable-sync",
        "--metrics-recording-only",
    ]
    if headless:
        args.append("--headless=new")
    if extra:
        args.extend(extra)
    return args


def pick_profile_dir(*, root: Path, profile: str, agent_id: str = "") -> Path:
    """Directory for one agent's login identity, confined to ``root``.

    Namespaced **per agent**, not just per profile name. Chromium takes a
    ``SingletonLock`` on its ``--user-data-dir``, so two agents pointed at one
    directory means the second browser simply refuses to start — and the error
    that surfaces ("no CDP endpoint within 30s") points nowhere near the
    cause. Two agents both on the default profile is the normal case, not an
    exotic one.

    A name that is not a plain identifier is replaced rather than sanitised:
    silently rewriting ``../../etc`` into something similar-looking would make
    two different configured profiles collide without saying so.
    """
    name = (profile or "").strip()
    if not _SAFE_PROFILE.match(name):
        name = "default"
    who = (agent_id or "").strip()
    if not _SAFE_PROFILE.match(who):
        who = "shared"
    return root / "profiles" / who / name


async def wait_for_endpoint(
    *,
    probe: Callable[[], Awaitable[str]],
    timeout: float = 30.0,
    interval: float = 0.25,
) -> str:
    """Poll until the browser answers with its CDP websocket URL.

    Raises:
        TimeoutError: carrying the LAST probe error, because "timed out" alone
            tells whoever reads the log nothing about why.
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    while True:
        try:
            return await probe()
        except Exception as exc:  # noqa: BLE001 - the reason is re-raised below
            last_error = exc
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"browser did not expose a CDP endpoint within {timeout}s: {last_error}"
            )
        await asyncio.sleep(interval)
