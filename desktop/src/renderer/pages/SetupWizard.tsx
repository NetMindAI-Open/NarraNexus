/**
 * @file SetupWizard.tsx
 * @description Four-phase setup wizard: Preflight → Install → Launch → Config
 *
 * Phase flow:
 *   preflight → [allReady] → launch (if no EverMemOS install needed)
 *              → [missing]  → install → [done] → launch
 *   launch → [success] → config → [finish] → done
 *
 * Config is the last step — user fills API Keys and EverMemOS config after
 * the core environment is already running.
 */

import React, { useEffect, useState, useCallback } from 'react'
import AsciiBanner from '../components/AsciiBanner'
import PreflightView from '../components/setup/PreflightView'
import GuidedInstallView from '../components/setup/GuidedInstallView'
import ServiceLaunchView from '../components/setup/ServiceLaunchView'
import ProviderConfigView from '../components/setup/ProviderConfigView'

interface SetupWizardProps {
  onComplete: () => void
}

type SetupPhase = 'preflight' | 'install' | 'launch' | 'config'

/** External link mapping */
const KEY_LINKS: Record<string, { label: string; url: string }> = {
  OPENAI_API_KEY: { label: 'Get Key', url: 'https://platform.openai.com/api-keys' },
  GOOGLE_API_KEY: { label: 'Get Key', url: 'https://aistudio.google.com/apikey' },
  NETMIND_API_KEY: { label: 'Get Key', url: 'https://www.netmind.ai' },
  ANTHROPIC_API_KEY: { label: 'Get Key', url: 'https://console.anthropic.com/settings/keys' },
  LLM_API_KEY: { label: 'Get Key', url: 'https://openrouter.ai/keys' }
}

/** NetMind one-click configuration presets */
const NETMIND_PRESETS: Record<string, string> = {
  LLM_MODEL: 'deepseek-ai/DeepSeek-V3.2',
  LLM_BASE_URL: 'https://api.netmind.ai/inference-api/openai/v1',
  VECTORIZE_PROVIDER: 'deepinfra',
  VECTORIZE_MODEL: 'BAAI/bge-m3',
  VECTORIZE_BASE_URL: 'https://api.netmind.ai/inference-api/openai/v1',
  RERANK_PROVIDER: 'none',
  RERANK_API_KEY: 'EMPTY',
  RERANK_BASE_URL: '',
  RERANK_MODEL: ''
}

const EM_GROUP_LABELS: Record<string, string> = {
  llm: 'LLM',
  vectorize: 'Embedding',
  rerank: 'Rerank',
  infrastructure: 'Infrastructure (usually no changes needed)',
  other: 'Other'
}

/** Phase labels for the step indicator */
const PHASE_LABELS: { phase: SetupPhase; label: string }[] = [
  { phase: 'preflight', label: 'Check' },
  { phase: 'install', label: 'Install' },
  { phase: 'launch', label: 'Launch' },
  { phase: 'config', label: 'Configure' }
]

const SetupWizard: React.FC<SetupWizardProps> = ({ onComplete }) => {
  const [phase, setPhase] = useState<SetupPhase>('preflight')

  // EverMemOS install toggle (set during preflight)
  const [installEverMemOS, setInstallEverMemOS] = useState(true)

  // Config state (loaded early, used in config phase)
  const [fields, setFields] = useState<EnvField[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [emFields, setEmFields] = useState<EverMemOSEnvField[]>([])
  const [emValues, setEmValues] = useState<Record<string, string>>({})
  const [emAdvancedOpen, setEmAdvancedOpen] = useState(false)
  const [emMode, setEmMode] = useState<'skip' | 'netmind' | 'custom'>('netmind')
  const [everMemOSInstalled, setEverMemOSInstalled] = useState(false)

  // Claude Code authentication state
  const [claudeAuth, setClaudeAuth] = useState<ClaudeAuthInfo | null>(null)
  const [loginStatus, setLoginStatus] = useState<LoginProcessStatus>({ state: 'idle' })
  const [setupToken, setSetupToken] = useState('')
  const [tokenResult, setTokenResult] = useState<{ valid: boolean; message: string } | null>(null)

  // Preflight state
  const [preflightResult, setPreflightResult] = useState<PreflightResult | null>(null)
  const [checking, setChecking] = useState(false)

  // Install state
  const [missingIds, setMissingIds] = useState<string[]>([])

  // Provider config ready (all 3 slots configured)
  const [providerReady, setProviderReady] = useState(false)

  // Config finishing state
  const [finishing, setFinishing] = useState(false)

  // Load .env, EverMemOS config, and Claude auth on mount (needed later for config phase)
  useEffect(() => {
    window.nexus.getEnv().then(({ config, fields: f }) => {
      setFields(f)
      setValues(config)
    })
    window.nexus.getEverMemOSEnv().then(({ config, fields: f }) => {
      setEmFields(f)
      setEmValues(config)
    })
    window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
  }, [])

  // Listen for Claude login status
  useEffect(() => {
    const unsub = window.nexus.onClaudeLoginStatus((status: LoginProcessStatus) => {
      setLoginStatus(status)
      if (status.state === 'success') {
        window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
      }
    })
    return unsub
  }, [])

  // Auto-start preflight on mount
  useEffect(() => {
    runPreflight()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Compute derived state
  const PLACEHOLDER_VALUES = ['sk-or-v1-xxxx', 'xxxxx']

  const emConfigured = emMode === 'skip'
    ? false
    : emMode === 'netmind'
      ? !!values['NETMIND_API_KEY']?.trim()
      : emFields.filter((f) => f.required).every((f) => {
          const v = emValues[f.key]?.trim()
          return v && !PLACEHOLDER_VALUES.includes(v)
        })

  // Docker gets ~50% of system RAM (same formula as Colima allocation, capped 2-12GB)
  const dockerMemoryGb = preflightResult
    ? Math.max(2, Math.min(12, Math.floor(preflightResult.systemInfo.totalMemoryGb / 2)))
    : 0
  const lowMemory = preflightResult ? dockerMemoryGb < 6 : false

  const buildFinalEmValues = (): Record<string, string> => {
    if (emMode === 'netmind') {
      return {
        ...emValues,
        ...NETMIND_PRESETS,
        LLM_API_KEY: values['NETMIND_API_KEY'] || '',
        VECTORIZE_API_KEY: values['NETMIND_API_KEY'] || ''
      }
    }
    return emValues
  }

  // Phase transitions
  const runPreflight = useCallback(async () => {
    setChecking(true)
    try {
      const result = await window.nexus.runPreflight() as PreflightResult
      setPreflightResult(result)
      // Auto-disable EverMemOS toggle if low memory
      const mem = Math.max(2, Math.min(12, Math.floor(result.systemInfo.totalMemoryGb / 2)))
      if (mem < 6) setInstallEverMemOS(false)
    } catch (err) {
      console.error('Preflight failed:', err)
    }
    setChecking(false)
  }, [])

  const handleProceedToInstall = (ids: string[]) => {
    const installerIds: string[] = []
    if (ids.includes('docker')) installerIds.push('docker')
    if (ids.includes('uv')) installerIds.push('uv')
    if (ids.includes('node')) installerIds.push('node')
    if (ids.includes('claude')) installerIds.push('claude')
    if (ids.includes('uv') || ids.includes('python')) installerIds.push('python-deps')
    installerIds.push('python-deps', 'frontend-build')
    if (installEverMemOS) {
      installerIds.push('evermemos-clone', 'evermemos-deps')
    }
    const unique = [...new Set(installerIds)]
    setMissingIds(unique)
    setPhase('install')
  }

  const handleProceedToLaunch = () => {
    // If allReady but user wants EverMemOS, go through install for EverMemOS components
    if (installEverMemOS) {
      const installerIds = ['python-deps', 'frontend-build', 'evermemos-clone', 'evermemos-deps']
      setMissingIds(installerIds)
      setPhase('install')
    } else {
      setPhase('launch')
    }
  }

  const handleInstallComplete = () => {
    setPhase('launch')
  }

  const handleLaunchComplete = async () => {
    // Check if EverMemOS was installed (for showing config section)
    const installed = await window.nexus.isEverMemOSInstalled()
    setEverMemOSInstalled(installed)
    // Refresh Claude auth (CLI might have been installed during install phase)
    window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
    setPhase('config')
  }

  const handleConfigComplete = async () => {
    setFinishing(true)
    try {
      // Save .env
      await window.nexus.setEnv(values)
      // Save EverMemOS env
      await window.nexus.setEverMemOSEnv(buildFinalEmValues())
      // If EverMemOS is installed AND configured, launch it now
      if (everMemOSInstalled && emConfigured && !lowMemory) {
        await window.nexus.launchEverMemOS()
      }
      // Mark setup as complete
      await window.nexus.setSetupComplete()
      onComplete()
    } catch (err) {
      console.error('Config complete failed:', err)
    }
    setFinishing(false)
  }

  // Field groups
  const apiFields = fields.filter(
    (f) => f.key.includes('API_KEY') || f.key.includes('SECRET') || f.key.includes('BASE_URL')
  )
  const dbFields = fields.filter((f) => f.key.startsWith('DB_'))

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Title bar drag area */}
      <div className="h-8 titlebar-drag shrink-0" />

      {/* Banner */}
      <div className="px-8 pt-2 pb-4">
        <AsciiBanner />
      </div>

      {/* Phase indicator */}
      <div className="px-8 pb-4">
        <div className="flex gap-1">
          {PHASE_LABELS.map(({ phase: p, label }, i) => {
            const isActive = p === phase
            const isPast = PHASE_LABELS.findIndex((pl) => pl.phase === phase) > i
            return (
              <div key={p} className="flex items-center gap-1 flex-1">
                <div className={`h-1 flex-1 rounded-full transition-colors ${
                  isActive ? 'bg-blue-500' : isPast ? 'bg-green-400' : 'bg-gray-200'
                }`} />
                <span className={`text-[10px] shrink-0 ${
                  isActive ? 'text-blue-600 font-medium' : isPast ? 'text-green-600' : 'text-gray-400'
                }`}>
                  {label}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto px-8 pb-6">
        {/* ─── Phase: Preflight ─────────────────────────── */}
        {phase === 'preflight' && (
          <>
            {preflightResult ? (
              <PreflightView
                result={preflightResult}
                onProceedToInstall={handleProceedToInstall}
                onProceedToLaunch={handleProceedToLaunch}
                onRecheck={runPreflight}
                checking={checking}
                installEverMemOS={installEverMemOS}
                onToggleEverMemOS={setInstallEverMemOS}
                lowMemory={lowMemory}
                dockerMemoryGb={dockerMemoryGb}
              />
            ) : (
              <div className="flex flex-col items-center gap-3 py-12">
                <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-gray-500">Checking environment...</p>
              </div>
            )}
          </>
        )}

        {/* ─── Phase: Install ─────────────────────────── */}
        {phase === 'install' && (
          <GuidedInstallView
            missingIds={missingIds}
            onComplete={handleInstallComplete}
            onBack={() => setPhase('preflight')}
          />
        )}

        {/* ─── Phase: Launch ─────────────────────────── */}
        {phase === 'launch' && (
          <ServiceLaunchView
            skipEverMemOS={true}
            onComplete={handleLaunchComplete}
            onRetry={() => {}}
          />
        )}

        {/* ─── Phase: Config ─────────────────────────── */}
        {phase === 'config' && (
          <>
            {/* LLM Provider Configuration */}
            <ProviderConfigView
              onReady={() => setProviderReady(true)}
              claudeAuth={claudeAuth}
              loginStatus={loginStatus}
              onStartClaudeLogin={() => {
                setLoginStatus({ state: 'running', message: 'Opening browser...' })
                window.nexus.startClaudeLogin()
              }}
              onCancelClaudeLogin={() => window.nexus.cancelClaudeLogin()}
              onSendClaudeLoginInput={(input) => window.nexus.sendClaudeLoginInput(input)}
            />

            {/* Divider */}
            <div className="my-5 border-t border-gray-200" />

            {/* Database configuration */}
            <div>
              <p className="text-xs text-gray-400 mb-3">
                Database (defaults usually work fine)
              </p>
              <div className="grid grid-cols-2 gap-3">
                {dbFields.map((field) => (
                  <div key={field.key}>
                    <label className="block text-xs font-medium text-gray-500 mb-1">
                      {field.label}
                    </label>
                    <input
                      type={field.key === 'DB_PASSWORD' ? 'password' : 'text'}
                      value={values[field.key] || ''}
                      onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                      placeholder={field.placeholder}
                      className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* EverMemOS configuration — only show if installed */}
            {everMemOSInstalled && (
              <>
                <div className="my-5 border-t border-gray-200" />
                <div>
                  <h2 className="text-sm font-semibold text-gray-700 mb-3">
                    EverMemOS Memory System
                  </h2>

                  {lowMemory && (
                    <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
                      <p className="text-xs text-red-700 font-medium">
                        Docker memory too low: {dockerMemoryGb}GB allocated (system {preflightResult?.systemInfo.totalMemoryGb ?? '?'}GB), EverMemOS requires at least 6GB.
                        EverMemOS will be disabled. The core Agent will still work without memory features.
                      </p>
                    </div>
                  )}

                  <div className="flex gap-4 mb-4">
                    <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="emMode"
                        checked={emMode === 'skip'}
                        onChange={() => setEmMode('skip')}
                        className="accent-blue-500"
                      />
                      <span className="text-sm font-medium text-gray-700">Skip</span>
                    </label>
                    <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="emMode"
                        checked={emMode === 'netmind'}
                        onChange={() => setEmMode('netmind')}
                        className="accent-blue-500"
                      />
                      <span className="text-sm font-medium text-gray-700">NetMind.AI Power</span>
                    </label>
                    <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="emMode"
                        checked={emMode === 'custom'}
                        onChange={() => setEmMode('custom')}
                        className="accent-blue-500"
                      />
                      <span className="text-sm font-medium text-gray-700">Custom Configuration</span>
                    </label>
                  </div>

                  {emMode === 'skip' && (
                    <div className="mb-4 p-3 bg-gray-50 border border-gray-200 rounded-lg">
                      <p className="text-xs text-gray-600">
                        EverMemOS will not be started. The core Agent will still work without long-term memory features.
                        You can configure this later in settings.
                      </p>
                    </div>
                  )}

                  {emMode === 'netmind' && (
                    <>
                      {!emConfigured ? (
                        <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                          <p className="text-xs text-amber-700">
                            NetMind API Key is not configured above. Fill it in to enable EverMemOS memory features.
                          </p>
                        </div>
                      ) : (
                        <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg">
                          <p className="text-xs text-green-700">
                            Will use the NetMind API Key above to power: LLM (DeepSeek-V3.2), Embedding (bge-m3). Rerank disabled. No extra configuration needed.
                          </p>
                        </div>
                      )}
                    </>
                  )}

                  {emMode === 'custom' && (
                    <>
                      {(['llm', 'vectorize', 'rerank', 'other'] as const).map((group) => {
                        const groupFields = emFields
                          .filter((f) => f.group === group)
                          .sort((a, b) => a.order - b.order)
                        if (groupFields.length === 0) return null

                        return (
                          <div key={group} className="mb-4">
                            <p className="text-xs font-medium text-gray-500 mb-2">
                              {EM_GROUP_LABELS[group]}
                            </p>
                            <div className="space-y-2">
                              {groupFields.map((field) => (
                                <div key={field.key}>
                                  <label className="block text-xs font-medium text-gray-600 mb-1">
                                    {field.label}
                                    {field.required && <span className="text-red-500 ml-0.5">*</span>}
                                  </label>
                                  <div className="flex gap-2">
                                    {field.inputType === 'select' ? (
                                      <select
                                        value={emValues[field.key] || ''}
                                        onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                        className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all bg-white"
                                      >
                                        <option value="">{field.placeholder}</option>
                                        {field.options?.map((opt) => (
                                          <option key={opt} value={opt}>{opt}</option>
                                        ))}
                                      </select>
                                    ) : (
                                      <input
                                        type={field.inputType}
                                        value={emValues[field.key] || ''}
                                        onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                        placeholder={field.placeholder}
                                        className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                                      />
                                    )}
                                    {KEY_LINKS[field.key] && (
                                      <button
                                        onClick={() => window.nexus.openExternal(KEY_LINKS[field.key].url)}
                                        className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap transition-colors"
                                      >
                                        {KEY_LINKS[field.key].label}
                                      </button>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      })}

                      {(() => {
                        const infraFields = emFields
                          .filter((f) => f.group === 'infrastructure')
                          .sort((a, b) => a.order - b.order)
                        if (infraFields.length === 0) return null

                        return (
                          <div className="mb-4">
                            <button
                              onClick={() => setEmAdvancedOpen((v) => !v)}
                              className="titlebar-no-drag flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors mb-2"
                            >
                              <span className={`inline-block transition-transform ${emAdvancedOpen ? 'rotate-90' : ''}`}>
                                &#9654;
                              </span>
                              {EM_GROUP_LABELS.infrastructure}
                            </button>
                            {emAdvancedOpen && (
                              <div className="grid grid-cols-2 gap-2">
                                {infraFields.map((field) => (
                                  <div key={field.key}>
                                    <label className="block text-xs font-medium text-gray-500 mb-1">
                                      {field.label}
                                    </label>
                                    <input
                                      type={field.inputType}
                                      value={emValues[field.key] || ''}
                                      onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                      placeholder={field.placeholder}
                                      className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                                    />
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )
                      })()}
                    </>
                  )}
                </div>
              </>
            )}

            {/* Google Gemini API Key (for RAG) */}
            <div className="my-5 border-t border-gray-200" />
            <div>
              <h2 className="text-sm font-semibold text-gray-700 mb-1">
                Gemini RAG Knowledge Base
              </h2>
              <p className="text-[11px] text-gray-400 mb-3">
                Optional. Enables the RAG (Retrieval-Augmented Generation) module powered by
                Gemini File Search. Without this key, the RAG knowledge base feature is unavailable
                but all other Agent capabilities work normally.
              </p>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={values['GOOGLE_API_KEY'] || ''}
                  onChange={(e) => setValues((v) => ({ ...v, GOOGLE_API_KEY: e.target.value }))}
                  placeholder="Google Gemini API Key"
                  className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                />
                <button
                  onClick={() => window.nexus.openExternal('https://aistudio.google.com/apikey')}
                  className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap border border-blue-200"
                >
                  Get Key
                </button>
              </div>
            </div>

            {/* Finish button */}
            <div className="mt-6">
              <button
                onClick={handleConfigComplete}
                disabled={finishing || !providerReady}
                className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {finishing ? 'Finishing...' : !providerReady ? 'Configure LLM Providers First' : 'Finish Setup'}
              </button>
              {everMemOSInstalled && emConfigured && !lowMemory && (
                <p className="text-xs text-green-600 mt-1.5 text-center">
                  EverMemOS will be started automatically on finish
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default SetupWizard
