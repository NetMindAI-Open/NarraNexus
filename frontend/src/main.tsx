import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './lib/echarts-nm-theme'  // side-effect: registers nm-light / nm-dark ECharts themes at startup
import App from './App.tsx'
import {
    initManyfoldFragmentAuth,
    installManyfoldFragmentHashListener
} from './lib/manyfoldFragmentAuth'
import { installExternalLinkInterceptor } from './lib/externalLinkInterceptor'
import { captureInboundEntry } from './lib/netmindAuth/tokenInbound'

// Run BEFORE the first render so the App tree never observes a
// "logged-out" state when the user was sent here via Manyfold's
// "Open Native UI" link. Idempotent / no-op for direct local-mode visits.
initManyfoldFragmentAuth()
installManyfoldFragmentHashListener()

// Capture ?token / ?source from the TRUE entry URL synchronously, before the
// first render. A logged-out arena entry (`/?source=arena`) is otherwise lost:
// RootRedirect synchronously navigates to /login, and that descendant
// <Navigate> effect rewrites the URL before App's mount effect can read it.
// Stashing `source` here lets the post-login Arena provisioning still fire.
captureInboundEntry()

// Tauri-only: make <a target="_blank"> actually open in the OS browser.
// In a regular browser this returns a no-op uninstaller so nothing changes.
// See lib/externalLinkInterceptor.ts for the rationale.
installExternalLinkInterceptor()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
