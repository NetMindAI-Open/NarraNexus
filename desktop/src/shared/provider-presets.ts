/**
 * @file provider-presets.ts
 * @description Data-driven preset provider registry for quick setup
 *
 * Each preset defines a provider that can fulfill one or more protocol endpoints
 * with a single API key. This registry is extensible — add new entries to
 * PRESET_PROVIDERS to make them available in the setup wizard's "Quick Setup" flow.
 *
 * Design: providers are protocol-agnostic containers. Each preset declares which
 * protocols it supports (anthropic / openai) along with the base_url and default
 * models for each. The `default_slots` field defines which model is auto-assigned
 * to each slot during Quick Setup — users don't need to pick models manually.
 */

// =============================================================================
// Types
// =============================================================================

/** A protocol endpoint that a preset provider supports */
export interface PresetProtocolEndpoint {
  /** The LLM protocol identifier */
  protocol: 'anthropic' | 'openai'
  /** Base URL for this protocol's API */
  base_url: string
  /** Default LLM model IDs available through this endpoint */
  models: string[]
  /** Embedding model IDs (only relevant for openai protocol endpoints) */
  embedding_models?: string[]
}

/** Default slot assignment — which model goes to which functional slot */
export interface PresetSlotDefaults {
  /** Agent slot: protocol + model for main AI dialogue */
  agent: { protocol: 'anthropic' | 'openai'; model: string }
  /** Embedding slot: protocol + model for vector search */
  embedding: { protocol: 'anthropic' | 'openai'; model: string }
  /** Helper LLM slot: protocol + model for auxiliary analysis */
  helper_llm: { protocol: 'anthropic' | 'openai'; model: string }
}

/** A preset provider — one API key that covers one or more protocol endpoints */
export interface PresetProvider {
  /** Unique identifier (used as card_type when calling the backend) */
  id: string
  /** Display name shown to users */
  name: string
  /** Short tagline explaining the value proposition */
  tagline: string
  /** Longer description for tooltip / help text */
  description: string
  /** URL where the user can obtain an API key */
  get_key_url: string
  /** Protocol endpoints this provider supports */
  endpoints: PresetProtocolEndpoint[]
  /** Whether this preset covers all 3 required slots (agent + embedding + helper_llm) */
  covers_all_slots: boolean
  /** Default model assignment per slot — auto-applied during Quick Setup */
  default_slots: PresetSlotDefaults
}

// =============================================================================
// Registry
// =============================================================================

/**
 * Preset providers available in the "Quick Setup" flow.
 *
 * To add a new provider:
 *   1. Append a new PresetProvider object to this array
 *   2. Ensure the backend's POST /api/providers handler supports the card_type
 *      (add a builder in provider_registry.py + default models in model_catalog.py)
 *   3. That's it — the setup wizard picks it up automatically
 */
export const PRESET_PROVIDERS: PresetProvider[] = [
  {
    id: 'netmind',
    name: 'NetMind.AI Power',
    tagline: 'One key, full coverage',
    description:
      'A single API key that provides both Anthropic-compatible and OpenAI-compatible endpoints. ' +
      'Covers Agent, Embedding, and Helper LLM — no extra configuration needed.',
    get_key_url: 'https://www.netmind.ai/user/dashboard',
    endpoints: [
      {
        protocol: 'anthropic',
        base_url: 'https://api.netmind.ai/inference-api/anthropic',
        models: ['minimax/minimax-m2.5'],
      },
      {
        protocol: 'openai',
        base_url: 'https://api.netmind.ai/inference-api/openai/v1',
        models: [
          'minimax/minimax-m2.5',
          'google/gemini-3.1-pro-preview',
          'google/gemini-3.1-flash-lite-preview',
          'moonshotai/Kimi-K2.5',
          'zai-org/GLM-5',
          'deepseek-ai/DeepSeek-V3',
        ],
        embedding_models: ['BAAI/bge-m3', 'nvidia/NV-Embed-v2', 'dunzhang/stella_en_1.5B_v5'],
      },
    ],
    covers_all_slots: true,
    default_slots: {
      agent:      { protocol: 'anthropic', model: 'minimax/minimax-m2.5' },
      embedding:  { protocol: 'openai',    model: 'nvidia/NV-Embed-v2' },
      helper_llm: { protocol: 'openai',    model: 'google/gemini-3.1-pro-preview' },
    },
  },
  {
    id: 'yunwu',
    name: 'Yunwu',
    tagline: 'Claude + OpenAI proxy',
    description:
      'Yunwu proxies official Claude and OpenAI APIs. One key gives you access to ' +
      'Claude Sonnet / Opus for the Agent, and GPT models + embeddings for everything else.',
    get_key_url: 'https://yunwu.ai',
    endpoints: [
      {
        protocol: 'anthropic',
        base_url: 'https://yunwu.ai',
        models: ['claude-sonnet-4-6', 'claude-opus-4-6'],
      },
      {
        protocol: 'openai',
        base_url: 'https://yunwu.ai/v1',
        models: ['gpt-5.1-2025-11-13'],
        embedding_models: ['text-embedding-3-small', 'text-embedding-3-large'],
      },
    ],
    covers_all_slots: true,
    default_slots: {
      agent:      { protocol: 'anthropic', model: 'claude-sonnet-4-6' },
      embedding:  { protocol: 'openai',    model: 'text-embedding-3-small' },
      helper_llm: { protocol: 'openai',    model: 'gpt-5.1-2025-11-13' },
    },
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    tagline: 'Claude + OpenAI proxy',
    description:
      'OpenRouter proxies official Claude and OpenAI APIs. One key gives you access to ' +
      'Claude Sonnet / Opus for the Agent, and GPT models + embeddings for everything else.',
    get_key_url: 'https://openrouter.ai/keys',
    endpoints: [
      {
        protocol: 'anthropic',
        base_url: 'https://openrouter.ai/api',
        models: ['claude-sonnet-4-6', 'claude-opus-4-6'],
      },
      {
        protocol: 'openai',
        base_url: 'https://openrouter.ai/api/v1',
        models: ['gpt-5.1-2025-11-13'],
        embedding_models: ['text-embedding-3-small', 'text-embedding-3-large'],
      },
    ],
    covers_all_slots: true,
    default_slots: {
      agent:      { protocol: 'anthropic', model: 'claude-sonnet-4-6' },
      embedding:  { protocol: 'openai',    model: 'text-embedding-3-small' },
      helper_llm: { protocol: 'openai',    model: 'gpt-5.1-2025-11-13' },
    },
  },
]
