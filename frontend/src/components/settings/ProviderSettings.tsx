/**
 * @file ProviderSettings.tsx
 * @description LLM Provider configuration for the web frontend Settings modal
 *
 * Layout (always expanded, no collapsed state):
 *
 *   ┌─────────────────────────────────────────┐
 *   │  SECTION 1: Add Providers               │
 *   │  ┌ Quick Add (preset selector + key) ─┐ │
 *   │  │ Claude Code Login card              │ │
 *   │  │ + Anthropic / + OpenAI buttons      │ │
 *   │  │ Configured Providers list           │ │
 *   │  └────────────────────────────────────-┘ │
 *   ├─────────────────────────────────────────┤
 *   │  SECTION 2: Model Assignment            │
 *   │  ┌ Agent slot ────────────────────────┐ │
 *   │  │ Embedding slot                     │ │
 *   │  │ Helper LLM slot                   │ │
 *   │  │ Apply / Discard                    │ │
 *   │  └───────────────────────────────────-┘ │
 *   └─────────────────────────────────────────┘
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { useConfigStore } from '@/stores'
import { getApiBaseUrl } from '@/stores/runtimeStore'
import { QuotaPanel } from './QuotaPanel'

/** fetch wrapper that injects JWT auth header when available (cloud mode) */
function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  try {
    const raw = localStorage.getItem('narra-nexus-config')
    if (raw) {
      const token = JSON.parse(raw)?.state?.token
      if (token) headers.set('Authorization', `Bearer ${token}`)
    }
  } catch {}
  return fetch(input, { ...init, headers })
}

// =============================================================================
// Types
// =============================================================================

interface ProviderSummary {
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

interface SlotConfig {
  provider_id: string
  model: string
}

interface SlotData {
  config: SlotConfig | null
  required_protocols: string[]
}

interface KnownModelMeta {
  model_id: string
  display_name: string
  dimensions: number | null
  max_output_tokens: number | null
}

interface EmbeddingModelInfo {
  model_id: string
  display_name: string
  dimensions: number
}

// =============================================================================
// Preset providers (web-side copy — must match backend card_type support)
// =============================================================================

interface WebPresetProvider {
  id: string       // card_type sent to backend
  name: string
  description: string
  get_key_url: string
}

const PRESET_PROVIDERS: WebPresetProvider[] = [
  { id: 'netmind',    name: 'NetMind.AI Power', description: 'One key covers both Anthropic & OpenAI endpoints', get_key_url: 'https://www.netmind.ai/user/dashboard' },
  { id: 'yunwu',      name: 'Yunwu',            description: 'Proxies official Claude & OpenAI APIs',           get_key_url: 'https://yunwu.ai' },
  { id: 'openrouter', name: 'OpenRouter',       description: 'Proxies official Claude & OpenAI APIs',           get_key_url: 'https://openrouter.ai/keys' },
]

// =============================================================================
// Agent Framework definitions
// =============================================================================

interface AgentFramework {
  id: string
  label: string
  protocol: string
  desc: string
}

const AGENT_FRAMEWORKS: AgentFramework[] = [
  { id: 'claude_code', label: 'Claude Code', protocol: 'anthropic', desc: 'Claude Agent SDK via Claude Code CLI' },
]

const SLOT_DEFS: { key: string; label: string; desc: string; protocol: string }[] = [
  { key: 'agent', label: 'Agent', desc: 'Main dialogue (Anthropic)', protocol: 'anthropic' },
  { key: 'embedding', label: 'Embedding', desc: 'Vector search (OpenAI)', protocol: 'openai' },
  { key: 'helper_llm', label: 'Helper LLM', desc: 'Auxiliary tasks (OpenAI)', protocol: 'openai' },
]

// =============================================================================
// Model Bubble Tag Input
// =============================================================================

function ModelBubbleInput({
  models, onChange, placeholder = 'model name'
}: {
  models: string[]; onChange: (m: string[]) => void; placeholder?: string
}) {
  const [input, setInput] = useState('')
  const addModel = () => {
    const v = input.trim()
    if (v && !models.includes(v)) onChange([...models, v])
    setInput('')
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {models.map((m) => (
        <span key={m} className="inline-flex items-center gap-1 px-2.5 py-1 text-sm rounded-full bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 whitespace-nowrap">
          {m}
          <button onClick={() => onChange(models.filter((x) => x !== m))} className="text-[var(--accent-primary)]/50 hover:text-[var(--accent-primary)]">&times;</button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1">
        <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addModel() } }}
          placeholder={placeholder}
          style={{ width: Math.max(100, (input.length + 1) * 8) }}
          className="px-2.5 py-1 text-sm rounded-full border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
        <button onClick={addModel} disabled={!input.trim()}
          className="px-2 py-1 text-sm rounded-full border border-[var(--accent-primary)]/20 text-[var(--accent-primary)] bg-[var(--accent-primary)]/5 hover:bg-[var(--accent-primary)]/10 disabled:opacity-30">
          +
        </button>
      </span>
    </div>
  )
}

// =============================================================================
// Section Header
// =============================================================================

function SectionHeader({ step, title, subtitle }: { step: number; title: string; subtitle: string }) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-2.5 mb-1">
        <span className="w-6 h-6 rounded-full bg-[var(--accent-primary)]/15 text-[var(--accent-primary)] text-sm font-semibold flex items-center justify-center">
          {step}
        </span>
        <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
      </div>
      <p className="text-sm text-[var(--text-tertiary)] ml-[34px]">{subtitle}</p>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export function ProviderSettings() {
  const userId = useConfigStore((s) => s.userId)

  /** Build a provider API URL with user_id query param.
   *
   * IMPORTANT: getApiBaseUrl() is called INSIDE the callback (not captured at
   * component mount), so it always reflects the current mode. When the user
   * switches between local and cloud, every fresh call returns the right host
   * without needing to re-mount this component. */
  const providerUrl = useCallback((path: string = '') => {
    const sep = path.includes('?') ? '&' : '?'
    return `${getApiBaseUrl()}/api/providers${path}${sep}user_id=${encodeURIComponent(userId)}`
  }, [userId])

  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [knownModels, setKnownModels] = useState<Record<string, KnownModelMeta>>({})
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModelInfo[]>([])
  const [officialBaseUrls, setOfficialBaseUrls] = useState<Record<string, string[]>>({})
  const [error, setError] = useState('')
  const [claudeStatus, setClaudeStatus] = useState<{ cli_installed: boolean; logged_in: boolean; expires_at: string | null } | null>(null)

  // Quick Add (preset provider)
  const [selectedPreset, setSelectedPreset] = useState<string>(PRESET_PROVIDERS[0].id)
  const [presetKey, setPresetKey] = useState('')
  const [presetAdding, setPresetAdding] = useState(false)

  // Protocol form
  const [showForm, setShowForm] = useState<'anthropic' | 'openai' | null>(null)
  const [formName, setFormName] = useState('')
  const [formUrl, setFormUrl] = useState('')
  const [formKey, setFormKey] = useState('')
  const [formAuth, setFormAuth] = useState<'api_key' | 'bearer_token'>('api_key')
  const [formModels, setFormModels] = useState<string[]>([])
  const [formAdding, setFormAdding] = useState(false)

  // Agent framework
  const [agentFramework, setAgentFramework] = useState<string>(AGENT_FRAMEWORKS[0].id)

  // Pending slot changes (local draft, not yet submitted)
  const [pendingSlots, setPendingSlots] = useState<Record<string, SlotConfig>>({})
  const [applying, setApplying] = useState(false)

  // Testing
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const [cfgRes, catRes, claudeRes] = await Promise.all([
        authFetch(providerUrl()).then((r) => r.json()),
        authFetch(providerUrl('/catalog')).then((r) => r.json()),
        authFetch(providerUrl('/claude-status')).then((r) => r.json()).catch(() => null),
      ])
      if (claudeRes?.success) setClaudeStatus(claudeRes.data)
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
        setSlots(cfgRes.data.slots)
        setPendingSlots({})
      }
      if (catRes.success) {
        setKnownModels(catRes.known_models)
        if (catRes.embedding_models) setEmbeddingModels(catRes.embedding_models)
        if (catRes.official_base_urls) setOfficialBaseUrls(catRes.official_base_urls)
      }
    } catch {}
  }, [providerUrl])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  const hasClaude = providerList.some((p) => p.source === 'claude_oauth')

  // Check which preset providers are already added
  const addedPresets = new Set(providerList.map((p) => p.source))

  // Compute effective config per slot: pending overrides server state
  const getEffectiveSlotConfig = (slotKey: string): SlotConfig | null => {
    if (pendingSlots[slotKey]) return pendingSlots[slotKey]
    return slots[slotKey]?.config || null
  }

  const allSlotsReady = SLOT_DEFS.every((s) => {
    const cfg = getEffectiveSlotConfig(s.key)
    return cfg?.provider_id && cfg?.model
  })

  const hasPendingChanges = Object.keys(pendingSlots).length > 0

  // ---- Provider actions ----
  const addProvider = async (body: Record<string, unknown>) => {
    setError('')
    try {
      const res = await authFetch(providerUrl(), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || 'Failed'); return false }
      await refreshConfig()
      return true
    } catch { setError('Network error'); return false }
  }

  const handleQuickAdd = async () => {
    if (!presetKey.trim()) { setError('Please enter your API key'); return }
    setPresetAdding(true)
    const ok = await addProvider({ card_type: selectedPreset, api_key: presetKey.trim() })
    if (ok) setPresetKey('')
    setPresetAdding(false)
  }

  const handleAddClaudeOAuth = async () => {
    await addProvider({ card_type: 'claude_oauth' })
  }

  const handleAddProtocol = async () => {
    if (!showForm || !formKey.trim()) { setError('Enter API key'); return }
    setFormAdding(true)
    const ok = await addProvider({
      card_type: showForm,
      name: formName.trim() || undefined,
      api_key: formKey.trim(),
      base_url: formUrl.trim(),
      auth_type: formAuth,
      models: formModels,
    })
    if (ok) {
      setShowForm(null); setFormName(''); setFormUrl(''); setFormKey(''); setFormAuth('api_key'); setFormModels([])
    }
    setFormAdding(false)
  }

  const handleDelete = async (id: string) => {
    await authFetch(providerUrl(`/${id}`), { method: 'DELETE' })
    setPendingSlots((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(next)) {
        if (v.provider_id === id) delete next[k]
      }
      return next
    })
    await refreshConfig()
  }

  const handleTest = async (id: string) => {
    setTesting(id)
    try {
      const res = await authFetch(providerUrl(`/${id}/test`), { method: 'POST' }).then((r) => r.json())
      setTestResults((p) => ({ ...p, [id]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResults((p) => ({ ...p, [id]: { ok: false, msg: 'Network error' } }))
    }
    setTesting(null)
  }

  // Local slot change
  const handleLocalSlotChange = (slot: string, pid: string, model: string) => {
    setPendingSlots((prev) => ({ ...prev, [slot]: { provider_id: pid, model } }))
  }

  // Apply all pending slot changes to backend
  const handleApply = async () => {
    setApplying(true)
    setError('')
    try {
      for (const [slot, cfg] of Object.entries(pendingSlots)) {
        const res = await authFetch(providerUrl(`/slots/${slot}`), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_id: cfg.provider_id, model: cfg.model }),
        }).then((r) => r.json())
        if (!res.success) {
          setError(`Failed to set ${slot}: ${res.detail || 'Unknown error'}`)
          break
        }
      }
      await refreshConfig()
    } catch {
      setError('Network error applying changes')
    }
    setApplying(false)
  }

  const handleDiscard = () => { setPendingSlots({}) }

  const openForm = (protocol: 'anthropic' | 'openai') => {
    setShowForm(protocol)
    setFormName('')
    setFormUrl(protocol === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
    setFormKey(''); setFormAuth('api_key'); setFormModels([]); setError('')
  }

  const getProvidersForSlot = (protocol: string) =>
    providerList.filter((p) => p.protocol === protocol && p.is_active)

  const isOfficialProvider = (prov: ProviderSummary) => {
    const urls = officialBaseUrls[prov.protocol] || []
    return urls.includes(prov.base_url || '')
  }

  const getModelsForSlot = (prov: ProviderSummary, slotKey: string) => {
    if (slotKey === 'embedding') {
      if (prov.source === 'netmind') {
        return embeddingModels.filter((em) => prov.models.includes(em.model_id))
      }
      return embeddingModels.filter((em) => em.model_id.startsWith('text-embedding-'))
    }
    return prov.models
      .filter((mid) => !knownModels[mid]?.dimensions)
      .map((mid) => ({ model_id: mid, display_name: knownModels[mid]?.display_name || mid }))
  }

  // ---- Slot row renderer ----
  const renderSlotRow = (slot: typeof SLOT_DEFS[number]) => {
    const selectedFramework = AGENT_FRAMEWORKS.find((f) => f.id === agentFramework)
    const effectiveProtocol = slot.key === 'agent' && selectedFramework
      ? selectedFramework.protocol
      : slot.protocol

    const cfg = getEffectiveSlotConfig(slot.key)
    const ready = !!(cfg?.provider_id && cfg?.model)
    const matching = getProvidersForSlot(effectiveProtocol)
    const curProv = cfg?.provider_id ? providers[cfg.provider_id] : null
    const isChanged = !!pendingSlots[slot.key]

    return (
      <div key={slot.key} className={cn('p-4 rounded-xl border',
        isChanged ? 'border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/5' :
        ready ? 'border-[var(--color-success)]/20 bg-[var(--color-success)]/5' : 'border-[var(--color-error)]/20 bg-[var(--color-error)]/5'
      )}>
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium text-[var(--text-primary)]">
            {slot.label}
            <span className="text-[var(--text-tertiary)] font-normal ml-2">{slot.desc}</span>
          </span>
          <div className="flex items-center gap-2">
            {isChanged && <span className="text-xs text-[var(--accent-primary)]">modified</span>}
            {ready
              ? <span className="text-[var(--color-success)] text-base">{'\u2713'}</span>
              : <span className="text-sm text-[var(--color-error)]">Needed</span>}
          </div>
        </div>

        {/* Agent Framework selector */}
        {slot.key === 'agent' && (
          <div className="mb-3">
            <label className="block text-sm text-[var(--text-tertiary)] mb-1">Agent Framework</label>
            <select
              value={agentFramework}
              onChange={(e) => setAgentFramework(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
            >
              {AGENT_FRAMEWORKS.map((fw) => (
                <option key={fw.id} value={fw.id}>{fw.label} — {fw.desc}</option>
              ))}
            </select>
          </div>
        )}

        {matching.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {/* Provider dropdown */}
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">Provider</label>
              <select value={cfg?.provider_id || ''}
                onChange={(e) => {
                  const pid = e.target.value
                  const prov = providers[pid]
                  if (!prov) return
                  const slotModels = getModelsForSlot(prov, slot.key)
                  if (slot.key === 'helper_llm' && isOfficialProvider(prov)) {
                    handleLocalSlotChange(slot.key, pid, 'default')
                  } else if (slotModels.length > 0) {
                    handleLocalSlotChange(slot.key, pid, slotModels[0].model_id)
                  } else {
                    handleLocalSlotChange(slot.key, pid, '')
                  }
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                <option value="">Select provider...</option>
                {matching.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
              </select>
            </div>

            {/* Model dropdown */}
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">Model</label>
              {(() => {
                if (!curProv) return (
                  <select disabled className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-tertiary)] outline-none">
                    <option>Select provider first...</option>
                  </select>
                )

                if (slot.key === 'embedding') {
                  const emModels = embeddingModels.filter((em) => curProv.models.includes(em.model_id))
                  return (
                    <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                      <option value="">Select embedding model...</option>
                      {emModels.map((em) => <option key={em.model_id} value={em.model_id}>{em.display_name} ({em.dimensions}d)</option>)}
                    </select>
                  )
                }

                if (slot.key === 'helper_llm' && isOfficialProvider(curProv)) {
                  const llmModels = getModelsForSlot(curProv, 'helper_llm')
                  return (
                    <>
                      <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                        className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                        <option value="default">Default (recommended)</option>
                        {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                      </select>
                      {cfg?.model && cfg.model !== 'default' && (
                        <p className="text-xs text-[var(--color-warning)] mt-1">All auxiliary tasks will use this model. May affect speed/cost.</p>
                      )}
                    </>
                  )
                }

                const llmModels = getModelsForSlot(curProv, slot.key)
                if (llmModels.length > 0) {
                  return (
                    <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                      <option value="">Select model...</option>
                      {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                    </select>
                  )
                }

                return (
                  <input type="text" value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                    placeholder="Enter model name"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                )
              })()}
            </div>
          </div>
        ) : (
          <p className="text-sm text-[var(--color-error)]">
            No {effectiveProtocol} protocol provider configured. Add one in Step 1 above.
          </p>
        )}
      </div>
    )
  }

  // ---- Full view (always expanded) ----
  return (
    <div className="space-y-8">

      {/* System free-tier quota — renders only in cloud mode + feature on */}
      <QuotaPanel />

      {/* ================================================================= */}
      {/* SECTION 1: Add Providers                                          */}
      {/* ================================================================= */}
      <div>
        <SectionHeader
          step={1}
          title="Add Providers"
          subtitle="Add API keys for your LLM providers. Each preset creates both Anthropic and OpenAI protocol endpoints automatically."
        />

        <div className="space-y-4 ml-[34px]">
          {/* ---- Quick Add: Preset provider selector ---- */}
          <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
            <h4 className="text-sm font-medium text-[var(--text-primary)] mb-1">Quick Add — Preset Provider</h4>
            <p className="text-sm text-[var(--text-tertiary)] mb-3">
              Select a provider, paste your API key, and both protocol endpoints will be created automatically.
            </p>

            <div className="space-y-3">
              {/* Provider selector */}
              <div>
                <label className="block text-sm text-[var(--text-secondary)] mb-1">Provider</label>
                <select
                  value={selectedPreset}
                  onChange={(e) => setSelectedPreset(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                >
                  {PRESET_PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.description}
                    </option>
                  ))}
                </select>
              </div>

              {/* API Key + Get Key + Add button */}
              <div className="flex gap-2">
                <input type="password" value={presetKey} onChange={(e) => setPresetKey(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleQuickAdd() }}
                  placeholder={addedPresets.has(selectedPreset) ? 'New key to re-configure...' : 'Paste your API key'}
                  className="flex-1 px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                <a
                  href={PRESET_PROVIDERS.find((p) => p.id === selectedPreset)?.get_key_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-2 text-sm text-[var(--accent-primary)] bg-[var(--accent-primary)]/10 rounded-lg hover:bg-[var(--accent-primary)]/20 whitespace-nowrap transition-colors"
                >
                  Get Key
                </a>
                <button onClick={handleQuickAdd} disabled={presetAdding}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--accent-primary)] text-white hover:bg-[var(--accent-primary)]/90 disabled:opacity-40 transition-colors">
                  {presetAdding ? 'Adding...' : addedPresets.has(selectedPreset) ? 'Update' : 'Add'}
                </button>
              </div>
            </div>
          </div>

          {/* ---- Claude Code Login Card ---- */}
          <div className="p-4 rounded-xl border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="text-sm font-medium text-[var(--text-primary)]">Claude Code Login</h4>
              {hasClaude && <span className="text-[var(--color-success)] text-sm ml-auto">{'\u2713'} Added</span>}
            </div>
            <p className="text-sm text-[var(--text-tertiary)] mb-2">OAuth login via Claude Code CLI. No API key needed.</p>
            {!hasClaude && claudeStatus && (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className={cn('inline-block w-2 h-2 rounded-full',
                    claudeStatus.logged_in ? 'bg-[var(--color-success)]' :
                    claudeStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
                  )} />
                  <span className="text-sm text-[var(--text-secondary)]">
                    {claudeStatus.logged_in ? 'Logged in' : claudeStatus.cli_installed ? 'Not logged in' : 'CLI not installed'}
                  </span>
                </div>
                {claudeStatus.logged_in && (
                  <button onClick={handleAddClaudeOAuth}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--accent-primary)] text-white hover:bg-[var(--accent-primary)]/90 transition-colors">
                    Add as Provider
                  </button>
                )}
                {!claudeStatus.logged_in && (
                  <p className="text-sm text-[var(--text-tertiary)]">
                    {claudeStatus.cli_installed
                      ? 'Run "claude login" in your terminal first, then refresh this page.'
                      : 'Install Claude Code CLI first, then run "claude login" in your terminal.'}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* ---- Custom: Add Protocol Buttons ---- */}
          <div className="flex gap-2">
            <button onClick={() => openForm('anthropic')}
              className="flex-1 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors">
              + Custom Anthropic
            </button>
            <button onClick={() => openForm('openai')}
              className="flex-1 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors">
              + Custom OpenAI
            </button>
          </div>

          {/* ---- Protocol Form ---- */}
          {showForm && (
            <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-[var(--text-primary)]">
                  Custom {showForm === 'anthropic' ? 'Anthropic' : 'OpenAI'} Provider
                </h4>
                <button onClick={() => setShowForm(null)} className="text-sm text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">Cancel</button>
              </div>
              <p className="text-sm text-[var(--text-tertiary)]">
                {showForm === 'anthropic' ? 'Anthropic API or any compatible endpoint.' : 'OpenAI API or any compatible endpoint.'}
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">Provider Name</label>
                  <input type="text" value={formName} onChange={(e) => setFormName(e.target.value)}
                    placeholder={showForm === 'anthropic' ? 'e.g., Anthropic' : 'e.g., OpenAI'}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                </div>
                {showForm === 'anthropic' ? (
                  <div>
                    <label className="block text-sm text-[var(--text-tertiary)] mb-1">Auth Type</label>
                    <select value={formAuth} onChange={(e) => setFormAuth(e.target.value as 'api_key' | 'bearer_token')}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                      <option value="api_key">API Key</option>
                      <option value="bearer_token">Bearer Token</option>
                    </select>
                  </div>
                ) : <div />}
              </div>
              <div>
                <label className="block text-sm text-[var(--text-tertiary)] mb-1">Base URL</label>
                <input type="text" value={formUrl} onChange={(e) => setFormUrl(e.target.value)}
                  placeholder="Base URL"
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
              </div>
              <div>
                <label className="block text-sm text-[var(--text-tertiary)] mb-1">API Key</label>
                <input type="password" value={formKey} onChange={(e) => setFormKey(e.target.value)}
                  placeholder="Your API key"
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
              </div>
              <div>
                <label className="block text-sm text-[var(--text-tertiary)] mb-1">Available Models</label>
                <ModelBubbleInput models={formModels} onChange={setFormModels} />
              </div>
              <button onClick={handleAddProtocol} disabled={formAdding || !formKey.trim()}
                className="w-full py-2.5 text-sm font-medium rounded-lg bg-[var(--accent-primary)] text-white hover:bg-[var(--accent-primary)]/90 disabled:opacity-40 transition-colors">
                {formAdding ? 'Adding...' : 'Add Provider'}
              </button>
            </div>
          )}

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

          {/* ---- Configured Providers List ---- */}
          {hasProviders && (
            <div className="space-y-2">
              <span className="text-xs text-[var(--text-tertiary)] uppercase tracking-wider font-medium">
                Configured Providers
              </span>
              {providerList.map((prov) => (
                <div key={prov.provider_id} className="flex items-center justify-between p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-primary)]">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)] truncate">{prov.name}</span>
                      <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase">{prov.protocol}</span>
                    </div>
                    <span className="text-sm text-[var(--text-tertiary)]">{prov.api_key_masked} · {prov.models.length} model(s)</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                      className="px-3 py-1.5 text-sm text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 rounded-lg disabled:opacity-40 transition-colors">
                      {testing === prov.provider_id ? '...' : 'Test'}
                    </button>
                    <button onClick={() => handleDelete(prov.provider_id)}
                      className="px-3 py-1.5 text-sm text-[var(--color-error)] hover:bg-[var(--color-error)]/5 rounded-lg transition-colors">
                      Delete
                    </button>
                    {testResults[prov.provider_id] && (
                      <span className={cn('text-sm', testResults[prov.provider_id].ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                        {testResults[prov.provider_id].msg}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ================================================================= */}
      {/* SECTION 2: Model Assignment                                        */}
      {/* ================================================================= */}
      {hasProviders && (
        <div>
          <SectionHeader
            step={2}
            title="Model Assignment"
            subtitle="Assign a provider and model to each functional slot. All three must be configured for the agent to work."
          />

          <div className="space-y-3 ml-[34px]">
            {SLOT_DEFS.map((slot) => renderSlotRow(slot))}

            {/* Apply / Discard buttons */}
            {hasPendingChanges && (
              <div className="flex gap-3 pt-2">
                <button
                  onClick={handleApply}
                  disabled={applying}
                  className={cn(
                    'flex-1 py-2.5 text-sm font-medium rounded-lg transition-colors',
                    'bg-[var(--accent-primary)] text-white',
                    'hover:bg-[var(--accent-primary)]/90',
                    'disabled:opacity-40'
                  )}
                >
                  {applying ? 'Applying...' : 'Apply Changes'}
                </button>
                <button
                  onClick={handleDiscard}
                  disabled={applying}
                  className="px-6 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors"
                >
                  Discard
                </button>
              </div>
            )}

            {/* Status indicator */}
            {allSlotsReady && !hasPendingChanges && (
              <div className="flex items-center gap-2 p-3 rounded-xl bg-[var(--color-success)]/10 border border-[var(--color-success)]/20">
                <span className="text-[var(--color-success)] text-base">{'\u2713'}</span>
                <span className="text-sm text-[var(--color-success)]">All model slots configured — agent is ready.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
