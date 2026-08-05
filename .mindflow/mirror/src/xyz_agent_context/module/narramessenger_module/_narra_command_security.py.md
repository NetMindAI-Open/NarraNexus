---
code_file: src/xyz_agent_context/module/narramessenger_module/_narra_command_security.py
stub: false
last_verified: 2026-08-05
---

## 2026-08-05 — 删掉不可达的空命令分支（随 lark 侧同源清理）

`validate_command` 开头已有 `if not command or not command.strip(): return
"Empty command"`，因此 `command.strip()` 非空且已去空白；这种输入
`shlex.split` 不会返回 `[]`（`str.strip()` 的空白集合是 shlex 的
`' \t\r\n'` 的超集）。所以 `shlex.split` 之后那条
`if not tokens: return "Empty command"` 不可达，且与开头返回同一句话。

来源：lark 侧 `_lark_command_security.py` 修 payload-vs-控制面 guard 时删掉了
同一类死分支，review 指出孪生 guard 里一模一样（铁律 #8 顺手扫相邻代码）。

**一直对的是 `BLOCKED_PATTERNS` 那一半**——先 shlex 分词，再
`lowered[:len(pat)] == pat` 做前导 token 锚定；第 0 位永远是 domain，正文只能
待在 flag 之后，所以正文确实够不着**子命令**规则。那才是 lark 侧对齐的参考实现。

**但 flag 规则不是。** 它是 `t == flag or t.startswith(f"{flag}=")`，残留形状
和 lark 侧**一模一样恰好两种**：正文**整体等于** blocked flag，或正文**以
`--token=` 开头**。实测 `explore --keyword "--token"` 与
`explore --keyword "--token=x"` 都被拒，而 `explore --keyword "--token is bad"`
放行。所以「正文永远够不着控制面规则」在本文件同样是**过度承诺**，别那样写。

残留和 lark 侧一样是**刻意取舍**：泄漏发生在 execve，secret 进了 argv 就对
`ps` / 进程审计 / crash log 可见，与 CLI 怎么解析无关。同一立论要求匹配
**大小写折叠**——2026-08-05 前 flag 检查误用了原始 `tokens` 而非 `lowered`，
`--TOKEN sekret` 会放行（值照样进 argv），已修为 `lowered`。

边界由 `tests/narramessenger_module/test_narra_command_security.py` 的**双向**
用例钉住（REFUSE 侧 + ALLOW 侧 + 大小写）。lark 侧付了三轮 review 学费才明白：
只用散文描述残留，每次都会说得比代码宽；**两侧都钉住才锁得死。**

## Why it exists

The ``narra_cli`` MCP tool is a passthrough: it hands an arbitrary
command string to the ``narra-cli`` binary. This module is what makes
that safe. It mirrors ``lark_module/_lark_command_security.py`` but the
surface is far smaller (narra-cli has ~6 domains), so the whitelist is
short and hand-auditable.

## Design decisions

- **Whitelist by domain, not per-command wrapping.** ``ALLOWED_DOMAINS``
  gates the first token (``room`` / ``im`` / ``speech`` / ``explore`` /
  ``status`` / ``help``). New subcommands/flags under an allowed domain
  pass with zero code change — that is the whole durability point. A new
  *top-level domain* is the only CLI-growth event that needs a one-line
  whitelist add.
- **``BLOCKED_PATTERNS`` carve out what must not go through passthrough.**
  ``configure`` (endpoint is platform-global), ``doctor`` (probe surface),
  and — TRANSITIONALLY — ``im send``. ``im send`` is blocked because the
  send/media path stays on the Matrix-direct dedicated tools
  (``narra_reply`` / ``narra_send`` / ``narra_send_media``) until the
  proxy media path's moderation / compound / failure behaviour is
  validated on dev (owner decision 2026-07-20). ``im messages`` /
  ``im attachments`` are NOT matched by the ``im send`` prefix and remain
  allowed. Remove this block (and the dedicated send tools) once proxy
  send/media is verified.
- **``BLOCKED_FLAGS`` = ``--token`` / ``--token-file``.** The platform
  injects the bearer per call (see [[narra_cli_client]]); an agent
  supplying its own is either overriding our injection or probing for a
  readable path — always rejected.
- **``explore`` passes the whitelist; official-only is enforced
  SERVER-SIDE.** The runtime guide states a non-official agent gets an
  ``official-agent-required`` JSON error from the backend. We deliberately
  do NOT gate ``explore`` client-side: there is no reliable client-side
  signal of official status, and a client gate would only block everyone
  (and hide the informative backend error). An earlier ``is_official``
  param was removed 2026-07-20 for exactly this reason.
- **All checks run on the shlex-tokenized command (2026-07-20 review fix), not
  the raw string.** Matching blocked patterns on the raw string let ``im  send``
  (extra whitespace) slip past the ``im send`` block while the token-based domain
  check still saw ``im``. ``validate_command`` now ``shlex.split``s once and
  matches blocked patterns against **leading tokens** — whitespace-robust, and
  quotes are respected so whitespace inside a quoted arg survives.
- **Escape expansion is scoped to text-value flags (2026-07-20 review fix).**
  ``\n``/``\t``/``\r`` are expanded ONLY in the value of ``--text`` / ``--markdown``
  (``_TEXT_VALUE_FLAGS``) — never in paths (``--out``/``--output``/``--input``),
  search terms (``--keyword``), or ids, where a literal backslash sequence must
  survive. Was applied to every token, which would silently rewrite a path.
- **``shlex.split`` + ``shell=False`` argv is the real injection defense,
  NOT a shell-metachar denylist.** Under ``execve`` the metachars are
  literal; a denylist would only break legitimate content ("S&P 500",
  "$76,000", markdown tables) — the exact lesson already burned in on the
  Lark side. ``sanitize_command`` also expands ``\n``/``\t``/``\r`` so an
  LLM-written ``--text "a\nb"`` renders a real newline.

## Upstream / downstream

- **Called by**: ``_narramessenger_mcp_tools.narra_cli`` (validate →
  sanitize → run).
- Independent (binding rule #3): no cross-module imports; a shape-twin of
  the Lark security layer, not a shared one.

## Gotchas

- Ordering: ``BLOCKED_PATTERNS`` is checked before ``BLOCKED_FLAGS``, so a
  command that is both blocked-pattern and carries a token flag reports
  the pattern reason. Both reject — the reason string is the only
  difference.
- When the send tools are eventually removed, drop ``"im send"`` from
  ``BLOCKED_PATTERNS`` in the same change.
