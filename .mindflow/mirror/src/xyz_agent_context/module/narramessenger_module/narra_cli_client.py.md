---
code_file: src/xyz_agent_context/module/narramessenger_module/narra_cli_client.py
stub: false
last_verified: 2026-07-20
---

## 2026-07-21 — redirect HOME so narra-cli can chmod its config dir (EPERM fix)

Real dev bug (only surfaced once a strong model actually RAN the command, not the
manual root-shell test which masked it): narra-cli's ``ConfigStore.ensurePrivateDir``
unconditionally ``chmod``s ``$HOME/.narra-cli`` at startup. The MCP server runs as
a non-root user (app) whose real ``$HOME`` (``/home/app``) is on a mount it cannot
chmod → ``EPERM: chmod '/home/app/.narra-cli'`` on EVERY narra_cli call. Fix:
``run()`` sets ``env["HOME"] = _narra_cli_home()`` — a process-owned dir under the
system tmpdir (created once, shared across calls; only holds the default/prod
endpoint config, never the token). The path is **per-uid** (``narra-cli-home-<uid>``)
and forced to **0700 with an ownership check**: on a shared host (bash run.sh mode,
铁律 #7) a fixed world-path + ``exist_ok=True`` could be pre-squatted by another uid
→ narra-cli chmods a dir it doesn't own = the same EPERM, and 0755 would leak the
endpoint config. If the path isn't ours we fall back to ``tempfile.mkdtemp`` (unique,
0700). Reproduced + verified on dev: ``HOME=/home/app``
→ EPERM, ``HOME=/tmp/…`` → runs. (My earlier "manual verify passed" was a false
positive — it ran as root, which can chmod anything.)

## 2026-07-20 (review round 2) — default timeout 60s → 120s

Large `im attachments download` / long `speech synthesize` can exceed 60s;
`NarraCliClient.run` + `run_narra_cli` default `timeout` bumped to 120s. Known
follow-up (not done — parity with lark): `_agent_user_id_cache` is unbounded /
never invalidated, same as `lark_cli_client`.

## 2026-07-20 — review fixes: envelope trusts exit code; dead field/param dropped

- ``_parse_envelope`` now takes the subprocess **exit code**. A file-writing
  command (``speech synthesize --out`` / ``im attachments download --output``)
  can succeed with **empty stdout**; empty + exit 0 → ``{"success": True}``
  instead of a false ``empty_output`` failure. (The never-wired ``capture_binary``
  param was removed — this general rule subsumes it.)
- Dropped ``NarraCliClient._base`` (stored ``backend_base_url`` but never used).
  The CLI's endpoint is its **global** config, NOT ``cred.backend_base_url`` —
  ``run_narra_cli`` now documents the single-backend (prod) assumption
  explicitly. A per-agent endpoint (api-test) is deliberately not built yet.

## Why it exists

The single spawn choke point for the local ``narra-cli`` binary
(``@narra-im/narra-cli``). NarraMessenger's query/context surface
(room, im messages, im attachments, speech, status) is delegated to
narra-cli via the ``narra_cli`` MCP tool rather than hand-wrapped as
per-command MCP tools — so when narra-cli grows a command, nothing here
changes (see the design/impl specs in the Obsidian ``Narramessenger
接入`` vault, 2026-07-20). This wrapper owns the three platform concerns
narra-cli itself does not.

Mirrors ``lark_module/lark_cli_client.py`` in shape but is far thinner:
narra-cli takes the bearer as a flag, so there is **no HOME override and
no per-agent config hydration** — the two things that make the Lark
client heavy.

## Design decisions

- **Binary resolution (lark #53 class).** A stripped MCP-subprocess PATH
  cannot see a locally-installed CLI or its ``env node`` shebang.
  ``_resolve_narra_cli`` resolves an absolute path in this order:
  ``NARRA_CLI_BIN`` (exported by run.sh / ENV in Docker) → our **managed
  install** dirs (``~/.narranexus/narra-cli`` for run.sh,
  ``/opt/narra-cli`` for Docker) → PATH → node-bin discovery. Managed
  installs are checked **before** PATH on purpose: a stale global
  ``narra-cli`` (an old ``npm i -g``) must not shadow the version we
  install and track. run.sh installs under ``~/.narranexus`` (not the
  repo tree) precisely so this dir — which is in the resolver's list —
  is found in BOTH run modes, including the 4-terminal ``make dev-mcp``
  path that never sees run.sh's ``NARRA_CLI_BIN`` export. Memoises on
  success; returns the bare name WITHOUT memoising when nothing resolves,
  so a mid-session install re-discovers it.
- **Token injection is the load-bearing security decision.** narra-cli
  accepts the bearer ONLY via ``--token`` / ``--token-file`` (verified
  against v1.1.0 source: ``command-utils.js::requireToken`` — no env, no
  stdin). We write the DB bearer to an **ephemeral** ``--token-file``
  (``tempfile.mkstemp`` → system tmp, ``chmod 600``, ``os.unlink`` in
  ``finally``). Consequences, all deliberate:
    - never on argv (so never in ``ps`` / ``/proc/<pid>/cmdline``),
    - never persisted,
    - system tmp lives in the MCP container, unreachable by the agent's
      workspace-sandboxed ``Read`` tool — so the agent cannot read its
      own runtime bearer (the risk if we'd used the doc's
      ``.narra/agent-runtime-token`` in-workspace convention).
  We deliberately do NOT adopt narra-cli's own storage conventions
  (``.narra/agent-runtime-token`` / ``runtime-state.json``): the CLI is
  stateless per invocation, so we run it stateless and inject identity
  each call — DB stays source of truth.
- **CWD = agent workspace (lark P0 class).** narra-cli writes
  ``--output`` / media at default-relative paths;
  ``_resolve_agent_workspace_cwd`` points CWD at the agent's workspace so
  downloads land in the agent's Read sandbox. None → inherit parent CWD
  (safe for send/query, wrong only for downloads).
- **Envelope normalization.** narra-cli emits a JSON envelope
  ``{command, data, issues, status}`` (status ``ok`` / ``error``, and it
  sets exitCode=1 on error but still prints the envelope). ``_parse_envelope``
  maps ``ok`` → ``{success, data}`` and ``error`` → ``{success:False,
  error:<first issue code>, issues}``. ``capture_binary`` + empty stdout
  (``--output`` wrote to disk) → ``{success:True}``.

## Upstream / downstream

- **Called by**: ``_narramessenger_mcp_tools.narra_cli`` (via
  ``run_narra_cli``).
- **Reads**: ``NarramessengerCredentialManager.get`` (bearer +
  backend_base_url), ``attachment_storage.get_workspace_path`` (CWD).
- **Spawns**: the ``narra-cli`` binary installed by run.sh /
  ``docker/Dockerfile.manyfold`` (and — cloud parity — the executor image
  in NarraNexus-deploy, which MUST install it too).

## Gotchas

- endpoint: narra-cli defaults to ``https://api.netmind.chat`` (prod), so
  prod needs no ``configure``. Non-prod boxes point at their backend via
  ``NARRA_BACKEND_ENDPOINT`` in run.sh (global ``configure`` — single
  backend per deployment assumption).
- The bearer file MUST stay out of the agent-readable CWD — that is why
  it goes to ``tempfile`` (MCP-container tmp), NOT under ``cwd``.
- No env/stdin token path exists upstream; if narra ever adds
  ``NARRA_AGENT_RUNTIME_TOKEN`` env support we could drop the temp file
  entirely (zero disk) — tracked as a possible upstream ask.
