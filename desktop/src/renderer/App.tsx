/**
 * @file App.tsx
 * @description Application root component — displays SetupWizard or Dashboard based on setup state
 */

import React, { useEffect, useState } from 'react'
import SetupWizard from './pages/SetupWizard'
import Dashboard from './pages/Dashboard'

type Page = 'loading' | 'setup' | 'setup-llm' | 'dashboard'

const App: React.FC = () => {
  const [page, setPage] = useState<Page>('loading')

  useEffect(() => {
    // Check if initial setup is complete
    window.nexus.getSetupState().then(({ setupComplete }) => {
      setPage(setupComplete ? 'dashboard' : 'setup')
    })
  }, [])

  // Switch to Dashboard after setup is complete
  const handleSetupComplete = () => {
    setPage('dashboard')
  }

  // Return to full settings page from Dashboard
  const handleOpenSettings = () => {
    setPage('setup')
  }

  // Go directly to LLM config phase from Dashboard
  const handleOpenLLMConfig = () => {
    setPage('setup-llm')
  }

  if (page === 'loading') {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-500">Loading...</p>
        </div>
      </div>
    )
  }

  if (page === 'setup' || page === 'setup-llm') {
    return (
      <SetupWizard
        onComplete={handleSetupComplete}
        initialPhase={page === 'setup-llm' ? 'config' : 'preflight'}
      />
    )
  }

  return (
    <Dashboard
      onOpenSettings={handleOpenSettings}
      onOpenLLMConfig={handleOpenLLMConfig}
    />
  )
}

export default App
