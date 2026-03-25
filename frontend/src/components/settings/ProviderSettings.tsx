/**
 * @file ProviderSettings.tsx
 * @description LLM Provider configuration for the web frontend (settings panel)
 *
 * Two-layer architecture (same as desktop ProviderConfigView):
 *   Layer 1 — Provider Atomic Cards: add/remove providers
 *   Layer 2 — Slot Model Selection: all 3 slots shown simultaneously
 *
 * Slot changes are accumulated locally and submitted together via Apply button,
 * so users can adjust multiple slots without the panel collapsing.
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'

const API_BASE = ''  // Relative — Vite proxy handles /api/*

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
// Agent Framework definitions
// =============================================================================

interface AgentFramework {
  id: string
  label: string
  protocol: string   // The LLM protocol this framework requires
  desc: string
}

const AGENT_FRAMEWORKS: AgentFramework[] = [
  { id: 'claude_code', label: 'Claude Code', protocol: 'anthropic', desc: 'Claude Agent SDK via Claude Code CLI' },
  // Future:
  // { id: 'openai_agents', label: 'OpenAI Agents SDK', protocol: 'openai', desc: 'OpenAI Agents SDK' },
]

const SLOT_DEFS: { key: string; label: string; desc: string; protocol: string }[] = [
  { key: 'agent', label: 'Agent', desc: 'Main dialogue', protocol: 'anthropic' },
  { key: 'embedding', label: 'Embedding', desc: 'Vector search (OpenAI protocol)', protocol: 'openai' },
  { key: 'helper_llm', label: 'Helper LLM', desc: 'Auxiliary tasks (OpenAI protocol)', protocol: 'openai' },
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
        <span key={m} className="inline-flex items-center gap-0.5 px-2 py-0.5 text-xs rounded-full bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 whitespace-nowrap">
          {m}
          <button onClick={() => onChange(models.filter((x) => x !== m))} className="text-[var(--accent-primary)]/50 hover:text-[var(--accent-primary)]">&times;</button>
        </span>
      ))}
      <span className="inline-flex items-center gap-0.5">
        <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addModel() } }}
          placeholder={placeholder}
          style={{ width: Math.max(80, (input.length + 1) * 7) }}
          className="px-2 py-0.5 text-xs rounded-full border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
        <button onClick={addModel} disabled={!input.trim()}
          className="px-1.5 py-0.5 text-xs rounded-full border border-[var(--accent-primary)]/20 text-[var(--accent-primary)] bg-[var(--accent-primary)]/5 hover:bg-[var(--accent-primary)]/10 disabled:opacity-30">
          +
        </button>
      </span>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export function ProviderSettings() {
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [knownModels, setKnownModels] = useState<Record<string, KnownModelMeta>>({})
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModelInfo[]>([])
  const [officialBaseUrls, setOfficialBaseUrls] = useState<Record<string, string[]>>({})
  const [error, setError] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  const [claudeStatus, setClaudeStatus] = useState<{ cli_installed: boolean; logged_in: boolean; expires_at: string | null } | null>(null)

  // NetMind card
  const [netmindKey, setNetmindKey] = useState('')
  const [netmindAdding, setNetmindAdding] = useState(false)

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
        fetch(`${API_BASE}/api/providers`).then((r) => r.json()),
        fetch(`${API_BASE}/api/providers/catalog`).then((r) => r.json()),
        fetch(`${API_BASE}/api/providers/claude-status`).then((r) => r.json()).catch(() => null),
      ])
      if (claudeRes?.success) setClaudeStatus(claudeRes.data)
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
        setSlots(cfgRes.data.slots)
        // Clear pending slots since we just loaded fresh state
        setPendingSlots({})
      }
      if (catRes.success) {
        setKnownModels(catRes.known_models)
        if (catRes.embedding_models) setEmbeddingModels(catRes.embedding_models)
        if (catRes.official_base_urls) setOfficialBaseUrls(catRes.official_base_urls)
      }
    } catch {}
  }, [])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  const hasNetMind = providerList.some((p) => p.source === 'netmind')
  const hasClaude = providerList.some((p) => p.source === 'claude_oauth')

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
      const res = await fetch(`${API_BASE}/api/providers`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || 'Failed'); return false }
      await refreshConfig()
      return true
    } catch { setError('Network error'); return false }
  }

  const handleAddNetMind = async () => {
    if (!netmindKey.trim()) { setError('Enter NetMind API Key'); return }
    setNetmindAdding(true)
    const ok = await addProvider({ card_type: 'netmind', api_key: netmindKey.trim() })
    if (ok) { setNetmindKey(''); setCollapsed(false) }
    setNetmindAdding(false)
  }

  const handleAddClaudeOAuth = async () => {
    await addProvider({ card_type: 'claude_oauth' })
    setCollapsed(false)
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
      setCollapsed(false)
    }
    setFormAdding(false)
  }

  const handleDelete = async (id: string) => {
    await fetch(`${API_BASE}/api/providers/${id}`, { method: 'DELETE' })
    // Clear any pending slots that reference the deleted provider
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
      const res = await fetch(`${API_BASE}/api/providers/${id}/test`, { method: 'POST' }).then((r) => r.json())
      setTestResults((p) => ({ ...p, [id]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResults((p) => ({ ...p, [id]: { ok: false, msg: 'Network error' } }))
    }
    setTesting(null)
  }

  // Local slot change — just update pending state, no API call
  const handleLocalSlotChange = (slot: string, pid: string, model: string) => {
    setPendingSlots((prev) => ({ ...prev, [slot]: { provider_id: pid, model } }))
  }

  // Apply all pending slot changes to backend
  const handleApply = async () => {
    setApplying(true)
    setError('')
    try {
      // Submit all pending slot changes sequentially (order doesn't matter but
      // sequential is safer for error handling)
      for (const [slot, cfg] of Object.entries(pendingSlots)) {
        const res = await fetch(`${API_BASE}/api/providers/slots/${slot}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_id: cfg.provider_id, model: cfg.model }),
        }).then((r) => r.json())
        if (!res.success) {
          setError(`Failed to set ${slot}: ${res.detail || 'Unknown error'}`)
          break
        }
      }
      await refreshConfig()
      // If all slots are ready after apply, collapse
      // (we check via refreshConfig's fresh data, but collapsed is set here)
      const cfgRes = await fetch(`${API_BASE}/api/providers`).then((r) => r.json())
      if (cfgRes.success) {
        const allReady = SLOT_DEFS.every(
          (s) => cfgRes.data.slots[s.key]?.config?.provider_id && cfgRes.data.slots[s.key]?.config?.model
        )
        if (allReady) setCollapsed(true)
      }
    } catch {
      setError('Network error applying changes')
    }
    setApplying(false)
  }

  // Discard pending changes
  const handleDiscard = () => {
    setPendingSlots({})
  }

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

  // ---- Collapsed summary ----
  if (collapsed && allSlotsReady && !hasPendingChanges) {
    return (
      <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded-full bg-[var(--color-success)]/20 flex items-center justify-center">
              <span className="text-[var(--color-success)] text-sm">{'\u2713'}</span>
            </div>
            <span className="text-sm text-[var(--text-secondary)]">LLM Providers configured</span>
          </div>
          <button onClick={() => setCollapsed(false)}
            className="px-2.5 py-1 text-xs font-medium text-[var(--accent-primary)] bg-[var(--accent-primary)]/10 rounded-lg hover:bg-[var(--accent-primary)]/20 transition-colors">
            Edit
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {SLOT_DEFS.map((s) => {
            const cfg = slots[s.key]?.config
            const prov = cfg?.provider_id ? providers[cfg.provider_id] : null
            return (
              <span key={s.key} className="text-xs px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">
                {s.label}: {prov?.name || '?'} / {cfg?.model || '?'}
              </span>
            )
          })}
        </div>
      </div>
    )
  }

  // ---- Slot row renderer (used for all 3 slots) ----
  const renderSlotRow = (slot: typeof SLOT_DEFS[number]) => {
    // For agent slot, protocol is driven by the selected framework
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
      <div key={slot.key} className={cn('p-3 rounded-lg border',
        isChanged ? 'border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/5' :
        ready ? 'border-[var(--color-success)]/20 bg-[var(--color-success)]/5' : 'border-[var(--color-error)]/20 bg-[var(--color-error)]/5'
      )}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-[var(--text-primary)]">
            {slot.label} <span className="text-[var(--text-tertiary)] font-normal">({slot.desc}, {effectiveProtocol})</span>
          </span>
          <div className="flex items-center gap-1.5">
            {isChanged && <span className="text-[10px] text-[var(--accent-primary)]">modified</span>}
            {ready ? <span className="text-[var(--color-success)] text-sm">{'\u2713'}</span> : <span className="text-xs text-[var(--color-error)]">Needed</span>}
          </div>
        </div>
        {/* Agent Framework selector (agent slot only) */}
        {slot.key === 'agent' && (
          <div className="mb-2">
            <label className="block text-[11px] text-[var(--text-tertiary)] mb-0.5">Agent Framework</label>
            <select
              value={agentFramework}
              onChange={(e) => setAgentFramework(e.target.value)}
              className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
            >
              {AGENT_FRAMEWORKS.map((fw) => (
                <option key={fw.id} value={fw.id}>{fw.label} — {fw.desc}</option>
              ))}
            </select>
            {AGENT_FRAMEWORKS.length <= 1 && (
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">More frameworks coming soon.</p>
            )}
          </div>
        )}

        {matching.length > 0 ? (
          <div className="grid grid-cols-2 gap-2">
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
              className="px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
              <option value="">Provider...</option>
              {matching.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
            </select>
            <div>
              {(() => {
                if (!curProv) return <select disabled className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-tertiary)] outline-none"><option>Select provider...</option></select>

                if (slot.key === 'embedding') {
                  const emModels = embeddingModels.filter((em) => curProv.models.includes(em.model_id))
                  return <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                    <option value="">Embedding model...</option>
                    {emModels.map((em) => <option key={em.model_id} value={em.model_id}>{em.display_name} ({em.dimensions}d)</option>)}
                  </select>
                }

                if (slot.key === 'helper_llm' && isOfficialProvider(curProv)) {
                  const llmModels = getModelsForSlot(curProv, 'helper_llm')
                  return <>
                    <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                      className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                      <option value="default">Default (recommended)</option>
                      {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                    </select>
                    {cfg?.model && cfg.model !== 'default' && (
                      <p className="text-[10px] text-[var(--color-warning)] mt-0.5">All auxiliary tasks will use this model. May affect speed/cost.</p>
                    )}
                  </>
                }

                const llmModels = getModelsForSlot(curProv, slot.key)
                if (llmModels.length > 0) {
                  return <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                    <option value="">Model...</option>
                    {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                  </select>
                }

                return <input type="text" value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                  placeholder="Model name" className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none" />
              })()}
            </div>
          </div>
        ) : (
          <p className="text-xs text-[var(--color-error)]">No {slot.protocol} provider. Add one above.</p>
        )}
      </div>
    )
  }

  // ---- Full view ----
  return (
    <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-[var(--text-primary)]">LLM Providers</span>
        {allSlotsReady && !hasPendingChanges && (
          <button onClick={() => setCollapsed(true)} className="text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">Collapse</button>
        )}
      </div>
      <div className="text-[11px] text-[var(--text-tertiary)] space-y-0.5">
        <p>Need at least: <span className="text-[var(--text-secondary)]">one OpenAI-compatible provider</span> (Embedding &amp; Helper LLM) + <span className="text-[var(--text-secondary)]">one Anthropic-compatible provider or Claude Code Login</span> (Agent).</p>
        <p>Embedding is required — currently only <span className="text-[var(--text-secondary)]">OpenAI official API</span> and <span className="text-[var(--text-secondary)]">NetMind.AI Power</span> are supported. More providers coming soon.</p>
        <p>A <span className="text-[var(--text-secondary)]">NetMind.AI Power</span> key meets the minimum in one step, though model selection is limited.</p>
      </div>

      {/* ---- NetMind.AI Power Card ---- */}
      <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[13px] font-medium text-[var(--text-primary)]">NetMind.AI Power</span>
          {hasNetMind && <span className="text-[var(--color-success)] text-xs ml-auto">{'\u2713'} Added</span>}
        </div>
        <p className="text-[11px] text-[var(--text-tertiary)] mb-2">A single API key covers both Anthropic and OpenAI protocol endpoints.</p>
        <div className="flex gap-1.5">
          <input type="password" value={netmindKey} onChange={(e) => setNetmindKey(e.target.value)}
            placeholder={hasNetMind ? 'New key to re-configure...' : 'NetMind API Key'}
            className="flex-1 px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          <button onClick={handleAddNetMind} disabled={netmindAdding}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/20 disabled:opacity-40">
            {netmindAdding ? '...' : hasNetMind ? 'Update' : 'Add'}
          </button>
        </div>
      </div>

      {/* ---- Claude Code Card ---- */}
      <div className="p-3 rounded-lg border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[13px] font-medium text-[var(--text-primary)]">Claude Code Login</span>
          {hasClaude && <span className="text-[var(--color-success)] text-xs ml-auto">{'\u2713'} Added</span>}
        </div>
        <p className="text-[11px] text-[var(--text-tertiary)] mb-1.5">OAuth login via Claude Code CLI. No API key needed.</p>
        {!hasClaude && claudeStatus && (
          <div className="space-y-1.5">
            <div className="flex items-center gap-1.5">
              <span className={cn('inline-block w-2 h-2 rounded-full',
                claudeStatus.logged_in ? 'bg-[var(--color-success)]' :
                claudeStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
              )} />
              <span className="text-xs text-[var(--text-secondary)]">
                {claudeStatus.logged_in ? 'Logged in' : claudeStatus.cli_installed ? 'Not logged in' : 'CLI not installed'}
              </span>
            </div>
            {claudeStatus.logged_in && (
              <button onClick={handleAddClaudeOAuth}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--accent-primary)]/20 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/30">
                Add as Provider
              </button>
            )}
            {!claudeStatus.logged_in && (
              <p className="text-[11px] text-[var(--text-tertiary)]">
                {claudeStatus.cli_installed
                  ? 'Run "claude login" in your terminal first, then refresh this page.'
                  : 'Install Claude Code CLI first, then run "claude login" in your terminal.'}
              </p>
            )}
          </div>
        )}
      </div>

      {/* ---- Add Protocol Buttons ---- */}
      <div className="flex gap-1.5">
        <button onClick={() => openForm('anthropic')}
          className="flex-1 py-2 text-xs rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]">
          + Anthropic Protocol
        </button>
        <button onClick={() => openForm('openai')}
          className="flex-1 py-2 text-xs rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]">
          + OpenAI Protocol
        </button>
      </div>

      {/* ---- Protocol Form ---- */}
      {showForm && (
        <div className="p-3 rounded-lg border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-medium text-[var(--text-primary)]">
              {showForm === 'anthropic' ? 'Anthropic' : 'OpenAI'} Protocol
            </span>
            <button onClick={() => setShowForm(null)} className="text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">Cancel</button>
          </div>
          <p className="text-[11px] text-[var(--text-tertiary)]">
            {showForm === 'anthropic' ? 'Anthropic API or compatible endpoint.' : 'OpenAI API or compatible endpoint.'}
          </p>
          <div className="grid grid-cols-2 gap-2">
            <input type="text" value={formName} onChange={(e) => setFormName(e.target.value)}
              placeholder="Provider name"
              className="px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
            {showForm === 'anthropic' ? (
              <select value={formAuth} onChange={(e) => setFormAuth(e.target.value as 'api_key' | 'bearer_token')}
                className="px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                <option value="api_key">API Key</option>
                <option value="bearer_token">Bearer Token</option>
              </select>
            ) : (
              <div />
            )}
          </div>
          <input type="text" value={formUrl} onChange={(e) => setFormUrl(e.target.value)}
            placeholder="Base URL"
            className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          <input type="password" value={formKey} onChange={(e) => setFormKey(e.target.value)}
            placeholder="API Key"
            className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          <div>
            <label className="block text-[11px] text-[var(--text-tertiary)] mb-1">Available Models</label>
            <ModelBubbleInput models={formModels} onChange={setFormModels} />
          </div>
          <button onClick={handleAddProtocol} disabled={formAdding || !formKey.trim()}
            className="w-full py-1.5 text-xs font-medium rounded-lg bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 hover:bg-[var(--accent-primary)]/20 disabled:opacity-40">
            {formAdding ? 'Adding...' : 'Add Provider'}
          </button>
        </div>
      )}

      {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}

      {/* ---- Configured Providers ---- */}
      {hasProviders && (
        <div className="space-y-1.5">
          <span className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Providers</span>
          {providerList.map((prov) => (
            <div key={prov.provider_id} className="flex items-center justify-between p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-primary)]">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium text-[var(--text-primary)] truncate">{prov.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase">{prov.protocol}</span>
                </div>
                <span className="text-[11px] text-[var(--text-tertiary)]">{prov.api_key_masked} · {prov.models.length} model(s)</span>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                  className="px-2 py-1 text-xs text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 rounded-md disabled:opacity-40">
                  {testing === prov.provider_id ? '...' : 'Test'}
                </button>
                <button onClick={() => handleDelete(prov.provider_id)}
                  className="px-2 py-1 text-xs text-[var(--color-error)] hover:bg-[var(--color-error)]/5 rounded-md">Del</button>
                {testResults[prov.provider_id] && (
                  <span className={cn('text-[11px]', testResults[prov.provider_id].ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                    {testResults[prov.provider_id].msg}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ---- All Slot Rows (shown simultaneously, no tabs) ---- */}
      {hasProviders && (
        <div className="space-y-2 pt-2.5 border-t border-[var(--border-subtle)]">
          <span className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Model Assignment</span>
          {SLOT_DEFS.map((slot) => renderSlotRow(slot))}

          {/* ---- Apply / Discard buttons ---- */}
          {hasPendingChanges && (
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleApply}
                disabled={applying}
                className={cn(
                  'flex-1 py-2 text-xs font-medium rounded-lg transition-colors',
                  'bg-[var(--accent-primary)] text-[var(--bg-primary)]',
                  'hover:bg-[var(--accent-primary)]/90',
                  'disabled:opacity-40'
                )}
              >
                {applying ? 'Applying...' : 'Apply Changes'}
              </button>
              <button
                onClick={handleDiscard}
                disabled={applying}
                className="px-4 py-2 text-xs rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
              >
                Discard
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
