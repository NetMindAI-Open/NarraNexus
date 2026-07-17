---
code_file: src/xyz_agent_context/agent_framework/provider_registry.py
last_verified: 2026-07-16
stub: false
---

## 2026-07-16 — `_interpret_test_response`: 自助类 400/404 不再当"可达"

原逻辑把 400/404/422 一律当"auth 通过=可达"(True)。但 NetMind 余额耗尽是 **400
`balance not enough`**、模型不存在 404、上下文过小 400——这些不是"可运行"。现在该分支复用
`llm_failure.classify_self_serviceable(None, resp.text)`,命中就返回 **False**(单一事实源,
不与 Job/实时层漂移)。这样 `ProviderReadiness.validate` 的实测能正确判"余额死掉=not ready",
edge 恢复(登录/provider 保存)就不会把余额死掉的 job 拉起来白跑一次;充值后实测 200 → 恢复。
onboarding 不受影响(只按 auth 短语硬拒;余额死 key 仍"unverified"通过),"test connection"
按钮则显示真实原因而非误导的 OK。见 `.mindflow/project/references/netmind_billing.md`。

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

# provider_registry.py — 本地单机 LLM provider 配置管理

## 为什么存在

本地单机部署时，用户的 API key 和模型配置需要持久化到磁盘（`~/.nexusagent/llm_config.json`）。Settings 页面支持 5 种 provider "卡片"（NetMind、Yunwu、OpenRouter、Claude OAuth、自定义 Anthropic/OpenAI），每种卡片对应不同的创建逻辑（NetMind 一个 key 生成两个 provider：anthropic 和 openai 协议）。这个文件封装了这些逻辑，提供原子的 add/remove/validate 操作，并包含 provider 连接测试能力。

## 上下游关系

上游：`backend/routes/` 中的 provider API 路由，用于处理前端 Settings 页面的操作。`api_config.py` 的 `_load_from_llm_config()` 在初始化时调用 `provider_registry.load()`。

下游：`~/.nexusagent/llm_config.json` 文件，以及通过 `model_catalog.get_default_models()` 预填充各 provider 的模型列表。

和 `user_provider_service.py` 的关系：这个文件处理**本地单机**场景（`llm_config.json` 文件），`user_provider_service.py` 处理**云端多租户**场景（数据库）。两者接口相似（都有 `add_provider`、`set_slot`、`validate` 等），但存储和作用域不同。`user_provider_service.py` 在做连接测试时会复用这里的 `provider_registry.test_provider()` 方法。

## 设计决策

**linked_group 机制**：NetMind/Yunwu/OpenRouter 一个 API key 对应两个 provider（anthropic 协议 + openai 协议）。通过 `linked_group` 字段把它们关联，删除一个时自动删除另一个，避免孤立 provider 占用 slot。

**Unique 卡片原子替换**：NetMind/Yunwu/OpenRouter/Claude OAuth 是"唯一"卡片，重新添加时先删除旧的（`_remove_by_source`），再创建新的。这样避免多个同类 provider 并存造成混乱。

**连接测试策略分流**：官方端点（OpenAI、Anthropic 官方）用 GET /models（零 token 消耗），非官方代理用 POST 真实 chat completion（max_tokens=1）。`_interpret_test_response` 把 400/404/422 也算作"认证通过"（API 可达、只是 model/payload 问题），这是为了兼容一些代理端点不支持 /models 但认证正常的情况。

## Gotcha / 边界情况

- `add_provider()` 是原子操作（load → modify → save），但在并发情况下（两个请求同时 add）会有 last-write-wins 的竞态。本地单机场景并发罕见，目前可接受。
- Slot 在 `_remove_by_source` 时被清除，这意味着删除 NetMind 后，之前分配给 NetMind provider 的所有 slot 都会失效，用户需要重新分配。

## 新人易踩的坑

- `validate()` 只检查"三个 slot 是否都配置了"，不验证 API key 是否有效。连接测试是独立的 `test_provider()` 操作，不在 validate 流程里。
- `yunwu` 和 `openrouter` 在 `add_provider()` 里的处理方式和 `netmind` 完全相同（都是 unique + dual providers），但在 `_DUAL_PROVIDER_CONFIGS`（`user_provider_service.py` 里）和这里的 builder 函数里有各自独立的 base_url 硬编码，两处要同步维护。
