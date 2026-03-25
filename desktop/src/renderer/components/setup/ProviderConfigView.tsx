/**
 * @file ProviderConfigView.tsx
 * @description LLM Provider configuration for the Setup Wizard config phase
 *
 * Two-layer architecture:
 *   Layer 1 — Provider Atomic Cards: 4 card types to add providers
 *     - NetMind (unique): one API key → 2 providers
 *     - Claude Code Login (unique): OAuth → 1 provider
 *     - Anthropic Protocol (multiple): base_url + api_key + model bubbles
 *     - OpenAI Protocol (multiple): base_url + api_key + model bubbles
 *
 *   Layer 2 — Slot Model Selection: 3 tabs [Agent] [Embedding] [Helper LLM]
 *     - Only shown after ≥1 provider exists
 *     - Green/red tab indicators, all green → "Continue" enabled
 */

import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://localhost:8000'

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
  linked_group?: string
  base_url?: string
}

interface SlotStatus {
  provider_id: string
  model: string
}

interface SlotData {
  config: SlotStatus | null
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

interface ProviderConfigViewProps {
  onReady: () => void
  claudeAuth: ClaudeAuthInfo | null
  loginStatus: LoginProcessStatus
  onStartClaudeLogin: () => void
  onCancelClaudeLogin: () => void
  onSendClaudeLoginInput: (input: string) => void
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
  // Future frameworks:
  // { id: 'openai_agents', label: 'OpenAI Agents SDK', protocol: 'openai', desc: 'OpenAI Agents SDK' },
]

// =============================================================================
// Slot metadata
// =============================================================================

const SLOT_DEFS: { key: string; label: string; desc: string; protocol: string }[] = [
  { key: 'agent', label: 'Agent', desc: 'Main dialogue', protocol: 'anthropic' },
  { key: 'embedding', label: 'Embedding', desc: 'Vector search (OpenAI protocol)', protocol: 'openai' },
  { key: 'helper_llm', label: 'Helper LLM', desc: 'Auxiliary tasks (OpenAI protocol)', protocol: 'openai' },
]

// =============================================================================
// Model Bubble Tag Input
// =============================================================================

const ModelBubbleInput: React.FC<{
  models: string[]
  onChange: (models: string[]) => void
  placeholder?: string
}> = ({ models, onChange, placeholder = 'model name' }) => {
  const [input, setInput] = useState('')

  const addModel = () => {
    const v = input.trim()
    if (v && !models.includes(v)) {
      onChange([...models, v])
    }
    setInput('')
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {models.map((m) => (
        <span key={m} className="inline-flex items-center gap-0.5 px-2 py-0.5 text-[11px] bg-blue-50 text-blue-700 rounded-full border border-blue-200 whitespace-nowrap">
          {m}
          <button
            onClick={() => onChange(models.filter((x) => x !== m))}
            className="titlebar-no-drag text-blue-400 hover:text-blue-600"
          >
            &times;
          </button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addModel() } }}
          placeholder={placeholder}
          style={{ width: Math.max(80, (input.length + 1) * 7) }}
          className="titlebar-no-drag px-1.5 py-0.5 text-[11px] border border-gray-200 rounded-full focus:ring-1 focus:ring-blue-400 outline-none"
        />
        <button
          onClick={addModel}
          disabled={!input.trim()}
          className="titlebar-no-drag px-1.5 py-0.5 text-[11px] text-blue-600 bg-blue-50 rounded-full hover:bg-blue-100 disabled:opacity-30 border border-blue-200 whitespace-nowrap"
        >
          +
        </button>
      </span>
    </div>
  )
}

// =============================================================================
// Provider Card (configured provider display)
// =============================================================================

const ProviderCard: React.FC<{
  prov: ProviderSummary
  testing: boolean
  testResult?: { ok: boolean; msg: string }
  onTest: () => void
  onDelete: () => void
}> = ({ prov, testing, testResult, onTest, onDelete }) => (
  <div className="flex items-center justify-between p-2.5 rounded-lg border border-gray-200 bg-white">
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-gray-700 truncate">{prov.name}</span>
        <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 uppercase tracking-wide">
          {prov.protocol}
        </span>
        {prov.source === 'netmind' && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">NetMind.AI</span>
        )}
        {prov.source === 'claude_oauth' && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600">OAuth</span>
        )}
      </div>
      <div className="flex items-center gap-3 mt-0.5">
        <span className="text-[10px] text-gray-400">{prov.api_key_masked}</span>
        {prov.models.length > 0 && (
          <span className="text-[10px] text-gray-400">{prov.models.length} model(s)</span>
        )}
      </div>
    </div>
    <div className="flex items-center gap-1.5 shrink-0">
      <button
        onClick={onTest}
        disabled={testing}
        className="titlebar-no-drag px-2 py-1 text-[10px] text-blue-600 hover:bg-blue-50 rounded-md disabled:opacity-40"
      >
        {testing ? 'Testing...' : 'Test'}
      </button>
      <button
        onClick={onDelete}
        className="titlebar-no-drag px-2 py-1 text-[10px] text-red-500 hover:bg-red-50 rounded-md"
      >
        Delete
      </button>
      {testResult && (
        <span className={`text-[10px] ${testResult.ok ? 'text-green-600' : 'text-red-500'}`}>
          {testResult.msg}
        </span>
      )}
    </div>
  </div>
)

// =============================================================================
// Main Component
// =============================================================================

const ProviderConfigView: React.FC<ProviderConfigViewProps> = ({
  onReady,
  claudeAuth,
  loginStatus,
  onStartClaudeLogin,
  onCancelClaudeLogin,
  onSendClaudeLoginInput,
}) => {
  // Provider state from backend
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [knownModels, setKnownModels] = useState<Record<string, KnownModelMeta>>({})
  const [embeddingModels, setEmbeddingModels] = useState<EmbeddingModelInfo[]>([])
  const [officialBaseUrls, setOfficialBaseUrls] = useState<Record<string, string[]>>({})
  const [error, setError] = useState('')
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})

  // NetMind card state
  const [netmindKey, setNetmindKey] = useState('')
  const [netmindAdding, setNetmindAdding] = useState(false)

  // Protocol card form state (for adding new anthropic/openai providers)
  const [showProtocolForm, setShowProtocolForm] = useState<'anthropic' | 'openai' | null>(null)
  const [protoName, setProtoName] = useState('')
  const [protoBaseUrl, setProtoBaseUrl] = useState('')
  const [protoKey, setProtoKey] = useState('')
  const [protoAuthType, setProtoAuthType] = useState<'api_key' | 'bearer_token'>('api_key')
  const [protoModels, setProtoModels] = useState<string[]>([])
  const [protoAdding, setProtoAdding] = useState(false)

  // Slot tab state
  const [activeSlotTab, setActiveSlotTab] = useState<string>('agent')
  const [agentFramework, setAgentFramework] = useState<string>(AGENT_FRAMEWORKS[0].id)

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/providers`).then((r) => r.json())
      if (res.success) {
        setProviders(res.data.providers)
        setSlots(res.data.slots)
      }
    } catch {}
  }, [])

  useEffect(() => {
    refreshConfig()
    // Load catalog
    fetch(`${API}/api/providers/catalog`)
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          setKnownModels(data.known_models)
          if (data.embedding_models) setEmbeddingModels(data.embedding_models)
          if (data.official_base_urls) setOfficialBaseUrls(data.official_base_urls)
        }
      })
      .catch(() => {})
  }, [refreshConfig])

  // ---- Derived state ----
  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  const hasNetMind = providerList.some((p) => p.source === 'netmind')
  const hasClaude = providerList.some((p) => p.source === 'claude_oauth')

  const allSlotsReady = SLOT_DEFS.every(
    (s) => slots[s.key]?.config?.provider_id && slots[s.key]?.config?.model
  )

  // Notify parent when all slots are ready
  useEffect(() => {
    if (allSlotsReady) onReady()
  }, [allSlotsReady, onReady])

  // ---- Provider actions ----
  const handleAddNetMind = async () => {
    if (!netmindKey.trim()) { setError('Please enter your NetMind API Key'); return }
    setNetmindAdding(true); setError('')
    try {
      const res = await fetch(`${API}/api/providers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_type: 'netmind', api_key: netmindKey.trim() }),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || 'Failed to add NetMind'); setNetmindAdding(false); return }
      setNetmindKey('')
      await refreshConfig()
    } catch { setError('Network error') }
    setNetmindAdding(false)
  }

  const handleAddClaudeOAuth = async () => {
    setError('')
    try {
      const res = await fetch(`${API}/api/providers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_type: 'claude_oauth' }),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || 'Failed to add Claude OAuth'); return }
      await refreshConfig()
    } catch { setError('Network error') }
  }

  const handleAddProtocolProvider = async () => {
    if (!showProtocolForm) return
    if (!protoKey.trim()) { setError('Please enter an API key'); return }
    setProtoAdding(true); setError('')
    try {
      const res = await fetch(`${API}/api/providers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          card_type: showProtocolForm,
          name: protoName.trim() || undefined,
          api_key: protoKey.trim(),
          base_url: protoBaseUrl.trim(),
          auth_type: protoAuthType,
          models: protoModels,
        }),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || 'Failed to add provider'); setProtoAdding(false); return }
      // Reset form
      setShowProtocolForm(null)
      setProtoName(''); setProtoBaseUrl(''); setProtoKey(''); setProtoAuthType('api_key'); setProtoModels([])
      await refreshConfig()
    } catch { setError('Network error') }
    setProtoAdding(false)
  }

  const handleDeleteProvider = async (id: string) => {
    try {
      await fetch(`${API}/api/providers/${id}`, { method: 'DELETE' })
      await refreshConfig()
    } catch {}
  }

  const handleTestProvider = async (id: string) => {
    setTesting(id)
    try {
      const res = await fetch(`${API}/api/providers/${id}/test`, { method: 'POST' }).then((r) => r.json())
      setTestResults((prev) => ({ ...prev, [id]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResults((prev) => ({ ...prev, [id]: { ok: false, msg: 'Network error' } }))
    }
    setTesting(null)
  }

  // ---- Slot actions ----
  const handleSlotChange = async (slotName: string, providerId: string, model: string) => {
    await fetch(`${API}/api/providers/slots/${slotName}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, model }),
    })
    await refreshConfig()
  }

  // Get providers matching a slot's required protocol
  const getProvidersForSlot = (protocol: string) =>
    providerList.filter((p) => p.protocol === protocol && p.is_active)

  // Check if a provider uses an official base_url
  const isOfficialProvider = (prov: ProviderSummary) => {
    const urls = officialBaseUrls[prov.protocol] || []
    return urls.includes(prov.base_url || '')
  }

  // Get filtered models for a slot + provider
  const getModelsForSlot = (prov: ProviderSummary, slotKey: string) => {
    if (slotKey === 'embedding') {
      // Embedding: show ALL known embedding models for this provider's source.
      // NetMind has its own embedding models, OpenAI has its own.
      // Users cannot add custom embedding models.
      if (prov.source === 'netmind') {
        return embeddingModels.filter((em) => prov.models.includes(em.model_id))
      }
      // For official OpenAI or user providers: show OpenAI embedding models
      return embeddingModels.filter((em) =>
        em.model_id.startsWith('text-embedding-')
      )
    }
    // Agent / Helper LLM: show non-embedding models only
    return prov.models
      .filter((mid) => !knownModels[mid]?.dimensions)
      .map((mid) => ({ model_id: mid, display_name: knownModels[mid]?.display_name || mid }))
  }

  // ---- Open protocol form with defaults ----
  const openProtocolForm = (protocol: 'anthropic' | 'openai') => {
    setShowProtocolForm(protocol)
    setProtoName('')
    setProtoBaseUrl(protocol === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
    setProtoKey('')
    setProtoAuthType('api_key')
    setProtoModels([])
    setError('')
  }

  return (
    <div className="space-y-5">
      <h2 className="text-sm font-semibold text-gray-700">LLM Provider Configuration</h2>

      {/* ================================================================= */}
      {/* Layer 1: Provider Atomic Cards                                     */}
      {/* ================================================================= */}
      <div className="space-y-3">
        <div className="text-xs text-gray-500 space-y-1">
          <p>
            You need at least: <strong>one OpenAI-compatible provider</strong> (for Embedding &amp; Helper LLM),
            and <strong>one Anthropic-compatible provider or Claude Code Login</strong> (for Agent).
          </p>
          <p className="text-gray-400">
            Embedding is required and currently only supports <span className="text-gray-500">OpenAI official API</span> and
            {' '}<span className="text-gray-500">NetMind.AI Power</span>. More embedding providers will be supported in the future.
          </p>
          <p className="text-gray-400">
            With a <span className="text-gray-500">NetMind.AI Power</span> key you can meet the minimum
            requirements in one step, though the available model selection is limited.
          </p>
        </div>

        {/* ---- NetMind.AI Power Card (unique) ---- */}
        <div className="p-3 rounded-lg border border-gray-200 bg-gray-50/30">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-gray-700">NetMind.AI Power</span>
            {hasNetMind && <span className="text-green-500 text-[11px] ml-auto">&#10003; Added</span>}
          </div>
          <p className="text-[11px] text-gray-500 mb-2">
            A single API key covers both Anthropic and OpenAI protocol endpoints.
            No extra configuration needed — just paste the key.
          </p>
          <div className="flex gap-2">
            <input
              type="password"
              value={netmindKey}
              onChange={(e) => setNetmindKey(e.target.value)}
              placeholder={hasNetMind ? 'Enter new key to re-configure...' : 'Your NetMind API Key'}
              className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-400 focus:border-blue-400 outline-none"
            />
            <button
              onClick={() => window.nexus.openExternal('https://www.netmind.ai/user/dashboard')}
              className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap border border-blue-200"
            >
              Get Key
            </button>
            <button
              onClick={handleAddNetMind}
              disabled={netmindAdding}
              className="titlebar-no-drag px-4 py-1.5 text-xs font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 whitespace-nowrap"
            >
              {netmindAdding ? 'Adding...' : hasNetMind ? 'Update' : 'Add'}
            </button>
          </div>
        </div>

        {/* ---- Claude Code Login Card (unique) ---- */}
        <div className="p-3 rounded-lg border border-indigo-200 bg-indigo-50/30">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-gray-700">Claude Code Login</span>
            {hasClaude && <span className="text-green-500 text-[11px] ml-auto">&#10003; Added</span>}
          </div>
          <p className="text-[11px] text-gray-500 mb-2">
            Use Claude Code CLI's OAuth login. No API key needed — authenticates through your browser.
            Provides access to Claude models for the Agent slot.
          </p>
          {!hasClaude && (
            <div>
              {/* Auth status */}
              {claudeAuth?.cliInstalled && (
                <div className="flex items-center gap-2 mb-2 text-[11px]">
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                    claudeAuth.authStatus.state === 'logged_in' ? 'bg-green-500' :
                    claudeAuth.authStatus.state === 'expired' ? 'bg-yellow-500' : 'bg-gray-400'
                  }`} />
                  <span className="text-gray-600">
                    {claudeAuth.authStatus.state === 'logged_in' ? 'Logged in' :
                     claudeAuth.authStatus.state === 'expired' ? 'Session expired' : 'Not logged in'}
                  </span>
                </div>
              )}
              {!claudeAuth?.cliInstalled && (
                <p className="text-[11px] text-amber-600 mb-2">
                  Claude Code CLI not detected. Install it first or use another provider.
                </p>
              )}
              {/* Login flow */}
              {claudeAuth?.cliInstalled && claudeAuth.authStatus.state !== 'logged_in' && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={onStartClaudeLogin}
                      disabled={loginStatus.state === 'running'}
                      className="titlebar-no-drag px-3 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600 disabled:opacity-40"
                    >
                      {loginStatus.state === 'running' ? 'Waiting...' : 'Login'}
                    </button>
                    {loginStatus.state === 'running' && (
                      <>
                        <div className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                        <button onClick={onCancelClaudeLogin} className="titlebar-no-drag text-xs text-gray-500 hover:text-gray-700 underline">
                          Cancel
                        </button>
                      </>
                    )}
                    {loginStatus.state === 'success' && <span className="text-xs text-green-600">Login successful!</span>}
                    {(loginStatus.state === 'failed' || loginStatus.state === 'timeout') && (
                      <span className="text-xs text-red-500">{loginStatus.message}</span>
                    )}
                  </div>
                  {loginStatus.state === 'running' && (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        placeholder="Paste auth code here"
                        className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-400"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            const v = (e.target as HTMLInputElement).value.trim()
                            if (v) { onSendClaudeLoginInput(v); (e.target as HTMLInputElement).value = '' }
                          }
                        }}
                      />
                      <span className="text-[10px] text-gray-400 shrink-0">Enter to submit</span>
                    </div>
                  )}
                </div>
              )}
              {/* Add button (after login success) */}
              {claudeAuth?.authStatus.state === 'logged_in' && (
                <button
                  onClick={handleAddClaudeOAuth}
                  className="titlebar-no-drag px-4 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600"
                >
                  Add as Provider
                </button>
              )}
            </div>
          )}
        </div>

        {/* ---- Add Protocol Provider Buttons ---- */}
        <div className="flex gap-2">
          <button
            onClick={() => openProtocolForm('anthropic')}
            className="titlebar-no-drag flex-1 py-2 text-xs font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            + Anthropic Protocol
          </button>
          <button
            onClick={() => openProtocolForm('openai')}
            className="titlebar-no-drag flex-1 py-2 text-xs font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            + OpenAI Protocol
          </button>
        </div>

        {/* ---- Protocol Provider Form (expandable) ---- */}
        {showProtocolForm && (
          <div className="p-3 rounded-lg border border-gray-300 bg-gray-50/50 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">
                {showProtocolForm === 'anthropic' ? 'Anthropic' : 'OpenAI'} Protocol Provider
              </span>
              <button
                onClick={() => setShowProtocolForm(null)}
                className="titlebar-no-drag text-xs text-gray-400 hover:text-gray-600"
              >
                Cancel
              </button>
            </div>
            <p className="text-[11px] text-gray-500">
              {showProtocolForm === 'anthropic'
                ? 'For Anthropic API or any Anthropic-compatible endpoint (e.g., proxy services).'
                : 'For OpenAI API or any OpenAI-compatible endpoint (e.g., proxy services, local LLMs).'}
            </p>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Provider Name</label>
                <input
                  type="text"
                  value={protoName}
                  onChange={(e) => setProtoName(e.target.value)}
                  placeholder={showProtocolForm === 'anthropic' ? 'e.g., Anthropic' : 'e.g., OpenAI'}
                  className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
                />
              </div>
              {showProtocolForm === 'anthropic' && (
                <div>
                  <label className="block text-[10px] text-gray-500 mb-0.5">Auth Type</label>
                  <select
                    value={protoAuthType}
                    onChange={(e) => setProtoAuthType(e.target.value as 'api_key' | 'bearer_token')}
                    className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                  >
                    <option value="api_key">API Key (X-Api-Key)</option>
                    <option value="bearer_token">Bearer Token</option>
                  </select>
                </div>
              )}
            </div>

            <div>
              <label className="block text-[10px] text-gray-500 mb-0.5">Base URL</label>
              <input
                type="text"
                value={protoBaseUrl}
                onChange={(e) => setProtoBaseUrl(e.target.value)}
                placeholder={showProtocolForm === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'}
                className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
              />
            </div>

            <div>
              <label className="block text-[10px] text-gray-500 mb-0.5">API Key</label>
              <input
                type="password"
                value={protoKey}
                onChange={(e) => setProtoKey(e.target.value)}
                placeholder="Your API key"
                className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
              />
            </div>

            <div>
              <label className="block text-[10px] text-gray-500 mb-0.5">
                Available Models
                <span className="text-gray-400 ml-1">(add model names this provider supports)</span>
              </label>
              <ModelBubbleInput models={protoModels} onChange={setProtoModels} />
            </div>

            <button
              onClick={handleAddProtocolProvider}
              disabled={protoAdding || !protoKey.trim()}
              className="titlebar-no-drag w-full py-1.5 text-xs font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40"
            >
              {protoAdding ? 'Adding...' : 'Add Provider'}
            </button>
          </div>
        )}

        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>

      {/* ---- Configured Providers List ---- */}
      {hasProviders && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-gray-400 uppercase tracking-wider font-medium">Configured Providers</p>
          {providerList.map((prov) => (
            <ProviderCard
              key={prov.provider_id}
              prov={prov}
              testing={testing === prov.provider_id}
              testResult={testResults[prov.provider_id]}
              onTest={() => handleTestProvider(prov.provider_id)}
              onDelete={() => handleDeleteProvider(prov.provider_id)}
            />
          ))}
        </div>
      )}

      {/* ================================================================= */}
      {/* Layer 2: Slot Model Selection (only after providers exist)         */}
      {/* ================================================================= */}
      {hasProviders && (
        <>
          <div className="border-t border-gray-200 my-4" />

          <div className="space-y-3">
            <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
              Model Assignment
            </h3>
            <p className="text-[11px] text-gray-400">
              Assign a provider and model to each slot. All three must be configured to continue.
            </p>

            {/* ---- Tab Row ---- */}
            <div className="flex rounded-lg border border-gray-200 overflow-hidden">
              {SLOT_DEFS.map((slot) => {
                const cfg = slots[slot.key]?.config
                const isConfigured = !!(cfg?.provider_id && cfg?.model)
                const isActive = activeSlotTab === slot.key

                return (
                  <button
                    key={slot.key}
                    onClick={() => setActiveSlotTab(slot.key)}
                    className={`titlebar-no-drag flex-1 py-2 text-xs font-medium transition-colors relative ${
                      isActive
                        ? 'bg-white text-gray-800 shadow-sm'
                        : 'bg-gray-50 text-gray-500 hover:bg-gray-100'
                    }`}
                  >
                    <div className="flex items-center justify-center gap-1.5">
                      <span
                        className={`inline-block w-2 h-2 rounded-full ${
                          isConfigured ? 'bg-green-500' : 'bg-red-400'
                        }`}
                      />
                      {slot.label}
                    </div>
                  </button>
                )
              })}
            </div>

            {/* ---- Active Tab Content ---- */}
            {SLOT_DEFS.filter((s) => s.key === activeSlotTab).map((slot) => {
              // For agent slot, protocol is driven by the selected framework
              const selectedFramework = AGENT_FRAMEWORKS.find((f) => f.id === agentFramework)
              const effectiveProtocol = slot.key === 'agent' && selectedFramework
                ? selectedFramework.protocol
                : slot.protocol

              const slotData = slots[slot.key]
              const cfg = slotData?.config
              const isConfigured = !!(cfg?.provider_id && cfg?.model)
              const matchingProviders = getProvidersForSlot(effectiveProtocol)
              const currentProvider = cfg?.provider_id ? providers[cfg.provider_id] : null

              return (
                <div
                  key={slot.key}
                  className={`p-3 rounded-lg border ${
                    isConfigured
                      ? 'border-green-200 bg-green-50/30'
                      : 'border-red-200 bg-red-50/20'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="text-sm font-medium text-gray-700">{slot.label}</span>
                      <span className="text-[10px] text-gray-400 ml-2">{slot.desc}</span>
                      <span className="text-[10px] text-gray-400 ml-1">
                        ({effectiveProtocol} protocol)
                      </span>
                    </div>
                    {isConfigured ? (
                      <span className="text-green-500 text-sm">&#10003;</span>
                    ) : (
                      <span className="text-red-400 text-xs">Not configured</span>
                    )}
                  </div>

                  {/* Agent Framework selector (agent slot only) */}
                  {slot.key === 'agent' && (
                    <div className="mb-2">
                      <label className="block text-[10px] text-gray-500 mb-0.5">Agent Framework</label>
                      <select
                        value={agentFramework}
                        onChange={(e) => setAgentFramework(e.target.value)}
                        className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                      >
                        {AGENT_FRAMEWORKS.map((fw) => (
                          <option key={fw.id} value={fw.id}>{fw.label} — {fw.desc}</option>
                        ))}
                      </select>
                      {AGENT_FRAMEWORKS.length <= 1 && (
                        <p className="text-[10px] text-gray-400 mt-0.5">More frameworks coming soon.</p>
                      )}
                    </div>
                  )}

                  {matchingProviders.length > 0 ? (
                    <div className="grid grid-cols-2 gap-2">
                      {/* Provider dropdown */}
                      <div>
                        <label className="block text-[10px] text-gray-500 mb-0.5">Provider</label>
                        <select
                          value={cfg?.provider_id || ''}
                          onChange={(e) => {
                            const pid = e.target.value
                            const prov = providers[pid]
                            if (!prov) return
                            // Auto-select first appropriate model
                            const slotModels = getModelsForSlot(prov, slot.key)
                            if (slot.key === 'helper_llm' && isOfficialProvider(prov)) {
                              handleSlotChange(slot.key, pid, 'default')
                            } else if (slotModels.length > 0) {
                              handleSlotChange(slot.key, pid, slotModels[0].model_id)
                            } else {
                              handleSlotChange(slot.key, pid, '')
                            }
                          }}
                          className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                        >
                          <option value="">Select provider...</option>
                          {matchingProviders.map((p) => (
                            <option key={p.provider_id} value={p.provider_id}>
                              {p.name}
                            </option>
                          ))}
                        </select>
                      </div>

                      {/* Model selection — differs by slot type */}
                      <div>
                        <label className="block text-[10px] text-gray-500 mb-0.5">Model</label>
                        {(() => {
                          if (!currentProvider) {
                            return <select disabled className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-gray-50 outline-none">
                              <option>Select provider first...</option>
                            </select>
                          }

                          if (slot.key === 'embedding') {
                            // Embedding: only known embedding models (hardcoded)
                            const emModels = getModelsForSlot(currentProvider, 'embedding')
                            return (
                              <select
                                value={cfg?.model || ''}
                                onChange={(e) => {
                                  if (cfg?.provider_id) handleSlotChange(slot.key, cfg.provider_id, e.target.value)
                                }}
                                className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                              >
                                <option value="">Select embedding model...</option>
                                {emModels.map((em) => (
                                  <option key={em.model_id} value={em.model_id}>
                                    {em.display_name} ({em.dimensions}d)
                                  </option>
                                ))}
                              </select>
                            )
                          }

                          if (slot.key === 'helper_llm' && isOfficialProvider(currentProvider)) {
                            // Official OpenAI helper_llm: offer "Default" + specific models
                            const llmModels = getModelsForSlot(currentProvider, 'helper_llm')
                            return (
                              <>
                                <select
                                  value={cfg?.model || ''}
                                  onChange={(e) => {
                                    if (cfg?.provider_id) handleSlotChange(slot.key, cfg.provider_id, e.target.value)
                                  }}
                                  className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                                >
                                  <option value="default">Default (recommended)</option>
                                  {llmModels.map((m) => (
                                    <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                                  ))}
                                </select>
                                {cfg?.model && cfg.model !== 'default' && (
                                  <p className="text-[10px] text-amber-500 mt-0.5">
                                    All auxiliary LLM tasks will use this model. May affect speed and cost.
                                  </p>
                                )}
                              </>
                            )
                          }

                          // Agent / non-official helper_llm: show provider's LLM models
                          const llmModels = getModelsForSlot(currentProvider, slot.key)
                          if (llmModels.length > 0) {
                            return (
                              <select
                                value={cfg?.model || ''}
                                onChange={(e) => {
                                  if (cfg?.provider_id) handleSlotChange(slot.key, cfg.provider_id, e.target.value)
                                }}
                                className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none"
                              >
                                <option value="">Select model...</option>
                                {llmModels.map((m) => (
                                  <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                                ))}
                              </select>
                            )
                          }

                          // No preset models — manual input
                          return (
                            <input
                              type="text"
                              value={cfg?.model || ''}
                              onChange={(e) => {
                                if (cfg?.provider_id) handleSlotChange(slot.key, cfg.provider_id, e.target.value)
                              }}
                              placeholder="Enter model name"
                              className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
                            />
                          )
                        })()}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-red-400">
                      No {slot.protocol} protocol provider configured. Add one above.
                    </p>
                  )}
                </div>
              )
            })}
          </div>

          {/* Continue button */}
          <button
            onClick={onReady}
            disabled={!allSlotsReady}
            className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-green-500 rounded-lg hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {allSlotsReady ? 'Continue to Finish Setup' : 'Configure all 3 slots to continue'}
          </button>
        </>
      )}
    </div>
  )
}

export default ProviderConfigView
