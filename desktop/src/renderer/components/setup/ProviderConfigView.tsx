/**
 * @file ProviderConfigView.tsx
 * @description 2-step LLM provider configuration for the Setup Wizard
 *
 * Step 1 — Choose an approach:
 *   A) Quick Setup: pick a preset provider (data-driven list), enter one API key
 *   B) Anthropic + OpenAI: CC login or Anthropic API key, plus OpenAI API key
 *   C) Configure Later: skip — configure in the web "Advanced Settings" page
 *
 * Step 2 — Fill in the relevant fields based on the chosen approach
 *
 * After configuration: show a summary card with slot status and validation.
 *
 * Database and Gemini RAG sections are deliberately excluded — those belong
 * in the web advanced settings or .env for power users.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { PRESET_PROVIDERS, type PresetProvider } from '../../../shared/provider-presets'

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
  base_url?: string
}

interface SlotStatus { provider_id: string; model: string }
interface SlotData { config: SlotStatus | null; required_protocols: string[] }

interface ProviderConfigViewProps {
  onReady: () => void
  claudeAuth: ClaudeAuthInfo | null
  loginStatus: LoginProcessStatus
  onStartClaudeLogin: () => void
  onCancelClaudeLogin: () => void
  onSendClaudeLoginInput: (input: string) => void
}

// =============================================================================
// Setup approach type
// =============================================================================

type SetupApproach = 'quick' | 'anthropic_openai' | 'later'

// =============================================================================
// Slot definitions (for the summary view)
// =============================================================================

const SLOT_DEFS = [
  { key: 'agent', label: 'Agent', desc: 'Powers the main AI dialogue and decision-making' },
  { key: 'embedding', label: 'Embedding', desc: 'Converts text to vectors for memory search' },
  { key: 'helper_llm', label: 'Helper LLM', desc: 'Handles auxiliary analysis tasks' },
]

// =============================================================================
// Helper: fetch wrapper
// =============================================================================

/**
 * Fetch with automatic retry — handles the case where the backend is
 * still starting up when the user reaches the Config phase.
 */
async function apiFetch<T = any>(path: string, init?: RequestInit, retries = 3): Promise<T> {
  const url = `${API}${path}`
  for (let i = 0; i <= retries; i++) {
    try {
      console.log(`[apiFetch] ${init?.method || 'GET'} ${url} (attempt ${i + 1}/${retries + 1})`)
      const res = await fetch(url, init)
      const text = await res.text()
      console.log(`[apiFetch] ${res.status} ${res.statusText} | ${text.slice(0, 300)}`)

      if (!res.ok) {
        // Server returned an error — try to parse JSON detail, otherwise show raw text
        let detail: string
        try {
          const json = JSON.parse(text)
          detail = json.detail || json.error || JSON.stringify(json)
        } catch {
          detail = text.slice(0, 200)
        }
        throw new Error(`Server error ${res.status}: ${detail}`)
      }

      return JSON.parse(text) as T
    } catch (err) {
      console.error(`[apiFetch] Attempt ${i + 1} failed:`, err instanceof Error ? err.message : err)
      // Don't retry on 4xx/5xx server errors (only retry on network failures)
      if (err instanceof Error && err.message.startsWith('Server error')) {
        throw err
      }
      if (i < retries) {
        await new Promise((r) => setTimeout(r, (i + 1) * 1000))
        continue
      }
      throw err
    }
  }
  throw new Error('Unreachable')
}

async function apiPost<T = any>(path: string, body: unknown): Promise<T> {
  return apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// =============================================================================
// Sub-component: Approach Selection Card
// =============================================================================

const ApproachCard: React.FC<{
  selected: boolean
  onClick: () => void
  title: string
  subtitle: string
  badge?: string
  children?: React.ReactNode
}> = ({ selected, onClick, title, subtitle, badge, children }) => (
  <button
    onClick={onClick}
    className={`titlebar-no-drag w-full text-left p-4 rounded-xl border-2 transition-all ${
      selected
        ? 'border-blue-500 bg-blue-50/50 shadow-sm'
        : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50/50'
    }`}
  >
    <div className="flex items-center gap-2 mb-1">
      <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${
        selected ? 'border-blue-500' : 'border-gray-300'
      }`}>
        {selected && <div className="w-2 h-2 rounded-full bg-blue-500" />}
      </div>
      <span className="text-sm font-semibold text-gray-800">{title}</span>
      {badge && (
        <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">
          {badge}
        </span>
      )}
    </div>
    <p className="text-xs text-gray-500 ml-6">{subtitle}</p>
    {children}
  </button>
)

// =============================================================================
// Sub-component: Slot Summary Row
// =============================================================================

const SlotSummaryRow: React.FC<{
  label: string
  desc: string
  configured: boolean
  providerName?: string
  model?: string
}> = ({ label, desc, configured, providerName, model }) => (
  <div className={`flex items-center justify-between p-3 rounded-lg border ${
    configured ? 'border-green-200 bg-green-50/40' : 'border-gray-200 bg-gray-50/40'
  }`}>
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${configured ? 'bg-green-500' : 'bg-gray-300'}`} />
        <span className="text-sm font-medium text-gray-700">{label}</span>
      </div>
      <p className="text-[11px] text-gray-400 ml-4">{desc}</p>
    </div>
    <div className="text-right shrink-0 ml-3">
      {configured ? (
        <div>
          <span className="text-xs text-gray-600">{providerName}</span>
          <br />
          <span className="text-[10px] text-gray-400 font-mono">{model}</span>
        </div>
      ) : (
        <span className="text-xs text-gray-400">Not configured</span>
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
  // ---- Step state ----
  const [step, setStep] = useState<1 | 2 | 'summary'>(1)
  const [approach, setApproach] = useState<SetupApproach>('quick')

  // ---- Quick Setup state ----
  const [selectedPreset, setSelectedPreset] = useState<string>(PRESET_PROVIDERS[0]?.id || '')
  const [presetKey, setPresetKey] = useState('')
  const [presetAdding, setPresetAdding] = useState(false)

  // ---- Anthropic + OpenAI state ----
  const [anthropicMode, setAnthropicMode] = useState<'cc_login' | 'api_key'>('api_key')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [anthropicBaseUrl, setAnthropicBaseUrl] = useState('https://api.anthropic.com')
  const [openaiKey, setOpenaiKey] = useState('')
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('https://api.openai.com/v1')
  const [dualAdding, setDualAdding] = useState(false)

  // ---- Backend state ----
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [error, setError] = useState('')
  const [configComplete, setConfigComplete] = useState(false)
  const [backendReady, setBackendReady] = useState(false)

  // ---- Wait for backend to be ready before allowing any interaction ----
  useEffect(() => {
    let cancelled = false
    const waitForBackend = async () => {
      console.log(`[waitForBackend] Starting to poll ${API}/api/providers ...`)
      for (let i = 0; i < 30; i++) {
        if (cancelled) return
        try {
          const res = await fetch(`${API}/api/providers`)
          console.log(`[waitForBackend] Attempt ${i + 1}: status=${res.status}`)
          if (res.ok) {
            console.log('[waitForBackend] Backend is ready!')
            setBackendReady(true)
            return
          }
        } catch (err) {
          console.log(`[waitForBackend] Attempt ${i + 1}: ${err instanceof Error ? err.message : err}`)
        }
        await new Promise((r) => setTimeout(r, 1000))
      }
      console.warn('[waitForBackend] Gave up after 30s, proceeding anyway')
      setBackendReady(true)
    }
    waitForBackend()
    return () => { cancelled = true }
  }, [])

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const res = await apiFetch<any>('/api/providers')
      if (res.success) {
        setProviders(res.data.providers)
        setSlots(res.data.slots)
      }
    } catch {}
  }, [])

  // Only load config after backend is confirmed ready
  useEffect(() => {
    if (backendReady) refreshConfig()
  }, [backendReady, refreshConfig])

  // ---- Derived state ----
  const allSlotsReady = SLOT_DEFS.every(
    (s) => slots[s.key]?.config?.provider_id && slots[s.key]?.config?.model
  )

  useEffect(() => {
    if (allSlotsReady && !configComplete) {
      setConfigComplete(true)
      onReady()
    }
  }, [allSlotsReady, configComplete, onReady])

  const currentPreset = PRESET_PROVIDERS.find((p) => p.id === selectedPreset)

  // ---- Actions ----

  const handleQuickSetup = async () => {
    if (!currentPreset) return
    if (!presetKey.trim()) { setError('Please enter your API key'); return }
    setPresetAdding(true)
    setError('')
    try {
      // Send provider + default_slots together — backend assigns slots atomically
      const res = await apiPost<any>('/api/providers', {
        card_type: currentPreset.id,
        api_key: presetKey.trim(),
        default_slots: currentPreset.default_slots,
      })
      if (!res.success) {
        setError(res.detail || `Failed to add ${currentPreset.name}`)
        setPresetAdding(false)
        return
      }
      console.log('[handleQuickSetup] Success, provider_ids:', res.provider_ids)
      await refreshConfig()
      setStep('summary')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[handleQuickSetup] CATCH:', msg, err)
      setError(`Network error: ${msg}`)
    }
    setPresetAdding(false)
  }

  const handleDualSetup = async () => {
    setError('')

    // Validate all inputs upfront — avoid partial setup if one key is missing
    if (anthropicMode === 'api_key' && !anthropicKey.trim()) {
      setError('Please enter your Anthropic API key')
      return
    }
    if (!openaiKey.trim()) {
      setError('Please enter your OpenAI API key')
      return
    }

    setDualAdding(true)
    try {
      // Step A: Add Anthropic provider (CC login or API key)
      if (anthropicMode === 'cc_login') {
        const res = await apiPost<any>('/api/providers', { card_type: 'claude_oauth' })
        if (!res.success) {
          setError(res.detail || 'Failed to add Claude OAuth provider')
          setDualAdding(false)
          return
        }
      } else {
        const res = await apiPost<any>('/api/providers', {
          card_type: 'anthropic',
          api_key: anthropicKey.trim(),
          base_url: anthropicBaseUrl.trim() || 'https://api.anthropic.com',
        })
        if (!res.success) {
          setError(res.detail || 'Failed to add Anthropic provider')
          setDualAdding(false)
          return
        }
      }

      // Step B: Add OpenAI provider
      const res2 = await apiPost<any>('/api/providers', {
        card_type: 'openai',
        api_key: openaiKey.trim(),
        base_url: openaiBaseUrl.trim() || 'https://api.openai.com/v1',
      })
      if (!res2.success) {
        setError(res2.detail || 'Failed to add OpenAI provider')
        setDualAdding(false)
        return
      }

      await refreshConfig()
      setStep('summary')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[handleDualSetup] CATCH:', msg, err)
      setError(`Network error: ${msg}`)
    }
    setDualAdding(false)
  }

  const handleSkipForNow = () => {
    setStep('summary')
  }

  // ---- Render ----

  // ---- Show loading until backend is ready ----
  if (!backendReady) {
    return (
      <div className="flex flex-col items-center gap-3 py-12">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500">Waiting for backend to be ready...</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* ================================================================= */}
      {/* Step 1: Choose your approach                                       */}
      {/* ================================================================= */}
      {step === 1 && (
        <>
          <div>
            <h2 className="text-base font-semibold text-gray-800 mb-1">
              How would you like to configure your AI models?
            </h2>
            <p className="text-xs text-gray-500">
              NarraNexus needs LLM providers for three functions: Agent (dialogue), Embedding (memory search), and Helper LLM (analysis).
              Choose the setup that works best for you.
            </p>
          </div>

          <div className="space-y-3">
            {/* Option A: Quick Setup */}
            <ApproachCard
              selected={approach === 'quick'}
              onClick={() => setApproach('quick')}
              title="Quick Setup"
              subtitle="One API key covers everything. The fastest way to get started."
              badge="Recommended"
            />

            {/* Option B: Anthropic + OpenAI */}
            <ApproachCard
              selected={approach === 'anthropic_openai'}
              onClick={() => setApproach('anthropic_openai')}
              title="Anthropic + OpenAI"
              subtitle="Use Anthropic (or Claude Code login) for the Agent, and OpenAI for Embedding & Helper LLM."
            />

            {/* Option C: Configure Later */}
            <ApproachCard
              selected={approach === 'later'}
              onClick={() => setApproach('later')}
              title="Configure Later"
              subtitle="Skip for now and set up providers in the web UI's Advanced Settings page."
            />
          </div>

          <button
            onClick={() => {
              setError('')
              if (approach === 'later') {
                handleSkipForNow()
              } else {
                setStep(2)
              }
            }}
            className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 transition-colors"
          >
            {approach === 'later' ? 'Skip & Continue' : 'Next'}
          </button>
        </>
      )}

      {/* ================================================================= */}
      {/* Step 2: Fill in the details                                        */}
      {/* ================================================================= */}
      {step === 2 && approach === 'quick' && (
        <>
          <div>
            <button
              onClick={() => { setStep(1); setError('') }}
              className="titlebar-no-drag text-xs text-gray-400 hover:text-gray-600 mb-2 flex items-center gap-1"
            >
              &#8592; Back
            </button>
            <h2 className="text-base font-semibold text-gray-800 mb-1">Quick Setup</h2>
            <p className="text-xs text-gray-500">
              Select a provider and enter your API key. All three model slots will be configured automatically.
            </p>
          </div>

          {/* Provider selector */}
          {PRESET_PROVIDERS.length > 1 ? (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">Provider</label>
              <select
                value={selectedPreset}
                onChange={(e) => setSelectedPreset(e.target.value)}
                className="titlebar-no-drag w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:ring-2 focus:ring-blue-400 focus:border-blue-400 outline-none"
              >
                {PRESET_PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} — {p.tagline}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            /* Single preset — show as a card instead of a dropdown */
            currentPreset && (
              <div className="p-3 rounded-lg border border-blue-200 bg-blue-50/30">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium text-gray-700">{currentPreset.name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                    {currentPreset.tagline}
                  </span>
                </div>
                <p className="text-[11px] text-gray-500">{currentPreset.description}</p>
              </div>
            )
          )}

          {/* API Key input */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">
              {currentPreset?.name || 'Provider'} API Key
            </label>
            <div className="flex gap-2">
              <input
                type="password"
                value={presetKey}
                onChange={(e) => setPresetKey(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleQuickSetup() }}
                placeholder="Paste your API key here"
                className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-400 focus:border-blue-400 outline-none"
              />
              {currentPreset && (
                <button
                  onClick={() => window.nexus.openExternal(currentPreset.get_key_url)}
                  className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap border border-blue-200"
                >
                  Get Key
                </button>
              )}
            </div>
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}

          <button
            onClick={handleQuickSetup}
            disabled={presetAdding || !presetKey.trim()}
            className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {presetAdding ? 'Configuring...' : 'Configure & Continue'}
          </button>
        </>
      )}

      {step === 2 && approach === 'anthropic_openai' && (
        <>
          <div>
            <button
              onClick={() => { setStep(1); setError('') }}
              className="titlebar-no-drag text-xs text-gray-400 hover:text-gray-600 mb-2 flex items-center gap-1"
            >
              &#8592; Back
            </button>
            <h2 className="text-base font-semibold text-gray-800 mb-1">Anthropic + OpenAI</h2>
            <p className="text-xs text-gray-500">
              Set up an Anthropic-compatible provider for the Agent, and OpenAI for Embedding & Helper LLM.
            </p>
          </div>

          {/* ── Anthropic Section ── */}
          <div className="p-4 rounded-xl border border-gray-200 bg-gray-50/30 space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-700 mb-0.5">Anthropic (Agent)</h3>
              <p className="text-[11px] text-gray-400">
                Powers the main AI agent that talks to users and makes decisions.
              </p>
            </div>

            {/* Mode toggle */}
            <div className="flex gap-3">
              <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="anthropicMode"
                  checked={anthropicMode === 'cc_login'}
                  onChange={() => setAnthropicMode('cc_login')}
                  className="accent-blue-500"
                />
                <span className="text-xs font-medium text-gray-600">Claude Code Login</span>
              </label>
              <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="anthropicMode"
                  checked={anthropicMode === 'api_key'}
                  onChange={() => setAnthropicMode('api_key')}
                  className="accent-blue-500"
                />
                <span className="text-xs font-medium text-gray-600">API Key</span>
              </label>
            </div>

            {anthropicMode === 'cc_login' ? (
              <div className="space-y-2">
                {/* Claude Code login status */}
                {claudeAuth?.cliInstalled && (
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className={`w-2 h-2 rounded-full ${
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
                  <p className="text-[11px] text-amber-600">
                    Claude Code CLI not detected. Install it first or switch to API Key mode.
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
                          placeholder="Paste auth code here (if needed)"
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
                {claudeAuth?.authStatus.state === 'logged_in' && (
                  <p className="text-xs text-green-600">
                    &#10003; Claude Code authenticated — ready to use as Agent provider.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <div>
                  <label className="block text-[10px] text-gray-500 mb-0.5">Base URL</label>
                  <input
                    type="text"
                    value={anthropicBaseUrl}
                    onChange={(e) => setAnthropicBaseUrl(e.target.value)}
                    placeholder="https://api.anthropic.com"
                    className="titlebar-no-drag w-full px-3 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-500 mb-0.5">API Key</label>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={anthropicKey}
                      onChange={(e) => setAnthropicKey(e.target.value)}
                      placeholder="sk-ant-..."
                      className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
                    />
                    <button
                      onClick={() => window.nexus.openExternal('https://console.anthropic.com/settings/keys')}
                      className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap border border-blue-200"
                    >
                      Get Key
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── OpenAI Section ── */}
          <div className="p-4 rounded-xl border border-gray-200 bg-gray-50/30 space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-700 mb-0.5">OpenAI (Embedding & Helper LLM)</h3>
              <p className="text-[11px] text-gray-400">
                Powers text embedding (memory search) and auxiliary AI tasks like summarization.
              </p>
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 mb-0.5">Base URL</label>
              <input
                type="text"
                value={openaiBaseUrl}
                onChange={(e) => setOpenaiBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1"
                className="titlebar-no-drag w-full px-3 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] text-gray-500 mb-0.5">API Key</label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={openaiKey}
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  placeholder="sk-..."
                  className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none"
                />
                <button
                  onClick={() => window.nexus.openExternal('https://platform.openai.com/api-keys')}
                  className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap border border-blue-200"
                >
                  Get Key
                </button>
              </div>
            </div>
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}

          <button
            onClick={handleDualSetup}
            disabled={dualAdding}
            className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {dualAdding ? 'Configuring...' : 'Configure & Continue'}
          </button>
        </>
      )}

      {/* ================================================================= */}
      {/* Summary: show slot status                                          */}
      {/* ================================================================= */}
      {step === 'summary' && (
        <>
          <div>
            <h2 className="text-base font-semibold text-gray-800 mb-1">Configuration Summary</h2>
            <p className="text-xs text-gray-500">
              {allSlotsReady
                ? 'All model slots are configured. You\'re ready to go!'
                : approach === 'later'
                  ? 'You can configure providers later in the web UI\'s Advanced Settings.'
                  : 'Review the current status below. You can adjust providers in the web UI after setup.'}
            </p>
          </div>

          <div className="space-y-2">
            {SLOT_DEFS.map((slot) => {
              const cfg = slots[slot.key]?.config
              const isConfigured = !!(cfg?.provider_id && cfg?.model)
              const prov = cfg?.provider_id ? providers[cfg.provider_id] : null

              return (
                <SlotSummaryRow
                  key={slot.key}
                  label={slot.label}
                  desc={slot.desc}
                  configured={isConfigured}
                  providerName={prov?.name}
                  model={cfg?.model}
                />
              )
            })}
          </div>

          {allSlotsReady && approach === 'quick' && (
            <div className="p-3 rounded-lg bg-green-50 border border-green-200">
              <p className="text-xs text-green-700">
                Default models have been assigned automatically. If you&apos;d like to switch models,
                you can do so in the web UI&apos;s Settings page after setup, or use the &quot;Configure LLM&quot;
                button on the dashboard.
              </p>
            </div>
          )}

          {!allSlotsReady && approach !== 'later' && (
            <div className="p-3 rounded-lg bg-amber-50 border border-amber-200">
              <p className="text-xs text-amber-700">
                Some slots are not yet assigned. You can configure them in the web UI&apos;s
                Settings page after setup.
              </p>
            </div>
          )}

          {approach === 'later' && (
            <div className="p-3 rounded-lg bg-blue-50 border border-blue-200">
              <p className="text-xs text-blue-700">
                No providers configured yet. After finishing setup, open the web UI and go to
                Settings to add your LLM providers.
              </p>
            </div>
          )}

          {/* Allow going back to reconfigure if not all slots ready */}
          {!allSlotsReady && approach !== 'later' && (
            <button
              onClick={() => { setStep(1); setError('') }}
              className="titlebar-no-drag w-full py-2 text-xs text-gray-500 hover:text-gray-700 transition-colors"
            >
              &#8592; Reconfigure
            </button>
          )}
        </>
      )}
    </div>
  )
}

export default ProviderConfigView
