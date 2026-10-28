---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/narra_cli_client.py
stub: false
last_verified: 2026-09-09
---


## 2026-09-09 — 端点随绑定注入（narra-cli 1.1.0 → 1.2.1）

**prod 事故：** `channel_credentials` 里 27 个非默认绑定（api-cn 19 / api-test 8）的
`narra_cli` 每次都报 `agent-token-invalid`。根因不是 token 坏，而是
`run_narra_cli` 只注入 `--token-file`，端点用 MCP 容器全局
`~/.narra-cli/config.json`（= api.netmind.chat），无视 `cred.backend_base_url`
——即 2026-07-20 那条「单后端假设」。prod MCP 容器内复现：同一 token 打
api.netmind.chat → invalid；配 api-cn → `status` ok。

**上游变化（逐条 `--help` diff 实测）：** 1.2.x 删掉 `configure` 命令和全局配置
文件，14 条走 API 的命令（含 `im messages patch/delete`）全部要求 `--endpoint`（缺则 `invalid-request: Missing
required --endpoint`）；裸 `help` 拒绝任何 flag（`Nonexistent flag`），
`<domain> [<sub>] --help` 容忍注入的 flag。engines 声明 node>=22，node 20 实跑
只有警告。

**改法：**
- `NarraCliClient(bearer_token, endpoint)`；`run()` 在 agent 参数之后追加
  `--endpoint <endpoint> --token-file <tmp>`。**位置不是防线**：oclif 对重复
  flag 直接报 "can only be specified once"，前置后置都赢不了，真正把 agent 的
  `--endpoint` 挡在 argv 之外的是 [[_narra_command_security]] 的 BLOCKED_FLAGS；
  后置只是固定 spawn 形状便于读日志。`_needs_injection`：仅裸 `help` 域
  （大小写折叠）跳过注入；`help <topic>`、裸 topic `im`、`<domain> --help` 均照常注入
  （1.2.1 实测容忍）。
- `run_narra_cli` 读 `cred.backend_base_url`，为空 **fail-closed** 回独立错误码
  `no_endpoint`（与「没绑定」的 `no_credential` 区分；猜默认端点=把 bearer 送到
  别家主机，正是这次的 bug）。上线前查过 prod 144 / dev 14 条绑定，空端点 0 条，
  没有第二批被误杀的存量。
- 删掉「单后端假设」docstring；`_narra_cli_home` 保留（1.2 没有 config store 了，
  但给第三方 CLI 一个私有 HOME 仍是安全默认）。
- 版本钉锁步：`docker/Dockerfile.manyfold` / `run.sh` / deploy 仓
  `docker/Dockerfile.python` 同批 1.2.1；1.1.0 拒绝 `--endpoint`、1.2.x 缺它不跑，
  **代码与二进制必须同批上**。机制：deploy 侧先落 `Dockerfile.python@1.2.1`
  （deploy-ops 的 `docker/**` 过滤把它拉到主机），deploy 仓
  `scripts/check_executor_clis.sh` 是 `make app-build` 的前置，四处钉版本不一致就
  拒绝构建——两种落地顺序都出不了混搭镜像，只是 app 先落会让 dev 停线到 deploy
  跟上为止；prod 晋级同样 deploy main 先、app main 后。
- run.sh 删掉 `configure` 步骤；`NARRA_BACKEND_ENDPOINT` 只剩 doctor 探针用途。

测试（`test_narra_cli_client.py`）：注入值=绑定端点、豁免集合恰为裸 help
（`HELP` / `help im` 不注，`im` / `status` 注）、`<domain> --help` 仍注入、
`run_narra_cli` 用绑定端点、缺端点不 spawn 且回 `no_endpoint`。删注入 3 红、
删 fail-closed 1 红。

## 2026-08-14 — CWD 解析收敛到 data_access 共享实现

本地 `_resolve_agent_workspace_cwd` + `_agent_user_id_cache` 删除，改用
[[workspace_cwd]] 的 `resolve_agent_workspace_cwd(agent_id, log_tag="narra-cli")`
——与 lark 的副本曾是字面重复且行为已开始漂移（lark 版空 owner 打 debug 日志、
narra 版静默）。语义不变，缓存跨渠道共享（落点为何是 data_access 而非 utils，
见该文件 mirror）。

## 2026-08-11 — 凭据 + workspace owner 改走 seam（narra 零凭据）

`run_narra_cli(...)` 和 `_resolve_agent_workspace_cwd(...)` **去掉 `db` 参数**：不再
`db.get_one("agents"/credential 表)`，改经 [[store]] 的 `get_channel_credential_store()`
——bearer 走 `get_credential("narramessenger")` + `_cred_from_raw`，workspace owner 走
`get_agent_owner(agent_id)`。CLI 子进程仍本地跑（纯 compute），只有 DB 持久化那一跳去
backend。这样 mcp 里 narra 相关代码 `get_mcp_db_client()` 残留=0，配合 lark/job 零凭据，
mcp 容器可 strip `DB_PASSWORD`。

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
