/**
 * @file UpdateBanner.tsx
 * @description Auto-update notification banner — shows update availability, download progress, and install action
 */

import React, { useEffect, useState } from 'react'

const UpdateBanner: React.FC = () => {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    const unsubscribe = window.nexus.onUpdateStatus((s: UpdateStatus) => {
      setStatus(s)
      setDismissed(false) // Re-show on new status
    })
    return unsubscribe
  }, [])

  // Don't show banner for these states
  if (!status || dismissed) return null
  if (status.state === 'checking' || status.state === 'not-available') return null

  // Error: show briefly then allow dismiss
  if (status.state === 'error') {
    return (
      <div className="mx-5 mt-2 px-4 py-2 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between text-sm">
        <span className="text-red-700">Update check failed: {status.error}</span>
        <button onClick={() => setDismissed(true)} className="text-red-400 hover:text-red-600 ml-3 text-xs">
          Dismiss
        </button>
      </div>
    )
  }

  // Update available — prompt download
  if (status.state === 'available') {
    return (
      <div className="mx-5 mt-2 px-4 py-2.5 bg-blue-50 border border-blue-200 rounded-lg flex items-center justify-between text-sm">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          <span className="text-blue-800">
            New version <strong>v{status.version}</strong> is available
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => window.nexus.downloadUpdate()}
            className="px-3 py-1 text-xs font-medium text-white bg-blue-500 rounded hover:bg-blue-600 transition-colors"
          >
            Download
          </button>
          <button onClick={() => setDismissed(true)} className="text-blue-400 hover:text-blue-600 text-xs">
            Later
          </button>
        </div>
      </div>
    )
  }

  // Downloading — show progress
  if (status.state === 'downloading') {
    const pct = status.progress ?? 0
    return (
      <div className="mx-5 mt-2 px-4 py-2.5 bg-blue-50 border border-blue-200 rounded-lg text-sm">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-blue-800">Downloading update...</span>
          <span className="text-blue-600 text-xs font-mono">{pct}%</span>
        </div>
        <div className="w-full h-1.5 bg-blue-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    )
  }

  // Downloaded — prompt install
  if (status.state === 'downloaded') {
    return (
      <div className="mx-5 mt-2 px-4 py-2.5 bg-green-50 border border-green-200 rounded-lg flex items-center justify-between text-sm">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <span className="text-green-800">
            Version <strong>v{status.version}</strong> is ready to install
          </span>
        </div>
        <button
          onClick={() => window.nexus.installUpdate()}
          className="px-3 py-1 text-xs font-medium text-white bg-green-500 rounded hover:bg-green-600 transition-colors"
        >
          Restart & Update
        </button>
      </div>
    )
  }

  return null
}

export default UpdateBanner
