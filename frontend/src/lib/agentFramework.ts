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
  label: string
  protocol: string
  desc: string
}

// ---- constants -----------------------------------------------------------

// One framework name per agent kind. ``codex_cli`` runs the official
// ``openai-codex`` Python SDK under the hood.
export const AGENT_FRAMEWORKS: AgentFramework[] = [
  { id: 'claude_code', label: 'Claude Code', protocol: 'anthropic', desc: 'Claude Agent SDK via Claude Code CLI' },
  { id: 'codex_cli', label: 'Codex CLI', protocol: 'openai', desc: 'Official openai-codex SDK — streaming reasoning + RPC interrupt' },
]

// Codex-framework predicate — kept as a helper so a future v3 framework id
// lands in one spot instead of scattered ``=== 'codex_cli'`` comparisons.
export const isCodexFramework = (framework: string | null | undefined): boolean =>
  framework === 'codex_cli'

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
// user_provider_service.py (same codex_oauth-only scoping).
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
