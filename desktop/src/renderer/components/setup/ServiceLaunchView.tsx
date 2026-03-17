/**
 * @file ServiceLaunchView.tsx
 * @description Phase 3 UI — service startup progress display
 *
 * Shows launch steps with real-time status, log messages, and retry button on failure.
 */

import React, { useEffect, useState } from 'react'

interface ServiceLaunchViewProps {
  skipEverMemOS: boolean
  onComplete: () => void
  onRetry: () => void
}

const StatusIcon: React.FC<{ status: LaunchStepStatus }> = ({ status }) => {
  switch (status) {
    case 'done': return <span className="text-green-500 text-sm leading-5">&#10003;</span>
    case 'running': return (
      <div className="w-3.5 h-3.5 mt-0.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
    )
    case 'error': return <span className="text-red-500 text-sm leading-5">&#10007;</span>
    case 'skipped': return <span className="text-gray-400 text-sm leading-5">&#8212;</span>
    default: return <span className="text-gray-300 text-sm leading-5">&#9679;</span>
  }
}

const ServiceLaunchView: React.FC<ServiceLaunchViewProps> = ({
  skipEverMemOS,
  onComplete,
  onRetry
}) => {
  const [steps, setSteps] = useState<Map<string, LaunchStep>>(new Map())
  const [launching, setLaunching] = useState(false)
  const [result, setResult] = useState<{ success: boolean; error?: string } | null>(null)

  // Listen for launch step updates
  useEffect(() => {
    const unsub = window.nexus.onLaunchStep((step: LaunchStep) => {
      setSteps((prev) => {
        const next = new Map(prev)
        next.set(step.id, step)
        return next
      })
    })
    return unsub
  }, [])

  // Auto-start launch on mount
  useEffect(() => {
    startLaunch()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const startLaunch = async () => {
    setLaunching(true)
    setResult(null)
    setSteps(new Map())
    const res = await window.nexus.runLaunch({ skipEverMemOS })
    setResult(res)
    setLaunching(false)
    if (res.success) {
      setTimeout(onComplete, 800)
    }
  }

  const handleRetry = () => {
    onRetry()
    startLaunch()
  }

  // Ordered step IDs
  const stepOrder: LaunchStepId[] = [
    'wait-docker', 'compose-up', 'wait-mysql', 'wait-synapse', 'init-tables', 'wait-evermemos', 'start-services'
  ]

  const defaultLabels: Record<LaunchStepId, string> = {
    'wait-docker': 'Wait for Docker',
    'compose-up': 'Start containers',
    'wait-mysql': 'Wait for MySQL',
    'wait-synapse': 'Wait for Synapse',
    'init-tables': 'Initialize database',
    'wait-evermemos': 'Wait for EverMemOS infra',
    'start-services': 'Start services'
  }

  return (
    <div className="space-y-4">
      <p className="text-sm font-medium text-gray-700">Starting Services</p>

      <div className="space-y-1.5 p-4 bg-gray-50 rounded-lg">
        {stepOrder.map((id) => {
          const step = steps.get(id)
          const status = step?.status ?? 'pending'
          const label = step?.label ?? defaultLabels[id]
          const message = step?.message

          return (
            <div key={id} className="flex items-start gap-2">
              <StatusIcon status={status} />
              <div className="flex-1 min-w-0">
                <p className={`text-sm leading-5 ${
                  status === 'error' ? 'text-red-600' :
                  status === 'running' ? 'text-blue-600 font-medium' :
                  status === 'skipped' ? 'text-gray-400' :
                  status === 'done' ? 'text-gray-600' :
                  'text-gray-400'
                }`}>
                  {label}
                </p>
                {message && status === 'running' && (
                  <p className="text-xs text-gray-400 mt-0.5 truncate">{message}</p>
                )}
                {message && status === 'error' && (
                  <p className="text-xs text-red-500 mt-0.5 break-words">{message}</p>
                )}
                {message && status === 'done' && (
                  <p className="text-xs text-gray-400 mt-0.5">{message}</p>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Result actions */}
      {result && !result.success && (
        <div className="p-3 bg-red-50 rounded-lg">
          <p className="text-sm text-red-700">{result.error || 'An unknown error occurred'}</p>
          <button
            onClick={handleRetry}
            className="titlebar-no-drag mt-2 px-4 py-1.5 text-xs font-medium text-red-600 bg-red-100 rounded-lg hover:bg-red-200 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {result?.success && (
        <div className="text-center">
          <p className="text-sm text-green-600 font-medium">Core services started! Proceeding to configuration...</p>
        </div>
      )}
    </div>
  )
}

export default ServiceLaunchView
