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
// type-only import on purpose: providersApi imports @/lib/api at runtime,
// and a value import here would create an agentFramework -> providersApi
// -> api runtime cycle (api.ts only back-references types).
import type { ProviderRow } from '@/lib/providersApi'

// ---- shared types --------------------------------------------------------

// The row shape has ONE definition (providersApi.ProviderRow); this module
// used to carry a fourth structural copy that three consumers cast into.
export type ProviderSummary = ProviderRow

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
  /**
   * Plugin-install gate (Claude Code / Codex CLI are user-installed local
   * plugins — see `getPlugins` / Settings › Plugins). `undefined`/`true` =
   * available; `false` = the plugin isn't installed, so the picker must
   * still LIST it (never hide it — the user needs to see what to install)
   * but render it `disabled`. Populated by `withFrameworkAvailability`,
   * never set directly on `AGENT_FRAMEWORKS`.
   */
  available?: boolean
}

/**
 * One entry of the LIVE `frameworks[]` array from
 * `GET/POST /api/providers/agent-framework` — the shape `providerBacksFramework`
 * / `availableFrameworks` read. The actual API response type (`lib/api.ts`
 * `getAgentFramework`) carries more fields (`available`, `display_name`); this
 * is declared as its own narrow type here so callers don't need to import
 * `lib/api.ts` just to type the parameter they're forwarding.
 */
export interface LiveFrameworkEntry {
  name: string
  /**
   * The backend's three-valued enum (`FrameworkMeta.protocol`): a specific
   * protocol, or `'any'` for a framework that drives the provider API itself
   * and works with either (NexusPower is sent as `'any'`).
   */
  protocol?: 'anthropic' | 'openai' | 'any'
  oauth_source?: string | null
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
 * Can this provider card actually back the AGENT slot under `framework`?
 * Frontend twin of backend ``provider_schema.framework_can_drive_provider``
 * (the gate inside ``validate_slot_binding``) — protocol first, then the
 * subscription-credential rule.
 *
 * API-key / bearer cards pass the second gate untouched: whether an endpoint
 * serves a framework WELL is the provider's characteristic, not something we
 * police (binding rule #15). This only refuses what is technically
 * unredeemable — and would otherwise fail in the middle of a run.
 *
 * `frameworks` (B6, 2026-09-07 final cut): the LIVE list from
 * `GET/POST /api/providers/agent-framework`, each entry now carrying its own
 * `protocol` and `oauth_source`. This is the ONLY source consulted for both
 * gates — the old hardcoded mirrors (`AGENT_FRAMEWORKS`-as-protocol-table and
 * a `CLI_FRAMEWORK_BY_OAUTH_SOURCE` map) are gone. **No list → fails closed**
 * (`false`): a caller mid-load must not guess from a stale static copy, and
 * every call site that has the list loaded is expected to pass it.
 *
 * `protocol` is the backend's three-valued enum (`FrameworkMeta.protocol`):
 * `'anthropic'` / `'openai'` gate as expected; `'any'` (NexusPower's value —
 * it drives the provider API itself and works with either) accepts every
 * provider protocol. This is read straight off the live value, not branched
 * on framework id — no `AGENT_FRAMEWORKS`/id-based special case is consulted.
 */
export function providerBacksFramework(
  prov: Pick<ProviderSummary, 'source' | 'protocol' | 'auth_type'>,
  framework: string | null | undefined,
  frameworks: LiveFrameworkEntry[] | undefined,
): boolean {
  if (!frameworks) return false
  const fw = frameworks.find((f) => f.name === framework)
  if (fw?.protocol && fw.protocol !== 'any' && fw.protocol !== prov.protocol) {
    return false
  }
  if (!SUBSCRIPTION_AUTH_TYPES.includes(prov.auth_type)) return true
  const redeemer = frameworks.find((f) => f.oauth_source === prov.source)?.name
  // No framework resolved yet redeems nothing — never read "unset" as a
  // hardcoded Claude Code (on the lightweight build it may not be installed).
  return !!framework && framework === redeemer
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
 *
 * `frameworks` (B6, 2026-09-07 final cut): forwarded straight into
 * `providerBacksFramework` — see that function's doc for the fail-closed
 * behavior when it is `undefined` (every non-`current` framework is filtered
 * out, same as "nothing in the wallet can drive it").
 */
export function availableFrameworks(
  providers: Array<Pick<ProviderSummary, 'source' | 'protocol' | 'auth_type'>>,
  current: string,
  frameworks: LiveFrameworkEntry[] | undefined,
): AgentFramework[] {
  if (!providers.length) return AGENT_FRAMEWORKS
  return AGENT_FRAMEWORKS.filter(
    (f) =>
      f.id === current || providers.some((p) => providerBacksFramework(p, f.id, frameworks)),
  )
}

/**
 * Turns the `frameworks` array from `GET/POST /api/providers/agent-framework`
 * into a name→available lookup. A framework the backend didn't mention
 * (older backend that predates plugin gating, or the array is entirely
 * absent) is treated as available elsewhere — this map only records what
 * the backend explicitly said, so the caller's "default true" policy lives
 * in one place (`withFrameworkAvailability`) instead of being duplicated at
 * every read site.
 */
export function frameworkAvailabilityMap(
  frameworks: Array<{ name: string; available: boolean }> | undefined,
): Record<string, boolean> {
  const map: Record<string, boolean> = {}
  for (const f of frameworks ?? []) map[f.name] = f.available
  return map
}

/**
 * Attaches the plugin-install gate to a framework list, defaulting to
 * available when the map has no entry for that framework id. This NEVER
 * hides a framework — unlike `availableFrameworks` (which hides frameworks
 * no bindable provider can drive), an uninstalled plugin still needs to
 * render so the user has something to click "install" on. The two gates
 * compose by calling this on the output of `availableFrameworks`.
 */
export function withFrameworkAvailability(
  frameworks: AgentFramework[],
  availability: Record<string, boolean>,
): AgentFramework[] {
  return frameworks.map((f) => ({
    ...f,
    available: f.id in availability ? availability[f.id] : true,
  }))
}

/** Whether a framework (already merged via `withFrameworkAvailability`) may
 *  be selected right now — `undefined` (map had no entry, or was never
 *  merged) counts as available. */
export function isFrameworkAvailable(framework: Pick<AgentFramework, 'available'>): boolean {
  return framework.available !== false
}

// What the helper_llm "Default (recommended)" resolves to per protocol.
// Mirrors backend ``_ONBOARD_HELPER_MODELS`` in model_catalog.py.
export const RECOMMENDED_HELPER_MODEL_BY_PROTOCOL: Record<string, string> = {
  openai: 'gpt-5.4-mini',
  anthropic: 'claude-haiku-4-5',
}

// Per-SOURCE onboarding defaults. The table is SERVER-owned (model_catalog.py
// ``get_onboard_defaults``: every source with an onboarding default; netmind_free
// follows the FREE_TIER_*_MODEL ops env). ``refreshOnboardDefaults``
// (lib/onboardDefaults.ts) loads it from GET /api/providers/catalog ->
// ``onboard_defaults`` and the panels keep it in React state, passing it to the
// pickers below as an explicit argument -- there is no module-level state, and
// this file holds NO per-source model ids, so a backend default change needs no
// frontend edit (the per-PROTOCOL helper table above is a separate,
// frontend-owned fallback). With ``{}`` (not loaded) every pick falls back to the
// first listed model -- deterministic, never an empty model. NetMind's synced
// list starts with an omni model, which is why the panels report a failed load
// instead of silently living on that fallback.
export type OnboardDefaults = Record<string, { agent?: string; helper?: string }>

// Default agent-slot model when a provider is picked: the source's onboarding
// default when it is on the card, else the first listed model.
export function defaultAgentModel(
  source: string | undefined,
  modelIds: string[],
  defaults: OnboardDefaults,
): string {
  const rec = defaults[source || '']?.agent
  if (rec && modelIds.includes(rec)) return rec
  return modelIds[0] || ''
}

// The DELIBERATE helper pick for a provider — a model somebody chose on purpose
// that is on the card: the source's server-owned onboarding default, the
// protocol's recommended cheap model, or the OAuth CLI alias (OAuth providers
// list family aliases, so the concrete protocol id may be absent — map those to
// the alias the backend auto-bind uses: claude→haiku, codex→gpt-5.4-mini).
// '' when none of them is on the card.
function deliberateHelperModel(
  source: string | undefined,
  protocol: string | undefined,
  modelIds: string[],
  defaults: OnboardDefaults,
): string {
  const bySource = defaults[source || '']?.helper
  if (bySource && modelIds.includes(bySource)) return bySource
  const rec = RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[protocol || 'openai']
  if (rec && modelIds.includes(rec)) return rec
  if (source === 'claude_oauth' && modelIds.includes('haiku')) return 'haiku'
  if (source === 'codex_oauth' && modelIds.includes('gpt-5.4-mini')) return 'gpt-5.4-mini'
  return ''
}

// Default helper-slot model when a provider is picked: the deliberate pick,
// never the flagship models[0] by preference (the helper does small structured
// jobs) — the first listed model only when no deliberate pick is on the card.
export function defaultHelperModel(
  source: string | undefined,
  protocol: string | undefined,
  modelIds: string[],
  defaults: OnboardDefaults,
): string {
  return deliberateHelperModel(source, protocol, modelIds, defaults) || modelIds[0] || ''
}

// The "Recommended: ..." helper hint. It names only a DELIBERATE pick: unlike the
// auto-pick it must never fall back to the list head (that would read "Recommended:
// claude-opus-4-8 — a small, fast model" on a curated flagship card, or the omni
// model on a NetMind card). When no deliberate pick is on the card it shows the
// protocol-table model instead.
export function recommendedHelperModel(
  source: string | undefined,
  protocol: string | undefined,
  modelIds: string[],
  defaults: OnboardDefaults,
): string {
  return (
    deliberateHelperModel(source, protocol, modelIds, defaults) ||
    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[protocol || 'openai'] ||
    'gpt-5.4-mini'
  )
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
