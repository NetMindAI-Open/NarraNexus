/**
 * Main App component with routing
 * Route-level code splitting: LoginPage and MainLayout use React.lazy for on-demand loading
 */

import { useState, useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTheme, useTimezoneSync } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { api, getBaseUrl } from '@/lib/api';
import { getRuntimeConfig, isForcedCloud, isForcedLocal } from '@/lib/runtimeConfig';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const RegisterPage = lazy(() => import('@/pages/RegisterPage'));
const ModeSelectPage = lazy(() => import('@/pages/ModeSelectPage'));
const SetupPage = lazy(() => import('@/pages/SetupPage'));
const SystemPage = lazy(() => import('@/pages/SystemPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const DashboardPage = lazy(() => import('@/pages/DashboardPage'));

/** Full-screen loading placeholder */
function PageFallback() {
  return (
    <div className="h-screen w-screen flex items-center justify-center bg-[var(--bg-deep)]">
      <div className="w-8 h-8 border-2 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn, userId, logout } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const [validating, setValidating] = useState(true);

  useEffect(() => {
    if (!isLoggedIn || !userId) {
      setValidating(false);
      return;
    }
    // Validate that the session is still valid (JWT token accepted by backend)
    api.getAgents(userId)
      .then(res => {
        if (!res.success) logout();
      })
      .catch(() => {
        // Backend unreachable — don't force logout
      })
      .finally(() => setValidating(false));
  }, [isLoggedIn, userId]);

  // Order matters: check mode BEFORE isLoggedIn.
  //
  // When the user clicks "Switch Mode" in the sidebar, handleSwitchMode
  // clears both `mode` (to null) and `isLoggedIn` (to false) in a single
  // Zustand batch. Zustand updates commit synchronously, but the imperative
  // navigate('/mode-select') goes through React Router's transition queue,
  // which has lower priority. That means ProtectedRoute re-renders against
  // the NEW store state while still matched to the OLD /app/* location —
  // and if we checked isLoggedIn first, we'd redirect to /login before the
  // mode-select transition lands, stranding the user on a stale-mode
  // login form backed by the wrong API URL.
  //
  // By checking `!mode` first, we route "mode cleared" through /mode-select
  // regardless of who wins the race.
  if (!mode) return <Navigate to="/mode-select" replace />;
  if (!isLoggedIn) return <Navigate to="/login" replace />;
  if (validating) return <PageFallback />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  if (isLoggedIn) return <Navigate to="/" replace />;
  // Login/register need a resolved mode to know whether to render the
  // local (user_id only) or cloud (user_id + password) form and which
  // backend to hit. If we got here with mode=null (e.g. via a stale
  // persisted route after a mode switch), punt to mode-select first.
  if (!mode) return <Navigate to="/mode-select" replace />;
  return <>{children}</>;
}

/** Redirect root based on runtime state */
function RootRedirect() {
  const { isLoggedIn, userId } = useConfigStore();
  const { mode, setMode, initialize } = useRuntimeStore();
  const [checking, setChecking] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);

  // Force mode from deploy-injected runtime config (highest priority),
  // falling back to the legacy build-time VITE_FORCE_CLOUD flag.
  // Run inside useEffect so render isn't mutating store state directly.
  useEffect(() => {
    const forcedByRuntime = getRuntimeConfig().mode;
    const forcedByBuild = import.meta.env.VITE_FORCE_CLOUD === 'true';
    const desired: typeof mode =
      forcedByRuntime === 'cloud' ? 'cloud-web'
      : forcedByRuntime === 'local' ? 'local'
      : forcedByBuild ? 'cloud-web'
      : null;
    if (desired && mode !== desired) {
      setMode(desired);
      initialize();
    }
  }, [mode, setMode, initialize]);

  useEffect(() => {
    if (!isLoggedIn || !userId) {
      setChecking(false);
      return;
    }
    // Check if user has any providers configured
    const checkProviders = async () => {
      try {
        const baseUrl = getBaseUrl();
        const res = await fetch(`${baseUrl}/api/providers?user_id=${encodeURIComponent(userId)}`);
        const data = await res.json();
        if (data.success && data.data?.providers) {
          const count = Object.keys(data.data.providers).length;
          setNeedsSetup(count === 0);
        }
      } catch {
        // Backend not ready — don't block, just go to chat
        setNeedsSetup(false);
      }
      setChecking(false);
    };
    checkProviders();
  }, [isLoggedIn, userId]);

  if (!mode) {
    return <Navigate to="/mode-select" replace />;
  }
  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
  }
  if (checking) {
    return <PageFallback />;
  }
  if (needsSetup) {
    return <Navigate to="/setup" replace />;
  }
  return <Navigate to="/app/chat" replace />;
}

function App() {
  const { effectiveTheme } = useTheme();
  useTimezoneSync();

  useEffect(() => {
    document.documentElement.classList.toggle('dark', effectiveTheme === 'dark');
  }, [effectiveTheme]);

  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        {/* Public routes — /mode-select is blocked when the deploy pipeline
            has forced a mode (cloud-web server, or locked-down kiosk build). */}
        <Route
          path="/mode-select"
          element={
            (isForcedCloud() || isForcedLocal() || import.meta.env.VITE_FORCE_CLOUD === 'true')
              ? <Navigate to="/login" replace />
              : <ModeSelectPage />
          }
        />
        <Route
          path="/login"
          element={<PublicRoute><LoginPage /></PublicRoute>}
        />
        <Route
          path="/register"
          element={<PublicRoute><RegisterPage /></PublicRoute>}
        />

        {/* Setup — requires login */}
        <Route
          path="/setup"
          element={<ProtectedRoute><SetupPage /></ProtectedRoute>}
        />

        {/* Protected app routes */}
        <Route
          path="/app"
          element={<ProtectedRoute><MainLayout /></ProtectedRoute>}
        >
          <Route index element={<Navigate to="chat" replace />} />
          <Route path="chat" element={null} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        {/* Root redirect + catch-all */}
        <Route path="/" element={<RootRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export default App;
