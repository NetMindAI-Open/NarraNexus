/**
 * agentFramework — shared types, constants, and helpers for the LLM
 * provider/slot UI, used by both the user-level Settings editor
 * (ProviderSettings) and the per-agent chat surfaces (ComposerModelBadge,
 * AgentLlmConfigPanel).
 *
 * Single source of truth for the agent-framework list, the codex-curated
 * model set / allowed sources, the recommended helper models, and the
 * slot→models resolution — so a per-agent override offers exactly the same
 * choices the Settings default editor does.
 */

import { isForcedCloud } from './runtimeConfig'

// ---- shared types --------------------------------------------------------

export interface ProviderSummary {
  provider_id: string
  name: string
  source: string
  protocol: string
  auth_type: string
  is_active: boolean
  models: string[]
  api_key_masked?: string
  base_url?: string
}

export interface KnownModelMeta {
  model_id: string
  display_name: string
  max_output_tokens: number | null
}

export interface AgentFramework {
  id: string
  /** Primary protocol — the one a picker shows and defaults to. */
  protocol: string
  /**
   * Every protocol this framework can drive. CLI-backed frameworks speak
   * exactly one (claude_code → anthropic, codex_cli → openai) because
   * their CLI does; NexusPower drives the provider API itself and works
   * with either, so it must not be filtered down to one.
   */
  protocols?: string[]
  label: string
  desc: string
}

/** Whether a provider's protocol can back this framework. */
export function frameworkAcceptsProtocol(
  framework: AgentFramework | undefined,
  protocol: string | undefined,
): boolean {
  if (!framework || !protocol) return true
  const accepted = framework.protocols ?? [framework.protocol]
  return accepted.includes(protocol)
}

// ---- constants -----------------------------------------------------------

// One framework name per agent kind. ``codex_cli`` runs the official
// ``openai-codex`` Python SDK under the hood.
export const AGENT_FRAMEWORKS: AgentFramework[] = [
  { id: 'claude_code', label: 'Claude Code', protocol: 'anthropic', desc: 'Claude Agent SDK via Claude Code CLI' },
  { id: 'codex_cli', label: 'Codex CLI', protocol: 'openai', desc: 'Official openai-codex SDK — streaming reasoning + RPC interrupt' },
  {
    id: 'nexus_power',
    label: 'NexusPower-beta',
    protocol: 'anthropic',
    protocols: ['anthropic', 'openai'],
    desc: 'Our own loop — replies stream as they are written, live plan, on-demand capabilities',
  },
]

// NexusPower predicate — the surfaces that render its exclusive streams
// (reply deltas, plan cards) branch on this instead of scattered
// ``=== 'nexus_power'`` comparisons.
export const isNexusPowerFramework = (framework: string | null | undefined): boolean =>
  framework === 'nexus_power'

// Codex-framework predicate — kept as a helper so a future v3 framework id
// lands in one spot instead of scattered ``=== 'codex_cli'`` comparisons.
export const isCodexFramework = (framework: string | null | undefined): boolean =>
  framework === 'codex_cli'

// Where the "use your own keys in the local version" notes point. The DMG is
// published by the release workflow to the public NetMindAI-Open repo.
export const DESKTOP_RELEASES_URL = 'https://github.com/NetMindAI-Open/NarraNexus/releases'

/**
 * Cloud netmind-only slot policy — the frontend twin of the route gates in
 * backend/routes/providers.py + agents_llm_config.py: a non-staff cloud user
 * may only bind NetMind-CAPACITY providers to slots. Bring-your-own API keys
 * can still be REGISTERED (the wallet stays open) but run in the local
 * (desktop) version only. Staff keeps full choice; local deployments are
 * never gated. Both slot editors (ModelDefaultsSettings and
 * AgentLlmConfigPanel) filter their provider dropdowns through this so the
 * UI never offers a choice the backend would 403.
 */
export function cloudNetmindOnly(role: string | undefined): boolean {
  return isForcedCloud() && role !== 'staff'
}

/**
 * Provider sources a cloud non-staff user may BIND to a slot — the mirror of
 * backend ``cloud_policy.CLOUD_BINDABLE_SOURCES``. Both are NetMind capacity:
 * `netmind` is the user's own Power account, `netmind_free` is the
 * platform-funded free-tier wallet reaching the same upstream through our
 * gateway.
 *
 * Exported as a PREDICATE, not a constant to compare against, because the
 * literal `p.source !== 'netmind'` was previously inlined in four places —
 * and when `netmind_free` was added backend-side, all four kept filtering the
 * free card out of every dropdown. A user could hold a working free provider
 * and be unable to select it anywhere.
 */
export function isSlotBindableSource(source: string | undefined): boolean {
  return source === 'netmind' || source === 'netmind_free'
}

/**
 * Frameworks a cloud non-staff user may select — the frontend twin of
 * backend ``cloud_policy.CLOUD_ALLOWED_FRAMEWORKS``.
 *
 * The gate is about CREDENTIAL RIDING, not framework variety: the
 * CLI-backed frameworks authenticate through a credential file in a HOME
 * the cloud image shares between users, so a non-staff user selecting one
 * would run on staff's login. NexusPower drives the provider API with the
 * key of the card bound to the agent slot and refuses subscription OAuth
 * outright, so it carries no such risk.
 *
 * Exported as a PREDICATE for the same reason `isSlotBindableSource` is:
 * the rule was previously inlined as `!== 'claude_code'` in two pickers,
 * and when NexusPower became cloud-legal both kept rejecting it.
 */
export const CLOUD_ALLOWED_FRAMEWORKS = ['claude_code', 'nexus_power']

export function frameworkAllowedInCloud(
  framework: string,
  role: string | undefined,
): boolean {
  if (!cloudNetmindOnly(role)) return true
  return CLOUD_ALLOWED_FRAMEWORKS.includes(framework)
}

/**
 * Auth types that carry a CLI SUBSCRIPTION credential instead of an API key —
 * `oauth` (the CLI's own credential store) and `oauth_token` (a setup-token
 * env-injected at spawn). Mirror of backend
 * ``provider_schema.SUBSCRIPTION_AUTH_TYPES``.
 */
export const SUBSCRIPTION_AUTH_TYPES = ['oauth', 'oauth_token']

/**
 * Subscription card → the ONE framework that can spend it. Mirror of backend
 * ``provider_schema.CLI_FRAMEWORK_BY_OAUTH_SOURCE``.
 *
 * A subscription login is a credential for a specific CLI, not a generic
 * provider key: only the `claude` CLI can redeem a Claude Code Login, only
 * `codex` a Codex CLI Login. NexusPower drives the provider HTTP API and
 * refuses subscription credentials outright, so it appears in no value here.
 */
export const CLI_FRAMEWORK_BY_OAUTH_SOURCE: Record<string, string> = {
  claude_oauth: 'claude_code',
  codex_oauth: 'codex_cli',
}

/**
 * Can this provider card actually back the AGENT slot under `framework`?
 * Frontend twin of backend ``provider_schema.framework_can_drive_provider``
 * (the gate inside ``validate_slot_binding``) — protocol first, then the
 * subscription-credential rule.
 *
 * API-key / bearer cards pass the second gate untouched: whether an endpoint
 * serves a framework WELL is the provider's characteristic, not something we
 * police (binding rule #15). This only refuses what is technically
 * unredeemable — and would otherwise fail in the middle of a run.
 */
export function providerBacksFramework(
  prov: Pick<ProviderSummary, 'source' | 'protocol' | 'auth_type'>,
  framework: string | null | undefined,
): boolean {
  const fw = AGENT_FRAMEWORKS.find((f) => f.id === framework)
  if (!frameworkAcceptsProtocol(fw, prov.protocol)) return false
  if (!SUBSCRIPTION_AUTH_TYPES.includes(prov.auth_type)) return true
  return (framework || 'claude_code') === CLI_FRAMEWORK_BY_OAUTH_SOURCE[prov.source]
}

/**
 * The frameworks worth OFFERING for this wallet: a framework nothing in the
 * provider list can drive is a dead end (pick it and every provider option
 * disappears), so it is hidden rather than left to fail at save time. Pass the
 * providers the user may actually BIND — on cloud that is the
 * `isSlotBindableSource` subset, not the whole wallet.
 *
 * Two deliberate escape hatches:
 *   - `providers` empty → no filtering. A user who hasn't added a card yet
 *     must not face an empty framework dropdown.
 *   - `current` is always kept, even when nothing can drive it. Dropping the
 *     selected value would silently re-point a `<select>` at another
 *     framework without the user (or the backend) ever agreeing to it.
 *
 * The cloud staff-only rule is deliberately NOT applied here: those pickers
 * keep listing a staff-only framework and answer the pick with an explanation
 * (see `frameworkAllowedInCloud`'s call sites), which reads friendlier than a
 * silently shorter list. This function answers a different question — "can any
 * card of mine run it at all".
 */
export function availableFrameworks(
  providers: Array<Pick<ProviderSummary, 'source' | 'protocol' | 'auth_type'>>,
  current: string,
): AgentFramework[] {
  if (!providers.length) return AGENT_FRAMEWORKS
  return AGENT_FRAMEWORKS.filter(
    (f) =>
      f.id === current || providers.some((p) => providerBacksFramework(p, f.id)),
  )
}

// What the helper_llm "Default (recommended)" resolves to per protocol.
// Mirrors backend ``_ONBOARD_HELPER_MODELS`` in model_catalog.py.
export const RECOMMENDED_HELPER_MODEL_BY_PROTOCOL: Record<string, string> = {
  openai: 'gpt-5.4-mini',
  anthropic: 'claude-haiku-4-5',
}

// Default helper-slot model when a provider is picked: the recommended CHEAP
// model, never the flagship models[0] (the helper does small structured jobs).
// OAuth providers list CLI family aliases, so the concrete
// RECOMMENDED_HELPER_MODEL_BY_PROTOCOL id may be absent — map those to the alias
// the backend auto-bind uses (claude→haiku, codex→gpt-5.4-mini). Falls back to
// the first model only when none of the above is available.
export function defaultHelperModel(
  source: string | undefined,
  protocol: string | undefined,
  modelIds: string[],
): string {
  const rec = RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[protocol || 'openai']
  if (rec && modelIds.includes(rec)) return rec
  if (source === 'claude_oauth' && modelIds.includes('haiku')) return 'haiku'
  if (source === 'codex_oauth' && modelIds.includes('gpt-5.4-mini')) return 'gpt-5.4-mini'
  return modelIds[0] || ''
}

// Curated model list OpenAI's OWN codex backend (codex_oauth) accepts, gated by
// account tier. Scoped to codex_oauth ONLY — a third-party openai-protocol
// provider (netmind / yunwu / openrouter / custom base_url) exposes its own
// catalogue. Must stay in sync with backend ``CODEX_CURATED_MODELS`` in
// providers/user_service.py (same codex_oauth-only scoping).
export const CODEX_CURATED_MODELS = ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini']

export interface ModelSuggestionGroup {
  label: string
  models: string[]
}

export const MODEL_SUGGESTION_GROUPS: ModelSuggestionGroup[] = [
  {
    label: 'Anthropic',
    models: [
      'claude-opus-4-8',
      'claude-sonnet-4-6',
      'claude-haiku-4-5',
      'claude-haiku-4-5-20251001',
    ],
  },
  {
    label: 'OpenAI',
    models: [
      'gpt-5.4',
      'gpt-5.4-mini',
      'gpt-5.4-nano',
      'gpt-5.2',
      'gpt-5.2-mini',
      'gpt-5.1',
      'gpt-5',
      'gpt-4.1',
      'o4-mini',
      'o3',
    ],
  },
  {
    label: 'Google Gemini',
    models: [
      'gemini-3.1-pro-preview',
      'gemini-3.1-pro-preview-customtools',
      'gemini-3-flash-preview',
      'gemini-3.1-flash-lite-preview',
      'gemini-2.5-pro',
      'gemini-2.5-flash',
      'gemini-2.5-flash-lite',
      'gemini-deep-research-preview',
      'gemini-deep-research-max-preview',
    ],
  },
  {
    label: 'Zhipu / GLM',
    models: ['glm-5.1', 'glm-5', 'glm-5-turbo'],
  },
  {
    label: 'Kimi (Moonshot)',
    models: ['kimi-k2.6'],
  },
  {
    label: 'Qwen (DashScope)',
    models: ['qwen3.6-max-preview', 'qwen3.6-plus', 'qwen3.6-flash'],
  },
  {
    label: 'MiniMax',
    models: ['MiniMax-M2.7', 'MiniMax-M2.7-highspeed', 'MiniMax-M2.5'],
  },
  {
    label: 'DeepSeek',
    models: ['deepseek-v4-pro', 'deepseek-v4-flash'],
  },
]

// Reasoning-param option vocabularies (framework-neutral; '' = Auto).
export const THINKING_OPTIONS: Array<'' | 'on' | 'off'> = ['', 'on', 'off']
export const REASONING_EFFORT_OPTIONS: Array<'' | 'low' | 'medium' | 'high' | 'max'> = [
  '', 'low', 'medium', 'high', 'max',
]

// ---- helpers -------------------------------------------------------------

/** "deepseek-ai/DeepSeek-V4-Pro" → "DeepSeek-V4-Pro"; "default"/"" → "default". */
export function prettifyModel(model: string | null | undefined): string {
  if (!model || model === 'default') return 'default'
  return model.includes('/') ? model.split('/').pop() || model : model
}

/**
 * Models offered for (provider, slot) under the current agent framework.
 *
 * Agent slot + codex_cli on the ``codex_oauth`` provider → the codex-curated
 * set (OpenAI's account-tier check is the real gate, mirrors backend
 * get_user_config). Every other provider — including third-party
 * openai-protocol aggregators (netmind / yunwu / openrouter) and custom
 * base_urls — exposes ITS OWN model list. ``knownModels`` supplies display
 * names.
 */
export function getModelsForSlot(
  prov: ProviderSummary,
  slotKey: string,
  agentFramework: string | null | undefined,
  knownModels: Record<string, KnownModelMeta>,
): Array<{ model_id: string; display_name: string }> {
  if (
    slotKey === 'agent' &&
    isCodexFramework(agentFramework) &&
    prov.source === 'codex_oauth'
  ) {
    return CODEX_CURATED_MODELS.map((mid) => ({
      model_id: mid,
      display_name: knownModels[mid]?.display_name || mid,
    }))
  }
  return prov.models.map((mid) => ({
    model_id: mid,
    display_name: knownModels[mid]?.display_name || mid,
  }))
}
