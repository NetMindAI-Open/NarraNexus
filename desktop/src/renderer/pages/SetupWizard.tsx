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
  /** Start directly at a specific phase (e.g. 'config' for LLM reconfiguration) */
  initialPhase?: SetupPhase
}

type SetupPhase = 'preflight' | 'install' | 'launch' | 'config'

/** Phase labels for the step indicator */
const PHASE_LABELS: { phase: SetupPhase; label: string }[] = [
  { phase: 'preflight', label: 'Check' },
  { phase: 'install', label: 'Install' },
  { phase: 'launch', label: 'Launch' },
  { phase: 'config', label: 'Configure' }
]

const SetupWizard: React.FC<SetupWizardProps> = ({ onComplete, initialPhase = 'preflight' }) => {
  const [phase, setPhase] = useState<SetupPhase>(initialPhase)

  // EverMemOS: auto-install if memory sufficient, skip otherwise (no user toggle)
  const [installEverMemOS, setInstallEverMemOS] = useState(true)

  // Config state (loaded early, used in config phase)
  const [fields, setFields] = useState<EnvField[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
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
  // Load .env and Claude auth on mount
  useEffect(() => {
    window.nexus.getEnv().then(({ config, fields: f }) => {
      setFields(f)
      setValues(config)
    })
    // EverMemOS env is auto-synced by backend — no need to load here
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

  // Auto-start preflight on mount (skip if entering directly at config phase)
  useEffect(() => {
    if (initialPhase === 'preflight') {
      runPreflight()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Docker gets ~50% of system RAM (same formula as Colima allocation, capped 2-12GB)
  const dockerMemoryGb = preflightResult
    ? Math.max(2, Math.min(12, Math.floor(preflightResult.systemInfo.totalMemoryGb / 2)))
    : 0
  const lowMemory = preflightResult ? dockerMemoryGb < 6 : false

  // Phase transitions
  const runPreflight = useCallback(async () => {
    setChecking(true)
    try {
      const result = await window.nexus.runPreflight() as PreflightResult
      setPreflightResult(result)
      // Auto-decide EverMemOS install based on available memory
      const mem = Math.max(2, Math.min(12, Math.floor(result.systemInfo.totalMemoryGb / 2)))
      setInstallEverMemOS(mem >= 6)
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
      // Save .env (database defaults are baked in — no user input needed)
      await window.nexus.setEnv(values)
      // EverMemOS .env is auto-synced from slot config by the backend
      // (evermemos_sync.py). If EverMemOS is installed and memory is sufficient, launch it.
      if (everMemOSInstalled && !lowMemory) {
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

  // (Database and Gemini RAG fields removed from DMG setup — power users
  //  can configure these via .env or the web UI's Advanced Settings.)

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
            {/* LLM Provider Configuration (2-step wizard) */}
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

            {/* EverMemOS — auto-synced from LLM provider config */}
            {everMemOSInstalled && (
              <>
                <div className="my-5 border-t border-gray-200" />
                <div className="p-3 rounded-lg bg-blue-50 border border-blue-200">
                  <h2 className="text-sm font-semibold text-gray-700 mb-1">
                    EverMemOS Memory System
                  </h2>
                  {lowMemory ? (
                    <p className="text-xs text-red-700">
                      Docker memory too low ({dockerMemoryGb}GB). EverMemOS will be disabled.
                      The core Agent will still work without long-term memory features.
                    </p>
                  ) : (
                    <p className="text-xs text-gray-600">
                      EverMemOS will automatically use your configured Embedding and Helper LLM providers.
                      No extra configuration needed.
                    </p>
                  )}
                </div>
              </>
            )}

            {/* Finish button */}
            <div className="mt-6">
              <button
                onClick={handleConfigComplete}
                disabled={finishing}
                className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {finishing ? 'Finishing...' : 'Finish Setup'}
              </button>
              {everMemOSInstalled && !lowMemory && (
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
