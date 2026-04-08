/**
 * Main App component with routing
 * Route-level code splitting: LoginPage and MainLayout use React.lazy for on-demand loading
 */

import { useState, useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTheme, useTimezoneSync } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { api } from '@/lib/api';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const RegisterPage = lazy(() => import('@/pages/RegisterPage'));
const ModeSelectPage = lazy(() => import('@/pages/ModeSelectPage'));
const SetupPage = lazy(() => import('@/pages/SetupPage'));
const SystemPage = lazy(() => import('@/pages/SystemPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));

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

  if (!isLoggedIn) return <Navigate to="/login" replace />;
  if (validating) return <PageFallback />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn } = useConfigStore();
  if (isLoggedIn) return <Navigate to="/" replace />;
  return <>{children}</>;
}

/** Redirect root based on runtime state */
function RootRedirect() {
  const { isLoggedIn } = useConfigStore();
  const { mode, initialized, setMode, initialize } = useRuntimeStore();

  // Cloud web deployment: force cloud-web mode, skip mode select
  const forceCloud = import.meta.env.VITE_FORCE_CLOUD === 'true';
  if (forceCloud && !mode) {
    setMode('cloud-web');
    initialize();
    return <Navigate to="/login" replace />;
  }

  if (!initialized && !mode) {
    return <Navigate to="/mode-select" replace />;
  }
  if (!initialized && mode === 'local') {
    return <Navigate to="/setup" replace />;
  }
  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
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
        {/* Public routes */}
        <Route path="/mode-select" element={<ModeSelectPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route
          path="/login"
          element={<PublicRoute><LoginPage /></PublicRoute>}
        />
        <Route
          path="/register"
          element={<PublicRoute><RegisterPage /></PublicRoute>}
        />

        {/* Protected app routes */}
        <Route
          path="/app"
          element={<ProtectedRoute><MainLayout /></ProtectedRoute>}
        >
          <Route index element={<Navigate to="chat" replace />} />
          <Route path="chat" element={null} />
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
