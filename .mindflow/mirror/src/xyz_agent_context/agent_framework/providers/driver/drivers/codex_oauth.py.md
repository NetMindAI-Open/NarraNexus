---
code_file: src/xyz_agent_context/agent_framework/providers/driver/drivers/codex_oauth.py
stub: false
last_verified: 2026-07-31
---

## 2026-07-31 — verify_live:P0「凭证失效仍测试通过」的修复主体

Test 按钮对 `auth_type="oauth"` 行曾无条件放行(user_service 短路),
auth.json 文件在但 refresh token 已死时照样显示"可用",且
ProviderReadiness 借同一条路把暂停的 job 重新武装到死凭证上。新增
`verify_live()`:经注册的 codex agent-loop 驱动跑一次 tool-free 单轮
("ping"),与 agent 同一传输。要点:

- codex 驱动读的是环境 `_codex_ctx`(agent 槽的配置),所以进入前用
  `build_codex_config(model)` 装本卡配置、结束 reset——与
  `CliHelperSDK._run_codex_oneshot` 同模式。
- 死凭证以终态错误**事件**(error_type="unauthorized")呈现而非异常,
  必须同时保留 type+message(cli_helper 同款教训)。
- 快速失败不花钱:probe()(auth.json 存在性)和 `which codex` 先挡。
- 模型选 curated 缺省(get_default_models)优先于行内 models——防
  2026-07-30 的死 pinned id 让"模型死"误报成"凭证死"。
- 超时 helper_cli_total_timeout_seconds;单轮非 agent_loop,不违铁律 #14。
- probe() 明确降格为"便宜的存在性信号,非健康"。

测试:tests/agent_framework/test_oauth_live_verification.py。

Review 轮修正(同日,PR #224):返回改三态(见 [[base]] VerifyVerdict)
——executor seam 生效时**先于一切本地检查**返回 unknown(判据是
`broker_client.executor_seam_active()`:BROKER_URL(dev/prod compose 实际
设的)或静态 AGENT_EXECUTOR_URL。第一版只认后者,在云上是死守卫——
review 第 3 轮抓出);控制面容器没装 codex、~/.codex 也不是执行面的,
本地检查全是误判来源;超时
/spawn 异常也是 unknown(codex 的凭证失败走错误事件,异常是环境问题);
只有错误事件/无凭证文件/无 CLI/无回话是 dead。一发脚手架换用共享
[[cli_oneshot]];curated 模型防线因 catalog 补键而真正生效(此前
get_default_models 对 codex 返回 [],防线是死代码)。

## 2026-06-17 — override build_codex_config(凭证 ref 特例归位)

agent slot 现在走 `build_codex_config`(不再是 resolver 的 free function)。本
driver override 它:强制 `auth_ref = CODEX_CLI_CREDENTIALS_REF`、`api_key` 留空
(`to_cli_env` 对 oauth 会 blank `CODEX_API_KEY`),让 `codex exec` 从
`~/.codex/auth.json` 读 token。`build_claude_config`/`build_openai_config` 仍是
`_DriverBase` 的 NotImplementedError default。原来散在 resolver
`if source=="codex_oauth"` 的凭证 ref 特例,现在归位到这个最该知道它的 driver。

## Why it exists

OpenAI Codex CLI's OAuth provider driver. Mirrors
``claude_oauth.py`` for the Codex side of the coding-agent
framework choice. The host machine's ``codex login`` command
performs OAuth with OpenAI and writes the resulting tokens to
``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``). NarraNexus
does NOT touch the token itself — the Codex CLI subprocess reads
it directly.

This driver's primary role is **probe-only**. Unlike CC's
``ClaudeOAuthDriver`` (which produces a ``ClaudeConfig`` for the
agent slot), Codex doesn't fit the ``ClaudeConfig`` /
``OpenAIConfig`` / ``EmbeddingConfig`` shapes. The runtime
dispatch happens at ``step_3_agent_loop._resolve_agent_framework_sdk``
which reads ``user_slots.agent_framework`` directly rather than
going through the driver's ``build_*_config`` methods.

## Design decisions

- **No ``build_*_config`` overrides.** Defaults inherited from
  ``_DriverBase`` all raise ``NotImplementedError``, which is the
  correct contract: Codex is the agent framework, not a target for
  ``ClaudeConfig`` / ``OpenAIConfig`` / ``EmbeddingConfig``
  consumers. step_3 dispatches via the framework column instead.
- **Probe checks file existence only.** We do NOT parse the
  ``auth.json`` content — that's Codex CLI's job, and the schema
  may change between versions. Existence + is_file is sufficient
  signal for the Settings page "✓ Codex CLI linked" pill.
- **``CODEX_HOME`` override honoured.**
  ``resolve_codex_credentials_path`` in ``derive.py`` checks
  ``CODEX_CLI_CREDENTIALS_PATH`` first, then ``CODEX_HOME``, then
  defaults. Same precedence as CC's ``CLAUDE_CLI_HOME`` chain.

## Upstream / downstream

- **Upstream**: ``backend/routes/providers.py``
  ``_probe_agent_framework_auth`` synthesizes a stub ProviderCard
  with ``auth_ref="codex-cli:~/.codex/auth.json"`` and calls
  ``CodexOAuthDriver(stub).probe()`` for the Settings page status
  pill.
- **Downstream**: ``provider_driver.derive.resolve_codex_credentials_path``
  for path resolution. ``provider_driver.registry`` for
  registration (``@register`` decorator triggers via
  ``drivers/__init__.py``'s explicit import).

## Gotchas

- **Importing the module is what registers the driver.** The
  ``@register`` decorator only fires on first import. The
  ``drivers/__init__.py`` does ``from . import codex_oauth`` to
  guarantee that. Forgetting to add it there means the driver is
  missing from ``DRIVER_REGISTRY`` and ``get_driver_class("codex_oauth")``
  returns ``None``.
- **The probe returns ``ok=False`` with a hint when the auth file
  is missing.** The hint text ("Run ``codex login`` on the host
  to create it.") is consumed verbatim by the frontend's status
  pill — keep it actionable.
- **Driver does NOT serve any slot.** The provider_driver layer
  has a slot-routing notion that maps drivers to the agent /
  helper_llm / embedding slots via ``build_*_config``. Codex
  intentionally doesn't fit that — see ``step_3_agent_loop``
  dispatch and ``user_slots.agent_framework`` column for the
  Codex-specific routing path.

## 2026-07-07 — helper 槽也由订阅覆盖

新增 `build_cli_helper_config`（framework=codex_cli）。OAuth 仍不能直连 chat-completions，但 helper 经 CliHelperSDK 走 `codex exec` 一次性，故订阅同时覆盖 agent+helper 两槽。
