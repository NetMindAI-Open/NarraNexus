"""
@file_name: cli_binary.py
@author: NarraNexus
@date: 2026-07-29
@description: Decide WHICH `claude` binary the agent loop launches, and say so
              out loud.

Why this module exists
----------------------
``claude-agent-sdk`` ships a complete CLI executable inside its own wheel
(``claude_agent_sdk/_bundled/claude``, ~186 MB) and ``_find_cli()`` returns it
BEFORE ever consulting ``PATH``. So the binary that actually sends every
request is pinned by the pip dependency, not by whatever ``npm install -g
@anthropic-ai/claude-code`` put on the machine. Nothing logged this, and the
npm-installed copy is a decoy: ``claude --version`` in a terminal answers for a
binary the agent loop never launches. During the 2026-07-29 investigation that
decoy produced two wrong version attributions in a row before the request
body's own ``cc_version=`` header settled it.

That mattered because the two versions do not behave the same. Measured on this
repo's real HTTP MCP servers (experiments E3 / E3b / E3c, 8 servers with
randomized handshake completion order):

  * SDK 0.1.43's bundled **2.1.56** does no normalization at all — the request's
    ``tools`` array is the concatenation of each server's ``tools/list`` in
    handshake-completion order, so it permutes on EVERY run (4 rounds → 4
    distinct order hashes), including on the ``--resume`` path. Since ``tools``
    precedes ``system`` in the cache prefix, one moved block voids the entire
    prefix behind it — our whole system prompt included.
  * **2.1.220** normalizes ``tools`` to strict alphabetical order. Same hostile
    setup, 4 rounds → one order hash, one bytes hash, and byte-identical
    prefixes across consecutive resume rounds.

The cheap fix is therefore to keep the SDK and hand it a newer binary via
``ClaudeAgentOptions(cli_path=...)`` — which short-circuits ``_find_cli()``
(``subprocess_cli.py:46``). E3b verified the pairing empirically: SDK 0.1.43
drives CLI 2.1.220 over the stdio stream-json protocol with no incompatibility,
across an 83-version gap.

Design rules this module follows
--------------------------------
1. **Fail-open, always.** Every uncertainty (no external binary, version
   mismatch, ``--version`` fails or hangs, explicit path missing) returns
   ``None``, which makes the SDK fall back to its bundled binary — i.e. exactly
   today's behavior. A CLI-selection problem must never block an agent run
   (铁律 #14).
2. **Version must be verified, not assumed.** The pin is checked by executing
   the candidate, because that is the only source that cannot lie about itself.
   A path being present says nothing about which version lives there.
3. **Resolve once per process.** The check spawns a subprocess; doing that per
   turn would be a real cost on a hot loop. The decision is cached behind a
   lock and logged exactly once, so the log answers "which binary is this
   process actually using" without being noise.

The pin lives in ``PINNED_CLI_VERSION`` below and is mirrored, as a literal,
into ``run.sh`` and ``docker/Dockerfile.manyfold``. ``tests/agent_framework/
test_claude_cli_pin.py`` asserts the three agree — the same
multiple-anchors-plus-a-check pattern CLAUDE.md already uses for the five
release version anchors.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

from loguru import logger

# Canonical pin. Mirrored as a literal in run.sh and docker/Dockerfile.manyfold;
# test_claude_cli_pin.py fails if they drift apart.
#
# Bumping this is a behavioral change, not a chore: re-run experiments E3 and
# E3c against the new version before moving it. The value 2.1.220 is the one
# both were run on.
#
# MANUAL VERIFICATION, no CI guard: the transcript format is also accepted by
# the SDK's bundled 2.1.56 (E4 re-run with --bundled), which is what lets an
# un-rebuilt cloud image keep working — it just gets no cache benefit, since
# 2.1.56 reshuffles the tools array. That claim needs a real CLI binary, so it
# cannot be a unit test; re-check it by hand when either version moves.
# Probes: reference/self_notebook/experiments/e3*.py, e4_synthetic_transcript_probe.py
PINNED_CLI_VERSION = "2.1.220"

# `claude --version` prints e.g. "2.1.220 (Claude Code)".
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

# A healthy `--version` is a few hundred milliseconds. This bound exists so a
# wedged binary degrades to "use the bundled one" instead of stalling process
# startup; it is not a correctness knob.
_VERSION_PROBE_TIMEOUT_S = 20.0

_lock = threading.Lock()
# (path_or_None, effective_version_or_None, reason)
_resolved: tuple[str | None, str | None, str] | None = None


def _probe_version(path: str) -> str | None:
    """Return the semver a binary reports, or None if it cannot be asked.

    Deliberately swallows everything: this runs on the startup path of every
    agent turn's process and its failure mode is "fall back to bundled", never
    an exception escaping into the loop.
    """
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001 — fail-open by design
        logger.warning(f"[CLAUDE-CLI] `{path} --version` failed: {e}")
        return None
    m = _VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
    return m.group(1) if m else None


def _bundled_path() -> Path | None:
    """Where the SDK's own binary lives, for logging the fallback honestly."""
    try:
        import claude_agent_sdk

        p = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        return p if p.exists() else None
    except Exception:  # noqa: BLE001 — diagnostic only
        return None


def _bundled_declared_version() -> str:
    try:
        from claude_agent_sdk import _cli_version

        return _cli_version.__cli_version__
    except Exception:  # noqa: BLE001 — diagnostic only
        return "unknown"


def _decide() -> tuple[str | None, str | None, str]:
    """Compute (cli_path_to_pass, its version, human reason).

    A None path means "let the SDK use its bundled binary".
    """
    from xyz_agent_context.settings import settings

    if not settings.claude_cli_prefer_pinned:
        return None, None, "gate off (CLAUDE_CLI_PREFER_PINNED=false)"

    explicit = (settings.claude_cli_path or "").strip()
    if explicit:
        # An operator naming a path outranks the pin — but a path that does not
        # exist is a configuration error we must not silently honour, so it
        # still falls back rather than handing the SDK a bad path (which would
        # surface as CLINotFoundError mid-turn).
        if not Path(explicit).is_file():
            return None, None, f"CLAUDE_CLI_PATH={explicit!r} is not a file"
        return explicit, _probe_version(explicit), "explicit CLAUDE_CLI_PATH"

    found = shutil.which("claude")
    if not found:
        return None, None, "no `claude` on PATH"

    ver = _probe_version(found)
    if ver is None:
        return None, None, f"`{found}` version unreadable"
    if ver != PINNED_CLI_VERSION:
        # The whole point of the pin: an unverified version is worse than the
        # known-quantity bundled one, because `run.sh` installs the npm package
        # UNPINNED and the cloud image pins a different version again. Silently
        # accepting whatever is on PATH would make request bytes depend on when
        # the machine happened to be provisioned.
        return None, None, f"`{found}` reports {ver}, pin is {PINNED_CLI_VERSION}"
    return found, ver, f"PATH binary matches pin {PINNED_CLI_VERSION}"


def resolve_cli_path() -> str | None:
    """Path to pass as ``ClaudeAgentOptions.cli_path``, or None for the bundled.

    Cached and logged once per process. The log line is the answer to "which
    binary is this process really launching" — read it instead of running
    ``claude --version``, which reports a binary the agent loop may never use.
    """
    return _resolve()[0]


def effective_cli_version() -> str | None:
    """Version of the binary this process actually launches, or None if it could
    not be determined.

    Distinct from ``PINNED_CLI_VERSION`` on purpose: when the resolver falls
    back, the pin is NOT what runs. Consumers that record the running version —
    the per-turn transcript stamps it into every record's ``version`` field —
    must write what is actually running, not what we hoped for.
    """
    return _resolve()[1]


def _resolve() -> tuple[str | None, str | None, str]:
    """Resolve once per process, log once, and memoize the whole decision."""
    global _resolved
    if _resolved is not None:
        return _resolved
    with _lock:
        if _resolved is not None:
            return _resolved
        path, version, reason = _decide()
        if path is None:
            bundled = _bundled_path()
            effective = str(bundled) if bundled else "<not found>"
            version = _probe_version(effective) if bundled else None
            reason = f"{reason} → SDK bundled"
        else:
            effective = path
        logger.info(
            f"[CLAUDE-CLI] effective binary={effective} "
            f"version={version or 'unknown'} "
            f"| decision: {reason} "
            f"| sdk={_bundled_declared_version()} pin={PINNED_CLI_VERSION}"
        )
        _resolved = (path, version, reason)
        return _resolved


def reset_cache_for_tests() -> None:
    """Drop the memoized decision. Tests only — the cache is per-process state
    and every test that changes settings must be able to re-resolve."""
    global _resolved
    with _lock:
        _resolved = None
