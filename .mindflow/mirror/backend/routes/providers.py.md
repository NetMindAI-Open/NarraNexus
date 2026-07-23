---
code_file: backend/routes/providers.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — agent-framework 403 门禁方向化(修云端老 codex 死锁)

`set_agent_framework` 的云端 403 原本方向不敏感(`_is_cloud() and not _is_staff` 就拦),
老 codex 用户切不回 claude_code。改为加 `and body.framework != "claude_code"`——非 staff
云端用户**总能切回 claude_code**,只挡切到其他框架。配套 m0003 数据迁移(存量用户自动迁)
+ 前端两个选择器弹窗方向修复。

## 2026-07-18 — netmind-only 规则收拢进 cloud_policy；检查下沉 set_slot

Code review 重构（供 2026-07-17 条目参照的更正）：

- `_NETMIND_ONLY_DETAIL` 与内联谓词移入 [[cloud_policy]]；本文件的
  `_netmind_slots_only(request)` 只是 `netmind_slots_only(_is_staff(request))`
  的薄封装。
- **PUT /slots 的路由级 prov 查询删除**（消除双读）：`set_slot` 接收
  `actor_is_staff=_is_staff(request)` 在服务层强制，路由 catch
  `CloudPolicyViolation` → 403。onboard 的 register-only 与 add 的
  default_slots 跳过仍在路由层（那是"怎么应用"而非"规则本身"）。
- **POST /agent-framework 的 403 文案换成 `FRAMEWORK_LOCKED_DETAIL`**——旧文案
  劝用户"用自己的 API key onboard"，与 netmind-only 策略自相矛盾。

## 2026-07-17 — 云端 netmind-only 槽位策略（非 staff 只能绑 NetMind）

新增 `_netmind_slots_only(request)`（= `_is_cloud() && !_is_staff()`）+
`_NETMIND_ONLY_DETAIL` 文案。产品决策：云端用户只能用 NetMind 账户运行，自有
API key 是本地版功能。三处落点：

- **PUT /slots/{slot}**：绑定前经 `service.db` 读 provider 行，
  `source != "netmind"` → 403（行缺失则放行给 service 报 not-found）。
- **POST /onboard**：`activate = not _netmind_slots_only(request)` —— 云端非
  staff 变 **register-only**（key 入钱包，框架/槽位不动，响应带
  `activated:false` 供 UI 分支）。
- **POST /**（add_provider）：`default_slots` 在 netmind-only 时跳过（加卡
  照常，不绑槽）。

同一规则守 per-agent 路由（[[agents_llm_config]]）；前端双面板下拉经
`cloudNetmindOnly`（frontend lib/agentFramework）同步过滤,UI 不会给出会被
403 的选项。**故意不动** `validate_slot_binding`——那是"协议兼容"层规则,这是
"部署×角色"策略,归 route 层（与 OAuth staff-gate 同层同型）。staff 豁免、
本地不受影响。测试：tests/backend/test_cloud_netmind_only_slots.py（stub
service,门禁边界）。

## 2026-07-16 — GET /api/providers 附上 NetMind 账户邮箱

新增 `_attach_netmind_accounts(uid, data)`:在 `_config_to_response` 之后,从
`user_providers`(source='netmind')按 provider_id 读出 `netmind_account_email` 合并进每个
provider dict,供 Settings 显示"该 key 属于哪个账户"。在 API 边界做的定向增强,避免把新列
一路穿过 LLMConfig 模型。best-effort——查不到就省略该字段。

## 2026-07-13 — use-subscription reachability moved to power axis

The `use_subscription` route's "not available" gate changed from
`is_cloud_mode()` to `is_power_login_enabled()` ([[deployment_mode]]), so a local
deployment that opted into Power login can hit it too. The
`settings.netmind_use_subscription_enabled` feature-flag gate is unchanged.
**`_is_cloud()` (line ~128) and its OAuth-card/staff uses are untouched** — those
are the multi-tenant authz boundary, not a Power capability.

## 2026-07-13 — Agent 实时层熔断器接入

在 4 个『用户重新可运行』边缘（add_provider/onboard/use_subscription/set_slot，紧挨既有 `schedule_user_no_quota_rearm(uid)`）新增 `await _resume_agent_circuit_breakers(uid)` → `agent_circuit_breaker.reset_for_owner`：换 key/充值/换 slot 后自动解除该 owner 的 auth/quota 暂停（transient 冷却不动）。best-effort，绝不弄挂重配。

## 2026-07-10 — use-subscription is now a thin wrapper over the provisioner

The mint + dedup + onboard + orphan-cleanup + per-user lock all moved into
[[netmind_provisioner]]'s `ensure_netmind_provider` (shared with login
auto-register). The route now only: gates (cloud / flag / token), calls
`ensure_netmind_provider(uid, token, activate_if_fresh=True)`, maps its outcome
to HTTP (True→200 with the refreshed config, False→409 already-connected,
KeyAuthError→401, KeyUpstreamError→502, ValueError→409/502/400), and runs the
hot-reload + rearm tail. It's now mainly a fallback — every login auto-registers,
so the frontend no longer calls it. `_use_sub_locks` was removed from this file.

## 2026-07-09 — `_is_cloud` delegates to deployment_mode

`_is_cloud()` no longer re-sniffs `DATABASE_URL`; it delegates to
[[deployment_mode]]'s `is_cloud_mode()` (the single source of truth — honours
`NARRANEXUS_DEPLOYMENT_MODE`, treats an unset `DATABASE_URL` as local). This was
one of three skewed copies; the per-agent config route ([[agents_llm_config]])
converged on the same helper. Import is top-level now.

## 2026-07-07 — use-subscription wires minted key to the env's inference base

/use-subscription now passes inference_base=settings.netmind_inference_base into
onboard_one_key, so a dev-minted key is registered against dev inference
(test.api.netmind.ai) rather than the hardcoded prod. /onboard (manual paste) is
unchanged (prod). See [[user_provider_service]].



## 2026-07-06 — use-subscription pre-flip caveats expanded

Expanded the pre-flip TODO on the in-process _use_sub_locks: before enabling
netmind_use_subscription_enabled in a multi-worker deploy, a distributed guard
must also cover the OTHER netmind-source creators (add_provider/onboard), and the
unbounded per-user lock dict should become TTL/bounded. Current single-worker,
flag-off deployment is unaffected.


## 2026-06-14 — PR #25 review §3：写接口补「凭证骑乘」门禁

云端镜像单 `app` 用户、单 HOME，所以 `~/.codex/auth.json` /
`~/.claude/.credentials.json` 是**容器全局**文件，由某个 staff 一次
`codex login` / `claude login` 落地。review §3 指出：非 staff 云端用户能绕过
前端隐藏、直接打写接口建一张 OAuth 卡挂到 slot，运行时就骑乘了 staff 的共享
凭证（消耗额度、以其身份调用）。原先 `is_cloud and not is_staff` 门禁**只在两个
只读 status 路由**（`/claude-status` / `/codex-status`）里有，三个写接口裸奔。

**修法（精准门禁，不一刀切）**：抽出三个 helper —— `_OAUTH_CARD_TYPES =
{claude_oauth, codex_oauth}`、`_is_cloud()`、`_is_staff(request)`，两个只读路由
重构成复用它们（行为不变，去重）。新增两道写接口门禁：

- `add_provider`（`POST /api/providers`）：仅当 `card_type ∈ _OAUTH_CARD_TYPES
  且 _is_cloud() 且非 staff` → 403。**API-key 卡（anthropic/openai/custom）放行**
  —— 它带的是用户自己的 key，永不碰共享文件。
- `set_agent_framework`（`POST /api/providers/agent-framework`）：cloud 非 staff
  一律 403（切到某 framework 后若无 API-key provider，运行时会回退到共享 OAuth
  文件，间接骑乘）。

**为什么 `onboard` 不加门禁**：`onboard_one_key` 强制要求 `api_key`，只会建
API-key 卡，永远产生不了 OAuth 卡 → 无骑乘风险。一刀切会误伤本 PR 招牌的云端
自助 onboarding，故只精准封堵 OAuth 向量。门禁都在 `_get_service()`/DB 调用
**之前**触发。

**前端跟进（待办）**：`ProviderSettings.tsx` 的 framework `<select>` 在云端非
staff 时应隐藏/禁用，否则切换会收到 403 报错。后端门禁是安全边界，前端隐藏是
体验优化，二者独立。

测试：`tests/backend/test_provider_oauth_gating.py`（9 个，DB-free，stub
`_get_service` 区分「过门禁」与门禁自身的 403）。**未动**的死路：§1/§2 沙箱
（codex 原生 approval 只有 auto_review/deny_all 两档，给不了 workspace 内放行+
越界拒的中间档，见 `xyz_codex_official_sdk.py.md` 2026-06-14 条目）。

## 2026-06-11 — /codex-status 不再把"文件存在"当"已登录"

`get_codex_status` 原来 `logged_in = creds_file.is_file()`——文件在就报已
登录，连已经读出来的 `expires_at` 都不用。后果：token 过期后 auth.json 还
在，页面照报 "Logged in / ✓ auth ready"，而每轮 codex turn 实际 `unauthorized`
失败（incident 2026-06-11，把用户误导了很久）。

新增 `_expiry_is_past(raw)`：能**确定性**解析（epoch 秒 / 毫秒 / ISO-8601）
且在过去 → `logged_in=False` + `expired=True`；**解析不了一律 fail-open**
（宁可少报，也不误锁正常会话）。result 新增 `expired` 字段。

局限（设计上绕不开）：本地读文件**抓不住 "refresh token already used"**——
那种情况 access token 可能还没到 expires_at，只有真实调用才知道死了。那条路
由 runtime 的 `auth_expired` 错误分类兜（见 response_processor /
step_3_agent_loop 的 2026-06-11 条目）。测试：tests/backend/test_codex_status_route.py。

## 2026-06-10 (later) — framework auth probe recognises the API-key leg

`_probe_agent_framework_auth(framework, user_id=None)` previously only
checked the CLI OAuth credentials file (~/.codex/auth.json /
~/.claude/.credentials.json) — so a perfectly runnable API-key user
(the one-key onboarding path!) was told "✗ auth missing, run codex
login". Now it checks TWO legs in order: (1) the user's agent slot is
wired to a provider with a real api_key matching the framework's
protocol → ok with "API-key provider configured (name)"; (2) the OAuth
file probe, whose failure detail now also mentions the API-key
alternative. Both GET and POST /agent-framework pass user_id.

## 2026-06-10 — POST /api/providers/onboard

One-key setup endpoint. All orchestration lives in
`UserProviderService.onboard_one_key` (route = HTTP envelope + the same
hot-reload + `schedule_user_no_quota_rearm` as add_provider). Response
carries provider_type / agent_framework / agent_model / helper_model so
the frontend can confirm what was wired. Hot-reload calls now pass
`cfg.anthropic_helper` as the 4th set_user_config arg.

## 2026-06-10 — merge `dev`: `/embeddings/*` routes removed

`dev` retired embeddings (BM25 routing) and deleted
`embedding_migration_service.py`. The `/embeddings/status` and
`/embeddings/rebuild` routes that the codex branch still carried are gone
(their backing service no longer exists). The hot-reload calls in
`add_provider` / `set_slot` now pass `set_user_config(cfg.claude,
cfg.openai, cfg.codex)` — no embedding arg. `Query` import dropped (only
the removed routes used it).

## 2026-06-10 — Framework-neutral reasoning params (feat/claude-sdk-adapter-upgrade)

SlotConfig gained two NEUTRAL knobs — `thinking: ""|on|off` and
`reasoning_effort: ""|low|medium|high|max` ("" = auto = adapter passes
nothing). They are deliberately NOT provider dialect (no "adaptive"/
"minimal"): NarraNexus will adapt more frameworks (Codex, pi, ...), so the
slot stores semantics and each agent-framework adapter owns the mapping +
clamping (rule #9). Persisted as `user_slots.params_json` (cloud) and via
the normal LLMConfig JSON dump (local llm_config.json) — both backends
expose them through the same set_slot(..., thinking=, reasoning_effort=)
signature with PUT semantics (omitted = reset to auto). Corrupt or
out-of-vocabulary stored params degrade to auto with a warning instead of
failing config load. Tests: tests/agent_framework/test_slot_reasoning_params.py.


## 2026-06-09 — funnel redesign: providers.py carries no analytics

`llm_slot_configured` was removed entirely from the lean funnel redesign.
`providers.py` has no analytics instrumentation — no event is emitted from
any route here. The service layer (`UserProviderService`) is also clean.
The mid-funnel events tracking LLM configuration detail were cut to simplify
the funnel to 5 lean events.

## 2026-06-08 (late evening) — `_ensure_codex_installed` 不再走 npm

`_ensure_codex_installed` 之前是个 60+ 行的 `npm install -g @openai/codex` 流程，包含 cloud-mode 拒绝 / timeout / PATH 验证一堆分支。**全删了，只剩 ~20 行**。

根因：cutover 到 v2 之后，`openai-codex` 是 pyproject.toml 硬依赖，它**transitively 拉 `openai-codex-cli-bin` wheel**——codex 二进制以 Python wheel 形式打包，落到 `site-packages/codex_cli_bin/bin/codex`，**不在 PATH 上但 SDK 通过 `bundled_codex_path()` 直接定位**。npm install 路径完全是 v1 时代的死代码。

更糟的是它在 DMG 上**误报 install_failed**：DMG 容器内没有 npm，旧实现先 `shutil.which("codex")` 找不到（因为 wheel 路径不在 PATH），然后 `shutil.which("npm")` 找不到，返回 `install_failed` ——可是 SDK 用的是 wheel bundle，**实际跑得通**。用户看到红 banner 但 codex 正常工作，binding rule #7 (DMG + bash 必须一致) 违规。

新实现：

```python
async def _ensure_codex_installed() -> dict:
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError as e:
        return {"installed": False, "action": "install_failed",
                "reason": f"openai-codex-cli-bin wheel not importable ({e}). Run uv sync."}
    binary = bundled_codex_path()
    if not binary.exists():
        return {"installed": False, "action": "install_failed",
                "reason": f"codex_cli_bin imported but binary missing at {binary}. Re-run uv sync."}
    return {"installed": True, "action": "already_installed", "reason": ""}
```

`action` 值集合从 4 个收敛到 2 个：`already_installed` / `install_failed`。`auto_installed` 和 `blocked` 不再产生（前者是 npm 成功，后者是 cloud 拒绝；wheel 路径都不需要）。前端 banner UI 对应简化，移除 auto_installed / blocked 两条分支。Settings 的"Verifying Codex CLI…"提示也从原来 30-60s 改成瞬时（wheel 验证只是 import + Path.exists，毫秒级）。

测试文件 `test_agent_framework_install.py` 完全重写：从 7 个 npm-mock 测试改成 5 个 wheel-mock 测试。

## 2026-06-08 (evening) — A/B 别名清理

`set_agent_framework` 和 `_probe_agent_framework_auth` 之前都接 `("codex_cli", "codex_cli_v2", "codex_official")` 三个名字，现在收敛到只认 `codex_cli`。所有 codex 变种共用同一 `~/.codex/auth.json` 和同一 `_ensure_codex_installed()` 副作用——别名集合留着没意义。`_SUPPORTED_AGENT_FRAMEWORKS` 通过 import service 层常量自动跟着收窄到 `(claude_code, codex_cli)`。

带 A/B 老名字的 DB 行 (`agent_framework='codex_cli_v2'` 等) 现在会失败——按 binding rule #2 选了 "fail loud, user re-picks" 而不是 silent startup migration。

## 2026-06-08 — Route 层 `_SUPPORTED_AGENT_FRAMEWORKS` 不再独立维护

之前 `backend/routes/providers.py` 在第 358 行硬编码了 `("claude_code", "codex_cli")` 一份白名单，而 service 层 `UserProviderService._SUPPORTED_AGENT_FRAMEWORKS` 已经扩到了 4 个名字（codex_cli_v2 / codex_official 是 v2 别名）。结果前端 dropdown 选 v2 → route 层 400 "Unknown framework"，而 service 层根本接收得了。两份白名单永远会漂。

修法：route 层直接 import service 层的常量当 single source of truth：

```python
from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService as _UserProviderServiceForFrameworks,
)
_SUPPORTED_AGENT_FRAMEWORKS = _UserProviderServiceForFrameworks._SUPPORTED_AGENT_FRAMEWORKS
```

附带的两处也同步：
- `_probe_agent_framework_auth(framework)`: codex 分支匹配 `("codex_cli", "codex_cli_v2", "codex_official")` —— v1/v2 共用 `~/.codex/auth.json`，probe 走同一 `CodexOAuthDriver`
- `set_agent_framework` 的 `_ensure_codex_installed()` 触发条件: 同上 —— v2 SDK 内部还是 spawn `codex` 二进制（app-server 模式），install 副作用对 v2 同样必要

**铁律：framework 名字白名单四处必须同步**：
1. `agent_framework/__init__.py` register_agent_loop_driver
2. `provider_driver/resolver._KNOWN_AGENT_FRAMEWORKS` + `_CODEX_FRAMEWORK_VALUES`
3. `user_provider_service._SUPPORTED_AGENT_FRAMEWORKS` （= 后端 single source of truth）
4. `frontend/src/components/settings/ProviderSettings.tsx` 的 `AGENT_FRAMEWORKS` + `CODEX_FRAMEWORK_IDS`

route 层 #4 后已经不算独立条目了，因为 import 自动跟 service。但 frontend 不能 import Python，仍需手 sync。

## 2026-05-18 — 关掉 query 参数 user_id 这条 identity channel

`_get_user_id` 以前同时认两个 user_id 源：`request.state.user_id`（middleware 设的）和 query 参数 `user_id`。这俩并存就是 IDOR 漏洞：客户端可以一边发 `X-User-Id: bob`、一边 `?user_id=alice`，让 backend 在不同分支看到不同身份。本次彻底关掉 query 通道——`_get_user_id` 只读 `request.state.user_id`，缺失就 401。所有 endpoint 也删掉了 `user_id: Optional[str] = Query(None)` 参数。

身份只能来自一个 channel：cloud=JWT、local=X-User-Id header。前端 ApiClient (`api.ts:getAuthHeaders`) 和 SettingsProviders 的 `authFetch` 现在都会同时发这两个 header（取决于 mode）。

例外：`/embeddings/status` 和 `/embeddings/rebuild` 仍然有 `user_id: str = Query(...)`，但它的语义是 **target user**（管理员视角的"我要查谁的"），不是 identity。后续可以加 staff 角色 check。

# routes/providers.py — LLM 提供商与 Slot 配置路由

## 为什么存在

系统支持多个 LLM 提供商（Anthropic、OpenAI 及兼容 API）和多个使用"槽位"（Slot）：主推理、嵌入向量、工具调用等。每个用户有自己独立的提供商配置，存储在 `user_providers` 和 `user_slots` 表里。这个路由提供提供商的增删查和 Slot 指配操作，以及两个特殊功能：Claude Code CLI 登录状态检查和嵌入向量迁移。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(providers_router, prefix="/api/providers")`；前端设置面板；`backend/auth.py` 的 `AUTH_EXEMPT_PATHS` 包含 `/api/providers/claude-status`
- **依赖谁**：
  - `UserProviderService`（来自 `xyz_agent_context.agent_framework.user_provider_service`）— 所有提供商和 Slot 操作
  - `xyz_agent_context.agent_framework.model_catalog` — 获取已知模型列表和建议值
  - `xyz_agent_context.schema.provider_schema` — `LLMConfig`、`SlotName`、`SLOT_REQUIRED_PROTOCOLS`
  - `xyz_agent_context.agent_framework.api_config` — 热重载配置（本地进程内）
  - `EmbeddingMigrationService` — 嵌入向量重建

## 设计决策

**`_get_user_id` 单一身份源**（2026-05-18 重写）

user_id **只能**从 `request.state.user_id` 读，由 `auth_middleware` 在 handler 跑之前注入：cloud 模式来自 JWT decode，local 模式来自 `X-User-Id` header。没有任何 fallback——middleware 在 header 缺失时已经 401 了，handler 拿到的一定是用户明确声明的身份。

历史版本接受 query 参数 `?user_id=` 作为 backup，是为了 local 模式"少改前端"。结果是双 channel 并存、互相覆盖，写错 user_id 的 bug 在 2026-05-18 被踩到（详见 `backend/auth.py.md` 同日 entry）。这条 channel 现在彻底关闭。

**api_key 脱敏**

响应里的 api_key 被替换为 `"***" + 末4位`（`api_key_masked`），原始 `api_key` 字段被删除。这防止前端或日志意外暴露完整 key。

**添加提供商后立即热重载**

`add_provider` 和 `set_slot` 成功后会调用 `get_user_runtime_llm_configs` + `set_user_config` 来更新当前进程的 LLM 配置。用 runtime resolver 而不是旧三元组 resolver 的原因是 Codex agent 还需要把 `CodexConfig` 注入当前 task 的 ContextVar。这在 local 模式下有意义（单进程，热更新生效），在云模式多进程环境下实际上只更新了处理这次请求的进程，其他进程不受影响。注释说"Hot-reload for current process (local mode)"，但代码在任何模式下都执行，用 try/except 忽略了可能的失败。

**`claude-status` 豁免认证**

这个接口在 `backend/auth.py` 的 `AUTH_EXEMPT_PATHS` 里，不需要 JWT。原因是前端需要在登录之前就能检查 Claude Code CLI 状态（用于显示安装引导）。但在云模式下，它检查了 `request.state.role == 'staff'`，只允许 staff 使用 CLI——这个检查依赖中间件注入的 role，但由于豁免了认证，cloud 模式下 `request.state.role` 可能不存在，`getattr` 用了默认空字符串来避免 AttributeError。

**`claude-status` 返回字段：cli_installed / logged_in / email / expires_at**

email 和 expires_at 是 best-effort 解析——`claude auth status` 的 JSON
schema 在 minor 版本之间会变（有时把 email 放在顶层，有时放在
`account` / `user` / `profile`，有时根本没有；expiresAt 同样可能在顶层
或藏在 `token` / `oauth` / `credentials` 子对象里），所以代码逐一探测
常见 shape，解析不出来就保持 None。前端能渲染缺失字段（不显示而已），
所以这里宁可放过也不抛错。fallback 路径（直接读 `~/.claude/.credentials.json`）
也会顺手补一次 email/expires_at，覆盖 `claude auth status` 不可用或返回
不完整的 v1.x CLI 场景。

`claude auth status` 必须通过 async subprocess 执行，不能在 async FastAPI
handler 里用同步 `subprocess.run`。这个 CLI 在未登录、网络慢或自身卡住时会等到
10 秒超时；如果同步等待，会冻结整个 backend event loop，同一批 Settings 请求
里的 `/api/providers` 读 DB Proxy 也会被拖到超时，表现为 `httpx.ReadError` 和
500。

## Gotcha / 边界情况

- **`validate_slots` 检查所有 SlotName 但不校验提供商**：它只检查每个 Slot 是否配置了，不验证对应提供商的 API key 是否有效。真正的连通性测试用 `test_provider`。
- **`/slots/validate` 路径优先级**：这是 `GET /slots/validate`，需要在 `PUT /slots/{slot_name}` 之前注册（不同方法，实际上不冲突），但需要在 `GET /{provider_id}` 这类动态路径之前，否则 "slots" 会被当成 provider_id。FastAPI 在同一路由器内优先匹配更具体的路径，所以这里实际上没问题，但路径命名容易让人困惑。

## 新人易踩的坑

`/embeddings/rebuild` 和 `/embeddings/status` **都接受 `?user_id=...` 查询参数**
且必填。`EmbeddingMigrationService(db, user_id=user_id)` 按该 user 过滤所有
entity。`get_migration_progress(user_id)` 是 per-user 字典，用户 A 的 rebuild
不会阻塞用户 B 的状态查询或 rebuild。2026-04-20 之前这两个端点无 user_id 参数，
migration service 全局单例对云端多用户是错的。

多进程部署下，per-user 进度仍然是"当前处理这次请求的进程"内的状态；不同进程不
共享。前端轮询时若请求 load-balance 到不同进程，会看到进度波动。未来可考虑把
进度落到 DB 或 Redis，但本轮修复不包括。

## 2026-07-02（Phase 5）— POST /use-subscription（模块 F）

一键“使用此订阅”：cloud 门禁（规范 is_cloud_mode）+ 功能开关
`settings.netmind_use_subscription_enabled`（默认关，待 C1）+ X-Netmind-Token +
**先显式去重**（已有 source=netmind → 409，避免生成孤儿 key）→
[[netmind_key_client]] 生成推理 key → 复用 `onboard_one_key(uid, key, "netmind")`
建双 provider + 绑槽 → 抄 /onboard 的 hot-reload + rearm。错误：KeyAuthError→401、
KeyUpstreamError→502、onboard ValueError→400。

## 审查加固（2026-07-02）

- **per-user 锁** `_use_sub_lock(uid)`：串行化 dedup+mint+onboard，挡同进程双击/多标签
  并发双 mint（安全/质量 HIGH TOCTOU）。**仅同进程**——多 worker 需分布式/DB 守卫，
  flag-flip 前 TODO（注：(user_id,source) 唯一约束不适用，netmind 是双行）。
- **孤儿 key 清理**：onboard 任何失败后 `key_client.delete_key(token, minted.token_id)`
  best-effort 撤销刚 mint 的 key，避免留下花钱的孤儿（安全 HIGH）。
- **ValueError 映射细化**：dedup "already exists"→409、我方 mint 的 key 被 NetMind 拒
  "rejected"→502（上游集成失败，非用户输入错）、其它→400。

## 2026-07-07 — POST /onboard 支持 replace + needs_replace 信号

`OnboardRequest` 加 `replace: bool=False`，透传给 `onboard_one_key`。当服务返回
`meta.needs_replace` 时，路由返回 `{success: False, needs_replace: True, provider_type,
existing_masked}`（**HTTP 200**，非错误），让前端按结构化字段分支弹确认，而不是解析
"already exists" 错误串。确认后前端带 `replace=true` 重发，服务原子换 key。

## 2026-07-07 (bug#3) — hot-reload 传 cli_helper

add/onboard/set-slot/use-subscription 的 4 处 `set_user_config(cfg...)` 均增传 `cfg.cli_helper`，订阅（OAuth）helper 才能在当前进程即时生效。OAuth 登录后 add_provider 自动绑定 agent+helper 两槽。
