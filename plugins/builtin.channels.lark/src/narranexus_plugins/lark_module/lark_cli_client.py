"""
@file_name: lark_cli_client.py
@date: 2026-04-10
@description: Unified wrapper for all lark-cli subprocess calls.

Design
------
DB is the single source of truth for every bot credential (app_id,
app_secret, profile name, brand). The workspace (a per-agent directory
used as HOME) is a derived view that the CLI reads — if it's missing or
stale it gets rebuilt from DB before the next command runs.

Flow for any agent-scoped command:
  _run_with_agent_id(args, agent_id)
    → fetch cred from DB
    → (lazy migration) if workspace_path is empty, compute + persist it
    → _ensure_hydrated(cred): rewrite workspace/.lark-cli/config.json
      from DB via `lark-cli config init --app-secret-stdin` if stale
    → subprocess lark-cli with HOME=workspace (no --profile needed —
      each workspace has exactly one active profile)

The old shared `~/.lark-cli/config.json` + `--profile` multiplexing was
retired in favour of one workspace per agent. This mirrors how a single-
machine user runs lark-cli: one HOME, one profile, no flags.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import tempfile
import time

# Validate format of Lark identifiers (message_id, file_key, image_key)
# before they enter the resource-fetch URL. Lark's actual IDs are
# alphanumeric with underscore/hyphen prefixes (om_xxx, file_v3_xxx,
# img_xxxxx). create_subprocess_exec prevents shell injection, but a
# message_id like "om_x/../../../admin" would still construct an
# unintended URL path. Hard-gate the format here.
_LARK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# Agent workspace CWD resolution (2026-05-28 P0): lark-cli writes
# default-relative outputs to its CWD, so agent-scoped calls spawn inside
# the agent's workspace — shared implementation in data_access.
from narranexus.platform.module_system.data_access import resolve_agent_workspace_cwd


# =============================================================================
# lark-cli executable resolution (issue #53)
# =============================================================================
# A login shell has npm's global bin + the node bin on PATH; a process spawned
# by a Docker CMD, a GUI launcher (launchd), or our MCP runner frequently does
# NOT — its PATH is stripped to something like /usr/bin:/bin. That makes both
# the `lark-cli` binary AND its `#!/usr/bin/env node` shebang invisible, so the
# spawn fails with ENOENT even though lark-cli runs fine in the user's terminal
# (the exact symptom in issue #53). We therefore never trust the inherited
# PATH: we resolve lark-cli to an absolute path and rebuild the npm/node bin
# entries ourselves, memoising the result once it succeeds.
_LARK_CLI_BIN: Optional[str] = None
_LARK_EXTRA_PATH: Optional[tuple[str, ...]] = None


def _discover_node_bin_dirs() -> tuple[str, ...]:
    """Best-effort list of dirs holding npm-global bins + the node binary.

    The directory that contains ``node`` / ``npm`` is exactly where
    ``npm install -g`` drops its bin symlinks (true for vanilla, Homebrew,
    nvm and n), so resolving those tools also locates lark-cli and satisfies
    its ``env node`` shebang. Static fallbacks + version-manager globs cover
    hosts where even node/npm are off this process's PATH.
    """
    dirs: list[str] = []
    for tool in ("lark-cli", "npm", "node"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).resolve().parent))

    home = Path.home()
    dirs += [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        str(home / ".npm-global" / "bin"),
        str(home / ".npm-packages" / "bin"),
    ]
    # Version managers install node under a per-version dir that is rarely on
    # a stripped PATH; glob every installed version's bin.
    dirs += sorted(glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin")))
    dirs += sorted(glob.glob("/usr/local/n/versions/node/*/bin"))

    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return tuple(out)


def _resolve_lark_cli() -> tuple[str, tuple[str, ...]]:
    """Return ``(lark_cli_executable, extra_PATH_dirs)``, memoised on success.

    Resolution order: explicit ``LARK_CLI_BIN`` override → current PATH →
    an augmented PATH built from :func:`_discover_node_bin_dirs`. When nothing
    resolves we return the bare name ``"lark-cli"`` WITHOUT memoising, so a
    retry after the user installs lark-cli mid-session re-discovers it (the
    repro in #53 installed it while NarraNexus was already running).
    """
    global _LARK_CLI_BIN, _LARK_EXTRA_PATH
    if _LARK_CLI_BIN is not None:
        return _LARK_CLI_BIN, _LARK_EXTRA_PATH or ()

    extra = _discover_node_bin_dirs()

    override = os.environ.get("LARK_CLI_BIN")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        resolved = override
    else:
        resolved = shutil.which("lark-cli") or shutil.which(
            "lark-cli",
            path=os.pathsep.join([*extra, os.environ.get("PATH", "")]),
        )

    if resolved:
        _LARK_CLI_BIN, _LARK_EXTRA_PATH = resolved, extra
        return resolved, extra
    return "lark-cli", extra


# =============================================================================
# Unknown +shortcut translation (2026-09-09)
# =============================================================================
# Agents hallucinate +shortcuts that do not exist (``docs +get``,
# ``calendar +events-list``). lark-cli answers with a validation error and a
# "run --help" hint, which the agent cannot act on without another blind
# guess. We recognise that exact error shape, run ``lark-cli <domain> --help``
# ONCE per domain (memoised for the process; the shortcut list only changes
# with a CLI upgrade, which restarts us) and hand back the real list.
# Only +shortcuts are offered: the raw ``service.resource`` rows in the same
# help block are not what the ``+`` grammar the agent was using refers to.
# Keyed by (executable, domain): a per-agent LARK_CLI_BIN or an in-place CLI
# upgrade under a long-lived local process must not serve a stale list.
_SHORTCUT_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}
# A FAILED probe is remembered only briefly (monotonic time of the failure):
# an agent looping on the same hallucinated domain while `--help` is broken
# must not pay an extra spawn and up to _HELP_PROBE_TIMEOUT on every call,
# yet a CLI repaired mid-process is picked up again after the window.
_SHORTCUT_PROBE_FAILED_AT: dict[tuple[str, str], float] = {}
_SHORTCUT_PROBE_RETRY_SEC = 60.0
# Ceiling for the --help probe; the probe never waits longer than the call
# that triggered it.
_HELP_PROBE_TIMEOUT = 15.0
_UNKNOWN_SUBCOMMAND_RE = re.compile(
    r'unknown subcommand "(?P<sub>[^"]+)" for "lark-cli (?P<domain>[A-Za-z0-9_-]+)"'
)
_HELP_SHORTCUT_RE = re.compile(r"^\s+(\+[A-Za-z0-9][A-Za-z0-9_-]*)\s", re.M)


def _unknown_subcommand(error_data: dict | None) -> tuple[str, str] | None:
    """``(domain, subcommand)`` when ``error_data`` is lark-cli's
    unknown-subcommand validation error, else None. Keyed on the message
    text the CLI emits (``unknown subcommand "+get" for "lark-cli docs"``),
    cross-checked against the structured ``params`` reason when present so an
    unrelated message that merely quotes those words does not match."""
    if not isinstance(error_data, dict):
        return None
    m = _UNKNOWN_SUBCOMMAND_RE.search(str(error_data.get("message") or ""))
    if not m:
        return None
    params = error_data.get("params")
    if isinstance(params, list) and params and not any(
        isinstance(p, dict) and p.get("reason") == "unknown subcommand" for p in params
    ):
        return None
    return m.group("domain"), m.group("sub")


def _parse_help_shortcuts(help_text: str) -> tuple[str, ...]:
    """The ``+shortcut`` names listed under ``Available Commands:`` in a
    ``lark-cli <domain> --help`` page, in the order the CLI prints them."""
    block = help_text.split("Available Commands:", 1)
    if len(block) < 2:
        return ()
    body = block[1].split("\nFlags:", 1)[0]
    return tuple(_HELP_SHORTCUT_RE.findall(body))


def _extract_reaction_id(data: Any) -> str:
    """Dig the ``reaction_id`` out of a lark-cli ``reactions create`` payload.

    lark-cli may hand back the reaction record directly or wrapped in a
    ``data`` envelope depending on the endpoint shape — check both. Returns ""
    when not found (informational only; the react tool tolerates it).
    """
    if isinstance(data, dict):
        rid = data.get("reaction_id")
        if isinstance(rid, str) and rid:
            return rid
        inner = data.get("data")
        if isinstance(inner, dict):
            rid = inner.get("reaction_id")
            if isinstance(rid, str) and rid:
                return rid
    return ""


class LarkCLIClient:
    """Async wrapper around lark-cli subprocess calls."""

    # =========================================================================
    # Routing entrypoint
    # =========================================================================

    async def _run_with_agent_id(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
        *,
        capture_binary: bool = False,
    ) -> dict:
        """Single routing entrypoint for every agent-scoped lark-cli call.

        Looks up the credential, ensures the workspace is hydrated from DB
        (creating or rebuilding config.json if needed), and runs the CLI
        with HOME=lark_workspace (config isolation) + CWD=agent_workspace
        (so any file outputs lark-cli writes via default `./...` paths land
        inside the agent's Read-tool sandbox; 2026-05-28 fix for the
        "transcript downloaded to a path I can't read" P0).

        Special case: `config init --new` (interactive app creation) is
        called BEFORE the credential exists, so it bypasses hydration and
        goes straight to `_run_with_home`.

        When ``capture_binary=True``, stdout is treated as a status
        channel rather than a JSON payload — used by binary endpoints
        like ``api GET /open-apis/im/v1/messages/.../resources/...``
        which write the response body to ``--output <path>``. See
        ``fetch_message_resource``.
        """
        is_init_new = (
            len(args) >= 3
            and args[0] == "config"
            and args[1] == "init"
            and "--new" in args
        )
        if is_init_new:
            return await self._run_with_home(args, agent_id, stdin_data, timeout)

        from narranexus.platform.module_system.data_access import get_channel_credential_store
        from ._lark_credential_manager import _cred_from_raw
        from ._lark_workspace import get_home_env, get_workspace_path

        store = get_channel_credential_store()
        raw = await store.get_credential("lark", agent_id)
        cred = _cred_from_raw(raw) if raw is not None else None
        if not cred:
            return {
                "success": False,
                "error": f"No Lark credential for agent {agent_id}. Run lark_setup first.",
            }

        # Lazy migration: pre-refactor manual binds had workspace_path="".
        # Persist through the seam so it works in both direct-db and
        # zero-cred deployments; the seam never raises, so a failed write
        # only surfaces here — warn or the migration retries invisibly on
        # every call.
        if not cred.workspace_path:
            cred.workspace_path = str(get_workspace_path(agent_id))
            res = await store.patch_credential(
                "lark", agent_id, {"workspace_path": cred.workspace_path}
            )
            if not res.get("success"):
                logger.warning(
                    f"[lark-cli] workspace_path migration not persisted for "
                    f"{agent_id}: {res.get('error')}"
                )

        hydrated, err = await self._ensure_hydrated(cred)
        if not hydrated:
            return {"success": False, "error": err}

        env = get_home_env(agent_id)
        cwd = await resolve_agent_workspace_cwd(agent_id, log_tag="lark-cli")
        cmd = ["lark-cli"] + args
        logger.debug(
            f"lark-cli [{agent_id}/{cred.profile_name}] cwd={cwd}: {' '.join(cmd)}"
        )
        return await self._exec_lark_cli(
            cmd,
            stdin_data,
            timeout,
            env=env,
            cwd=cwd,
            capture_binary=capture_binary,
        )

    # =========================================================================
    # Hydration: reconcile workspace config.json with DB
    # =========================================================================

    async def _ensure_hydrated(self, cred) -> tuple[bool, str]:
        """Make sure workspace/.lark-cli/config.json reflects the DB cred.

        Idempotent: if the workspace already holds a config entry for
        cred.app_id, it's a no-op. Otherwise we rebuild via `config init
        --app-id X --app-secret-stdin --name ...` with HOME=workspace.

        Returns (success, error_msg). Failure reasons:
          - No plain secret in DB (agent-assisted pre-enable) AND no
            existing workspace config (fresh machine after DB migration).
            → Tell the caller so it can surface the error to the user
              ("please paste your App Secret via lark_enable_receive").
        """
        from ._lark_workspace import ensure_workspace, get_home_env

        workspace = Path(cred.workspace_path)
        config_path = workspace / ".lark-cli" / "config.json"

        # Already up to date?
        if config_path.is_file():
            try:
                current = json.loads(config_path.read_text(encoding="utf-8"))
                apps = current.get("apps", [])
                if any(a.get("appId") == cred.app_id for a in apps):
                    return True, ""
            except (json.JSONDecodeError, OSError):
                pass  # corrupted or unreadable → rebuild

        # Need to hydrate. We need the plain secret from DB.
        plain_secret = cred.get_app_secret()
        if not plain_secret:
            return False, (
                "Workspace config missing and DB has no plain App Secret. "
                "If this is an agent-assisted setup, the user needs to complete "
                "`lark_enable_receive` once to unlock both trigger AND CLI. "
                "If this is a manual bind, re-bind via frontend LarkConfig."
            )

        ensure_workspace(cred.agent_id)
        env = get_home_env(cred.agent_id)
        cmd = [
            "lark-cli", "config", "init",
            "--app-id", cred.app_id,
            "--app-secret-stdin",
            "--brand", cred.brand,
            "--name", cred.profile_name,
        ]
        logger.info(
            f"Hydrating workspace for {cred.agent_id} "
            f"(app_id={cred.app_id}, profile={cred.profile_name})"
        )
        result = await self._exec_lark_cli(cmd, stdin_data=plain_secret, timeout=30.0, env=env)
        if result.get("success"):
            return True, ""
        return False, f"Workspace hydration failed: {result.get('error', 'unknown')}"

    # =========================================================================
    # Direct HOME-only runner (for config init --new)
    # =========================================================================

    async def _run_with_home(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
    ) -> dict:
        """Run lark-cli with HOME=workspace, no hydration, no --profile.

        Only used for `config init --new` inside lark_setup, which creates
        the credential itself — there's nothing to hydrate from yet.
        """
        from ._lark_workspace import get_home_env

        env = get_home_env(agent_id)
        cmd = ["lark-cli"] + args
        logger.debug(f"lark-cli HOME [{agent_id}]: {' '.join(cmd)}")
        return await self._exec_lark_cli(cmd, stdin_data, timeout, env=env)

    # =========================================================================
    # Shared subprocess + JSON parser
    # =========================================================================

    async def _exec_lark_cli(
        self,
        cmd: list[str],
        stdin_data: str,
        timeout: float,
        env: dict | None = None,
        cwd: Path | str | None = None,
        *,
        capture_binary: bool = False,
        translate_unknown: bool = True,
    ) -> dict:
        """Spawn lark-cli, collect stdout, parse JSON, handle errors.

        ``translate_unknown`` turns an unknown-subcommand validation error
        into the domain's shortcut list (see ``_domain_shortcuts``). The
        ``--help`` probe itself runs with it OFF: if a CLI ever answered the
        probe with the same error shape, translating it would probe again,
        and again — the guard is what makes the recursion impossible.

        ``cwd`` controls the child's working directory. Caller passes the
        agent workspace path so lark-cli's default-relative file outputs
        (``./artifact-<title>/transcript.txt`` and similar) land in the
        agent's Read-tool sandbox instead of whatever the MCP process'
        CWD happens to be (which used to be ``/app/`` — outside any
        readable mount). ``None`` means inherit the parent's CWD —
        only the lifecycle / setup flows that have no agent attached
        should use that.

        Two stdout-handling modes (orthogonal to ``cwd``):

        - **text mode** (``capture_binary=False``, default): parse stdout
          as JSON and return ``{"success": True, "data": <parsed>}``. This
          is the mode used by every existing call site.

        - **binary mode** (``capture_binary=True``): the caller passed
          ``--output <path>`` so lark-cli writes the response body to
          disk and emits an empty stdout (success) or a JSON error
          envelope (failure). Stdout is therefore NOT JSON on success
          and must not be parsed. Returns ``{"success": True}`` (no
          ``data``) on success; the caller is responsible for reading
          ``--output``. Phase 1c added this mode for
          ``fetch_message_resource``.
        """
        # Resolve lark-cli to an absolute path and make sure the child's PATH
        # carries the npm-global + node bins. Without this a stripped host /
        # MCP-subprocess PATH makes both the binary and its `env node` shebang
        # vanish, even though lark-cli works in the user's login shell (#53).
        if cmd and cmd[0] == "lark-cli":
            resolved, extra_path = _resolve_lark_cli()
            cmd = [resolved, *cmd[1:]]
            env = dict(env) if env is not None else dict(os.environ)
            if extra_path:
                env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                env=env,
                cwd=str(cwd) if cwd is not None else None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode() if stdin_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return {"success": False, "error": f"CLI command timed out after {timeout}s"}
        except FileNotFoundError:
            return {
                "success": False,
                "error": (
                    "lark-cli not found. Install it with `npm install -g @larksuite/cli`. "
                    "If it is installed but on a PATH this background process can't see, "
                    "set LARK_CLI_BIN to its absolute path (find it via `which lark-cli`)."
                ),
            }

        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if proc.returncode != 0:
            error_msg = stderr_str or stdout_str or f"CLI exited with code {proc.returncode}"
            error_data: dict = {}
            # The JSON error envelope lands on stdout for API failures but on
            # STDERR for the CLI's own validation errors (unknown subcommand,
            # bad flag — verified against lark-cli 1.0.86, 2026-09-09). Read
            # whichever stream carries it; without the stderr pass the agent
            # got the raw envelope as one opaque error string.
            for stream in (stdout_str, stderr_str):
                try:
                    parsed = json.loads(stream)
                except (json.JSONDecodeError, AttributeError):
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                    err = parsed["error"]
                    error_msg = err.get("message", error_msg)
                    if "console_url" in err:
                        error_msg += f"\n\nEnable permission here: {err['console_url']}"
                    error_data = err
                    break
            unknown = _unknown_subcommand(error_data) if translate_unknown else None
            if unknown is not None:
                domain, sub = unknown
                shortcuts = await self._domain_shortcuts(
                    domain, cmd[0], env, cwd, timeout=timeout
                )
                error_data = dict(error_data)
                error_data.setdefault("domain", domain)
                if shortcuts:
                    # Absent when the probe failed, so a consumer can tell
                    # "no shortcuts exist" from "could not read them".
                    error_data["valid_shortcuts"] = list(shortcuts)
                else:
                    error_data["shortcuts_unavailable"] = True
                if shortcuts:
                    error_msg = (
                        f"Unknown subcommand '{sub}' for `lark-cli {domain}`. "
                        f"Valid +shortcuts for {domain}: {', '.join(shortcuts)}. "
                        f"Pick one of these; do not invent others. Raw API "
                        f"resources are listed by `lark-cli {domain} --help`."
                    )
                else:
                    error_msg = (
                        f"Unknown subcommand '{sub}' for `lark-cli {domain}` "
                        f"(the domain's shortcut list could not be read; run "
                        f"`lark-cli {domain} --help`)."
                    )
            return {"success": False, "error": error_msg, "error_data": error_data}

        if capture_binary:
            # Bytes went to --output; stdout is a status channel and is
            # expected to be empty on success. Do NOT parse as JSON.
            return {"success": True}

        try:
            data = json.loads(stdout_str) if stdout_str else {}
        except json.JSONDecodeError:
            data = {"raw_output": stdout_str}

        return {"success": True, "data": data}

    async def _domain_shortcuts(
        self,
        domain: str,
        executable: str,
        env: dict | None,
        cwd: Path | str | None,
        *,
        timeout: float,
    ) -> tuple[str, ...]:
        """``+shortcuts`` of ``lark-cli <domain>``, read from its ``--help``
        page once per (executable, domain) for the process. Same executable /
        env / cwd as the failing call so the probe sees the identical CLI,
        and never a longer wait than that call. A SUCCESSFUL probe is cached
        whatever it yielded — including an empty tuple (a domain without
        +shortcuts) — so a domain that has none does not cost a spawn on
        every hallucinated call; a FAILED probe is not cached for good: hits
        within ``_SHORTCUT_PROBE_RETRY_SEC`` of the failure skip the probe,
        the first hit after that retries it. The probe runs with
        ``translate_unknown=False`` — the recursion guard."""
        key = (executable, domain)
        cached = _SHORTCUT_CACHE.get(key)
        if cached is not None:
            return cached
        failed_at = _SHORTCUT_PROBE_FAILED_AT.get(key)
        if failed_at is not None and time.monotonic() - failed_at < _SHORTCUT_PROBE_RETRY_SEC:
            return ()
        probe = await self._exec_lark_cli(
            [executable, domain, "--help"],
            "",
            min(timeout, _HELP_PROBE_TIMEOUT),
            env=env,
            cwd=cwd,
            translate_unknown=False,
        )
        if not probe.get("success"):
            logger.warning(
                f"[lark-cli] could not read `{domain} --help` for the shortcut "
                f"list: {probe.get('error')}"
            )
            _SHORTCUT_PROBE_FAILED_AT[key] = time.monotonic()
            return ()
        _SHORTCUT_PROBE_FAILED_AT.pop(key, None)
        data = probe.get("data")
        text = data.get("raw_output", "") if isinstance(data, dict) else ""
        shortcuts = _parse_help_shortcuts(text)
        if not shortcuts:
            # Either the domain has no +shortcuts, or the help page did not
            # reach us as text (e.g. it parsed as JSON and carries no
            # raw_output). Say so once — the empty result is cached below.
            logger.warning(
                f"[lark-cli] `{domain} --help` yielded no +shortcuts "
                f"(help text {len(text)} chars); the unknown-subcommand "
                f"error will carry no list for this domain"
            )
        _SHORTCUT_CACHE[key] = shortcuts
        return shortcuts

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def profile_remove(self, agent_id: str) -> dict:
        """Remove the CLI profile + keychain entry for an agent.

        Runs `lark-cli profile remove` with HOME=workspace so the CLI
        cleans its own config.json + the keychain reference it owns.
        The workspace directory itself is the caller's responsibility
        (e.g. delete_agent uses shutil.rmtree).
        """
        from narranexus.platform.module_system.data_access import get_channel_credential_store
        from ._lark_credential_manager import _cred_from_raw
        from ._lark_workspace import get_home_env, get_workspace_path

        raw = await get_channel_credential_store().get_credential("lark", agent_id)
        cred = _cred_from_raw(raw) if raw is not None else None
        if not cred:
            return {"success": False, "error": "No Lark credential for this agent."}

        workspace = Path(cred.workspace_path or str(get_workspace_path(agent_id)))
        if not (workspace / ".lark-cli" / "config.json").is_file():
            # Nothing to remove — workspace already empty or never hydrated
            return {"success": True, "data": {"message": "no workspace to clean"}}

        env = get_home_env(agent_id)
        cmd = ["lark-cli", "profile", "remove", cred.profile_name]
        return await self._exec_lark_cli(cmd, stdin_data="", timeout=30.0, env=env)

    # =========================================================================
    # Business methods — all agent_id-scoped, route via _run_with_agent_id
    # =========================================================================

    async def get_user(
        self, agent_id: str, user_id: str = "", identity: str = "user"
    ) -> dict:
        """Get a user's contact info (display name, etc.).

        Defaults to `--as user` — the agent owner's authorized user token,
        stored per-agent in the isolated workspace HOME. We need it because
        the bot tenant token lacks `contact:user.base:readonly`, so
        `--as bot` only ever returns open_id/union_id with no name. Pass
        `identity="bot"` for an app-identity lookup. Omit user_id to read
        the calling identity's own info.
        """
        args = ["contact", "+get-user", "--as", identity]
        if user_id:
            args.extend(["--user-id", user_id])
        return await self._run_with_agent_id(args, agent_id)

    async def send_message(
        self,
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        text: str = "",
        markdown: str = "",
    ) -> dict:
        """Send a message to a chat or user."""
        args = ["im", "+messages-send"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        if text:
            args.extend(["--text", text])
        elif markdown:
            args.extend(["--markdown", markdown])
        return await self._run_with_agent_id(args, agent_id)

    async def add_reaction(
        self, agent_id: str, message_id: str, emoji_type: str
    ) -> str:
        """Add an emoji reaction to a message; return its ``reaction_id``.

        Backs the agent-facing ``react_to_user_message`` tool. Reactions are
        best-effort (needs the bot's ``im:message.reactions:write_only`` scope),
        so this raises on failure and lets the caller log + swallow. Returns the
        ``reaction_id`` (or "" when unparsable — informational only).

        Runs ``--as bot`` on purpose: the reaction must be attributed to the
        agent's bot, not the owner's user identity. The workspace holds BOTH a
        bot token AND (after the three-click flow) the owner's user OAuth token;
        the raw ``im reactions create`` command defaults to the user token when
        present, so without ``--as bot`` the 👍 shows up under the human owner's
        name instead of the bot. (``+messages-send`` forces bot on its own, which
        is why replies were already attributed correctly.)
        """
        if not _LARK_ID_PATTERN.match(message_id):
            raise RuntimeError(
                f"invalid message_id format: {message_id!r} "
                f"(expected ^[A-Za-z0-9_-]+$)"
            )
        params = json.dumps({"message_id": message_id})
        data = json.dumps({"reaction_type": {"emoji_type": emoji_type}})
        args = [
            "im", "reactions", "create", "--as", "bot",
            "--params", params, "--data", data,
        ]
        result = await self._run_with_agent_id(args, agent_id)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "reaction create failed"))
        return _extract_reaction_id(result.get("data"))

    async def list_chat_messages(
        self,
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        limit: int = 20,
    ) -> dict:
        """List recent messages in a chat or P2P conversation."""
        args = ["im", "+chat-messages-list", "--as", "bot"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        args.extend(["--page-size", str(limit)])
        return await self._run_with_agent_id(args, agent_id)

    async def fetch_message_resource(
        self,
        agent_id: str,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
        timeout: float = 60.0,
    ) -> bytes:
        """Download a message resource binary via ``api GET .../resources/...``.

        Lark's IM resource endpoint returns raw bytes (PDF, image, audio,
        etc.) rather than JSON. We invoke lark-cli with ``--output
        <tmpfile>`` so the bytes land on disk; the subprocess wrapper's
        new ``capture_binary=True`` mode then skips stdout JSON-parsing.

        ``resource_type`` ∈ {``file``, ``image``, ``audio``, ``video``,
        ``media``}. See Lark docs:
        https://open.larksuite.com/document/server-docs/im-v1/message/get-2

        Raises ``RuntimeError`` on any failure (CLI error, empty output,
        lark-cli not installed, bad identifier format). Callers in
        ``LarkTrigger.fetch_attachments`` catch + audit + skip the ref,
        preserving the never-raise contract at the trigger boundary.
        """
        # Validate identifier format BEFORE building the URL. The values
        # originate from Lark events which we don't fully control;
        # create_subprocess_exec already blocks shell injection, but a
        # malformed message_id like "om_x/../../../admin" would still
        # construct an unintended URL path. ``_LARK_ID_PATTERN`` accepts
        # only the alphanumeric / underscore / hyphen format Lark actually
        # uses for message_id / file_key / image_key.
        if not _LARK_ID_PATTERN.match(message_id):
            raise RuntimeError(
                f"invalid message_id format: {message_id!r} "
                f"(expected ^[A-Za-z0-9_-]+$)"
            )
        if not _LARK_ID_PATTERN.match(file_key):
            raise RuntimeError(
                f"invalid file_key format: {file_key!r} "
                f"(expected ^[A-Za-z0-9_-]+$)"
            )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            tmp_path = tmp.name
        try:
            args = [
                "api", "GET",
                f"/open-apis/im/v1/messages/{message_id}/resources/{file_key}",
                "--params", json.dumps({"type": resource_type}),
                "--output", tmp_path,
            ]
            result = await self._run_with_agent_id(
                args, agent_id, timeout=timeout, capture_binary=True,
            )
            if not result.get("success"):
                raise RuntimeError(
                    f"lark-cli resource fetch failed: {result.get('error', 'unknown')}"
                )
            try:
                raw = Path(tmp_path).read_bytes()
            except OSError as e:
                raise RuntimeError(
                    f"lark-cli reported success but output file unreadable: {e}"
                ) from e
            if not raw:
                raise RuntimeError(
                    "lark-cli reported success but output file is empty"
                )
            return raw
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
