"""
@file_name: narra_cli_client.py
@date: 2026-07-20
@description: The single spawn choke point for the local ``narra-cli`` binary.

NarraMessenger's outbound / query surface (send, history, media, speech,
explore) is delegated to ``@narra-im/narra-cli`` (installed locally by run.sh /
Docker). This wrapper turns the CLI into one async call and owns the three
platform concerns the CLI itself does not:

  1. **Binary resolution** — a stripped MCP-subprocess PATH cannot see a
     locally-installed CLI (or its ``env node`` shebang), so we resolve an
     absolute path from ``NARRA_CLI_BIN`` (set by run.sh / Docker) → PATH →
     node-bin discovery, and rebuild the child's PATH. Same class as lark #53.
  2. **Token injection** — narra-cli takes the bearer only via ``--token`` /
     ``--token-file`` (no env, no stdin). We write the DB bearer to an EPHEMERAL
     ``--token-file`` (system tmp, ``chmod 600``, ``unlink`` in ``finally``) so
     the token never lands on argv (``ps`` / ``/proc``), never persists, and
     never sits in the agent's Read-sandbox workspace. See the design doc's
     security model.
  3. **CWD = agent workspace** — narra-cli writes ``--output`` / media downloads
     at default-relative paths; pointing CWD at the agent's workspace lands them
     in the agent's Read sandbox (same P0 fix as lark 2026-05-28).

Independent per binding rule #3 (no cross-module imports); mirrors
``lark_module/lark_cli_client.py`` in shape but is far thinner — narra-cli takes
the token as a flag (no per-agent config hydration / HOME override needed).
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

# =============================================================================
# narra-cli executable resolution (same class as lark issue #53)
# =============================================================================
_NARRA_CLI_BIN: Optional[str] = None
_NARRA_EXTRA_PATH: Optional[tuple[str, ...]] = None

# Locations WE install narra-cli to (run.sh → ~/.narranexus, Docker → /opt).
# Checked before PATH so a stale global install can never shadow ours.
_MANAGED_INSTALL_BINS: tuple[str, ...] = (
    str(Path.home() / ".narranexus" / "narra-cli" / "node_modules" / ".bin" / "narra-cli"),
    "/opt/narra-cli/node_modules/.bin/narra-cli",
)


def _discover_node_bin_dirs() -> tuple[str, ...]:
    """Best-effort dirs holding the local narra-cli bin + the node binary."""
    dirs: list[str] = []
    for tool in ("narra-cli", "npm", "node"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).resolve().parent))

    home = Path.home()
    dirs += [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/opt/narra-cli/node_modules/.bin",  # Docker install prefix
        str(home / ".narranexus" / "narra-cli" / "node_modules" / ".bin"),
    ]
    dirs += sorted(glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin")))

    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return tuple(out)


def _resolve_narra_cli() -> tuple[str, tuple[str, ...]]:
    """Return ``(narra_cli_executable, extra_PATH_dirs)``, memoised on success.

    Resolution order: explicit ``NARRA_CLI_BIN`` (set by run.sh / Docker) →
    current PATH → an augmented PATH from :func:`_discover_node_bin_dirs`. When
    nothing resolves we return the bare name ``"narra-cli"`` WITHOUT memoising,
    so a retry after a mid-session install re-discovers it.
    """
    global _NARRA_CLI_BIN, _NARRA_EXTRA_PATH
    if _NARRA_CLI_BIN is not None:
        return _NARRA_CLI_BIN, _NARRA_EXTRA_PATH or ()

    env_bin = os.environ.get("NARRA_CLI_BIN", "").strip()
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        _NARRA_CLI_BIN = env_bin
        _NARRA_EXTRA_PATH = (str(Path(env_bin).resolve().parent),)
        return _NARRA_CLI_BIN, _NARRA_EXTRA_PATH

    # Our MANAGED install locations (run.sh → ~/.narranexus, Docker → /opt)
    # take precedence over PATH: a stale global ``narra-cli`` (e.g. an old
    # ``npm i -g``) must NOT shadow the version we install and track.
    for managed in _MANAGED_INSTALL_BINS:
        if os.path.isfile(managed) and os.access(managed, os.X_OK):
            _NARRA_CLI_BIN = managed
            _NARRA_EXTRA_PATH = (str(Path(managed).resolve().parent),)
            return _NARRA_CLI_BIN, _NARRA_EXTRA_PATH

    on_path = shutil.which("narra-cli")
    if on_path:
        _NARRA_CLI_BIN = on_path
        _NARRA_EXTRA_PATH = (str(Path(on_path).resolve().parent),)
        return _NARRA_CLI_BIN, _NARRA_EXTRA_PATH

    extra = _discover_node_bin_dirs()
    for d in extra:
        cand = os.path.join(d, "narra-cli")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            _NARRA_CLI_BIN = cand
            _NARRA_EXTRA_PATH = extra
            return _NARRA_CLI_BIN, _NARRA_EXTRA_PATH

    # Not found — return bare name without memoising (retry re-discovers).
    return "narra-cli", extra


# =============================================================================
# narra-cli HOME — a chmod-able config-dir home
# =============================================================================
_NARRA_HOME: Optional[str] = None


def _narra_cli_home() -> str:
    """Return a process-owned, writable HOME for narra-cli, created once.

    narra-cli's ``ConfigStore`` always ``chmod``s ``$HOME/.narra-cli`` (0700) at
    startup; the container's real HOME is on a mount the server user cannot
    chmod, so we redirect HOME to a dir under the system tmpdir that the process
    owns. Shared across calls/agents is fine — the only thing narra-cli stores
    there is the (default, prod) endpoint config; the per-call TOKEN goes via
    ``--token-file``, never into this dir.
    """
    global _NARRA_HOME
    if _NARRA_HOME is not None:
        return _NARRA_HOME
    # Per-uid path + 0700, and verify WE own it. On a shared host (bash run.sh
    # mode, 铁律 #7) a co-tenant could pre-squat a fixed path: exist_ok=True would
    # silently pass and narra-cli would then chmod a dir it doesn't own —
    # reproducing this very EPERM — while a default-umask 0755 would leak the
    # endpoint config to other users. If the path isn't ours, fall back to an
    # unpredictable private dir (mkdtemp is 0700 and unique).
    base = os.path.join(tempfile.gettempdir(), f"narra-cli-home-{os.getuid()}")
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        if os.stat(base).st_uid == os.getuid():
            os.chmod(base, 0o700)  # tighten even if it pre-existed with looser bits
            _NARRA_HOME = base
            return _NARRA_HOME
    except OSError:
        pass
    _NARRA_HOME = tempfile.mkdtemp(prefix="narra-cli-home-")
    return _NARRA_HOME


# =============================================================================
# Agent workspace CWD (same P0 fix as lark 2026-05-28)
# =============================================================================
_agent_user_id_cache: dict[str, str] = {}


async def _resolve_agent_workspace_cwd(agent_id: str, db) -> Optional[Path]:
    """Return the agent's workspace dir for the subprocess CWD, or None.

    None means "inherit parent CWD" — tolerable for send/query, wrong only for
    downloads (which then land outside the agent's Read sandbox).
    """
    user_id = _agent_user_id_cache.get(agent_id)
    if user_id is None:
        try:
            row = await db.get_one("agents", {"agent_id": agent_id})
            if not row or not row.get("created_by"):
                return None
            user_id = row["created_by"]
            _agent_user_id_cache[agent_id] = user_id
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[narra-cli] user_id resolve failed for {agent_id}: {e}")
            return None
    try:
        from xyz_agent_context.utils.attachment_storage import get_workspace_path
        ws = get_workspace_path(agent_id, user_id)
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[narra-cli] workspace resolve failed for {agent_id}: {e}")
        return None


# =============================================================================
# Runtime client — one narra-cli invocation, token injected per call.
# =============================================================================
class NarraCliClient:
    """Runs one narra-cli command with the bearer injected via ephemeral file."""

    def __init__(self, bearer_token: str) -> None:
        self._bearer = bearer_token

    async def run(
        self,
        command_args: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: float = 120.0,
    ) -> dict:
        """Spawn narra-cli with ``command_args`` + injected ``--token-file``.

        Returns a normalized dict:
          - ``{"success": True, "data": <envelope.data>, "raw": <envelope>}``
          - ``{"success": False, "error": <issue code>, "issues": [...], "raw": ...}``
          - file-writing commands (``speech synthesize --out``,
            ``im attachments download --output``) may emit empty stdout on
            success; that + a zero exit code is treated as ``{"success": True}``.
        """
        tok_path = self._write_token_file()
        try:
            resolved, extra_path = _resolve_narra_cli()
            cmd = [resolved, *command_args, "--token-file", tok_path]
            env = dict(os.environ)
            if extra_path:
                env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])
            # narra-cli's ConfigStore.ensurePrivateDir unconditionally
            # `chmod`s ``$HOME/.narra-cli`` on startup. In the MCP container the
            # server runs as a non-root user whose real $HOME (/home/app) is on a
            # mount it cannot chmod → ``EPERM: chmod '/home/app/.narra-cli'`` on
            # EVERY call (dev 2026-07-20; only surfaced with a strong model that
            # actually ran the command). Point HOME at a dir the process owns so
            # the chmod succeeds.
            env["HOME"] = _narra_cli_home()

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=str(cwd) if cwd is not None else None,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
                return {"success": False, "error": "timeout",
                        "message": f"narra-cli timed out after {timeout}s"}
            except FileNotFoundError:
                return {"success": False, "error": "narra_cli_not_found",
                        "message": "narra-cli binary not found (install / PATH issue)"}

            return self._parse_envelope(stdout, stderr, proc.returncode)
        finally:
            try:
                os.unlink(tok_path)
            except OSError:
                pass

    def _write_token_file(self) -> str:
        """Write the bearer to a 600 temp file OUTSIDE the agent sandbox.

        System tmp lives in the MCP process's container, unreachable by the
        agent's workspace-sandboxed Read tool. Caller unlinks in ``finally``.
        """
        fd, path = tempfile.mkstemp(prefix="narra-tok-", suffix=".txt")
        try:
            os.write(fd, self._bearer.encode())
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
        return path

    @staticmethod
    def _parse_envelope(stdout: bytes, stderr: bytes, returncode: int) -> dict:
        text = (stdout or b"").decode(errors="replace").strip()
        if not text:
            # A file-writing command (speech synthesize --out / im attachments
            # download --output) can succeed with empty stdout — trust the exit
            # code rather than misreporting a written file as a failure.
            if returncode == 0:
                return {"success": True}
            return {"success": False, "error": "empty_output",
                    "message": (stderr or b"").decode(errors="replace").strip()}
        try:
            envelope = json.loads(text)
        except (ValueError, TypeError):
            return {"success": False, "error": "cli_parse_error",
                    "message": text[:500]}
        if not isinstance(envelope, dict):
            return {"success": False, "error": "cli_parse_error", "message": text[:500]}

        if envelope.get("status") == "ok":
            return {"success": True, "data": envelope.get("data"), "raw": envelope}

        issues = envelope.get("issues") or []
        code = ""
        if issues and isinstance(issues[0], dict):
            code = issues[0].get("code", "") or ""
        return {"success": False, "error": code or "cli_error",
                "issues": issues, "raw": envelope}


async def run_narra_cli(
    agent_id: str,
    command_args: list[str],
    *,
    db,
    timeout: float = 120.0,
) -> dict:
    """Resolve the agent's bearer + workspace, then run a narra-cli command.

    Returns the normalized :meth:`NarraCliClient.run` dict, or a
    ``no_credential`` error if the agent has no NarraMessenger binding.

    Single-backend (prod) assumption: we inject the per-agent bearer, but the
    ENDPOINT narra-cli talks to is its global config (``~/.narra-cli/config.json``,
    default ``https://api.netmind.chat``), NOT ``cred.backend_base_url``. Every
    binding is expected to use the same hosted backend as that global config.
    A per-agent endpoint (e.g. an api-test binding on a prod-configured host)
    would need a per-agent HOME override with its own config.json — deliberately
    NOT built yet; ``cred.backend_base_url`` is only used here to sanity-warn.
    """
    from ._narramessenger_credential_manager import NarramessengerCredentialManager

    cred = await NarramessengerCredentialManager(db).get(agent_id)
    if not cred or not getattr(cred, "bearer_token", ""):
        return {"success": False, "error": "no_credential",
                "message": "no NarraMessenger binding for this agent"}

    cwd = await _resolve_agent_workspace_cwd(agent_id, db)
    client = NarraCliClient(cred.bearer_token)
    return await client.run(command_args, cwd=cwd, timeout=timeout)
