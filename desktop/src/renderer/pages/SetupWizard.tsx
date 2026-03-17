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
  const [emMode, setEmMode] = useState<'netmind' | 'custom'>('netmind')
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

  const emConfigured = emMode === 'netmind'
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
            {/* API Keys */}
            <div className="space-y-3">
              {apiFields.map((field) => (
                <div key={field.key}>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {field.label}
                    {field.required && <span className="text-red-500 ml-0.5">*</span>}
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={values[field.key] || ''}
                      onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                      placeholder={field.placeholder}
                      className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                    {KEY_LINKS[field.key] && (
                      <button
                        onClick={() => window.nexus.openExternal(KEY_LINKS[field.key].url)}
                        className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap transition-colors"
                      >
                        {KEY_LINKS[field.key].label}
                      </button>
                    )}
                  </div>
                  {(field.key === 'ANTHROPIC_API_KEY' || field.key === 'ANTHROPIC_BASE_URL') && (
                    <p className="text-xs text-gray-400 mt-1">
                      If you have already logged in via Claude Code CLI locally, you can leave this empty.
                    </p>
                  )}
                </div>
              ))}
            </div>

            {/* Claude Code authentication panel */}
            <div className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
              <p className="text-sm font-medium text-gray-700 mb-3">Claude Code Authentication</p>

              {claudeAuth?.cliInstalled && (
                <div className="flex flex-wrap gap-x-6 gap-y-1 mb-3 text-xs">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
                    CLI: {claudeAuth.cliVersion || 'installed'}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                      claudeAuth.authStatus.state === 'logged_in' ? 'bg-green-500' :
                      claudeAuth.authStatus.state === 'expired' ? 'bg-yellow-500' :
                      'bg-gray-400'
                    }`} />
                    {claudeAuth.authStatus.state === 'logged_in' && (
                      <>Logged in{claudeAuth.authStatus.expiresAt
                        ? ` (expires: ${new Date(claudeAuth.authStatus.expiresAt).toLocaleDateString()})`
                        : ''
                      }</>
                    )}
                    {claudeAuth.authStatus.state === 'expired' && 'Login expired'}
                    {claudeAuth.authStatus.state === 'not_logged_in' && 'Not logged in'}
                  </span>
                  {claudeAuth.hasApiKey && (
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
                      API Key configured
                    </span>
                  )}
                </div>
              )}

              {/* OAuth login — only show when CLI is installed */}
              {claudeAuth?.cliInstalled && (
                <div className="mb-3">
                  <p className="text-xs text-gray-500 mb-2">
                    Option 1 (Recommended): Click below to open browser and authorize with your Anthropic account.
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        setLoginStatus({ state: 'running', message: 'Opening browser...' })
                        window.nexus.startClaudeLogin()
                      }}
                      disabled={loginStatus.state === 'running'}
                      className="titlebar-no-drag px-4 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      {loginStatus.state === 'running' ? 'Waiting for browser...' :
                       claudeAuth?.authStatus.state === 'logged_in' ? 'Re-login' :
                       'Login with Claude Code'}
                    </button>
                    {loginStatus.state === 'running' && (
                      <>
                        <div className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                        <button
                          onClick={() => window.nexus.cancelClaudeLogin()}
                          className="titlebar-no-drag text-xs text-gray-500 hover:text-gray-700 underline"
                        >
                          Cancel
                        </button>
                      </>
                    )}
                    {loginStatus.state === 'success' && (
                      <span className="text-xs text-green-600 font-medium">Login successful!</span>
                    )}
                    {(loginStatus.state === 'failed' || loginStatus.state === 'timeout') && (
                      <span className="text-xs text-red-500">{loginStatus.message}</span>
                    )}
                  </div>
                  {loginStatus.state === 'running' && (
                    <div className="mt-2">
                      {loginStatus.message && (
                        <p className="text-xs text-gray-500 mb-1.5">{loginStatus.message}</p>
                      )}
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          placeholder="Paste the auth code from browser here"
                          className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-400"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              const value = (e.target as HTMLInputElement).value.trim()
                              if (value) {
                                window.nexus.sendClaudeLoginInput(value)
                                ;(e.target as HTMLInputElement).value = ''
                              }
                            }
                          }}
                        />
                        <span className="text-[10px] text-gray-400 shrink-0">Press Enter to submit</span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {!claudeAuth?.cliInstalled && (
                <p className="text-xs text-gray-400 mb-3">
                  OAuth login will be available after Claude Code is installed during setup. You can use Option 1/2 below for now.
                </p>
              )}

              {/* Paste Setup Token */}
              <div className="mb-2">
                <p className="text-xs text-gray-500 mb-1.5">
                  Option {claudeAuth?.cliInstalled ? '2' : '1'}: Run <code className="px-1 py-0.5 bg-gray-200 rounded text-[11px]">claude setup-token</code> on
                  another machine and paste the token below.
                </p>
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={setupToken}
                    onChange={(e) => { setSetupToken(e.target.value); setTokenResult(null) }}
                    placeholder="sk-ant-oat01-..."
                    className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all"
                  />
                  <button
                    onClick={async () => {
                      const result = await window.nexus.saveSetupToken(setupToken)
                      setTokenResult(result)
                      if (result.valid) {
                        window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
                        setValues((v) => ({ ...v, ANTHROPIC_API_KEY: setupToken.trim() }))
                        setSetupToken('')
                      }
                    }}
                    disabled={!setupToken.trim()}
                    className="titlebar-no-drag px-3 py-1.5 text-xs font-medium text-indigo-600 bg-indigo-50 rounded-lg hover:bg-indigo-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    Save
                  </button>
                </div>
                {tokenResult && (
                  <p className={`text-xs mt-1 ${tokenResult.valid ? 'text-green-600' : 'text-red-500'}`}>
                    {tokenResult.valid ? 'Token saved!' : tokenResult.message}
                  </p>
                )}
              </div>

              <p className="text-xs text-gray-400">
                Option {claudeAuth?.cliInstalled ? '3' : '2'}: Fill in the Anthropic API Key field above directly.
              </p>
            </div>

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

            {/* Claude Code notice */}
            <div className="mt-4 p-3 rounded-lg bg-amber-50 border border-amber-200">
              <p className="text-xs text-amber-700">
                <strong>Note:</strong> Claude Code (if installed) uses its own global API key config.
                Run <code className="px-1 py-0.5 bg-amber-100 rounded text-[11px]">claude config</code> in terminal to set it separately.
              </p>
            </div>

            {/* Finish button */}
            <div className="mt-6">
              <button
                onClick={handleConfigComplete}
                disabled={finishing}
                className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {finishing ? 'Finishing...' : 'Finish Setup'}
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
