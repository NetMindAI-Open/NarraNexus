/**
 * Main App component with routing
 * Route-level code splitting: LoginPage and MainLayout use React.lazy for on-demand loading
 */

import { useState, useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { isTauri, listenTauri, consumePendingDeepLink } from '@/lib/tauri';
import { useTheme, useTimezoneSync } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { api } from '@/lib/api';
import { getRuntimeConfig, isForcedCloud, isForcedLocal } from '@/lib/runtimeConfig';
import { MockBanner } from '@/components/ui/MockBanner';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const RegisterPage = lazy(() => import('@/pages/RegisterPage'));
const ModeSelectPage = lazy(() => import('@/pages/ModeSelectPage'));
const SetupPage = lazy(() => import('@/pages/SetupPage'));
const SystemPage = lazy(() => import('@/pages/SystemPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const BundleExportPage = lazy(() => import('@/pages/BundleExportPage'));
const BundleImportPage = lazy(() => import('@/pages/BundleImportPage'));
const TeamDetailPage = lazy(() => import('@/pages/TeamDetailPage'));
const ManageAgentsPage = lazy(() => import('@/pages/ManageAgentsPage'));
const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
// NM design system dev gallery — public (no auth) so it can be loaded
// before login during visual review. Not linked from any nav.
const NMPlaygroundPage = lazy(() => import('@/pages/NMPlaygroundPage'));

/** Full-screen loading placeholder */
function PageFallback() {
  return (
    <div className="h-screen w-screen flex items-center justify-center bg-[var(--bg-deep)]">
      <div className="w-8 h-8 border-2 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

/**
 * Recover a null mode when the deploy pipeline has forced one.
 *
 * After logout/wipe, localStorage is cleared and the runtimeStore hydrates
 * with mode=null. Without this recovery, Public/ProtectedRoute would
 * redirect to /mode-select → which in forced deployments redirects back to
 * /login → infinite loop → black screen. Call this from a useEffect; it's
 * a no-op when no force is configured or when mode is already set.
 */
function useAutoRestoreForcedMode() {
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  useEffect(() => {
    if (mode) return;
    if (isForcedCloud()) setMode('cloud-web');
    else if (isForcedLocal()) setMode('local');
  }, [mode, setMode]);
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn, userId, logout } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const [validating, setValidating] = useState(true);
  const location = useLocation();
  useAutoRestoreForcedMode();

  useEffect(() => {
    if (!isLoggedIn || !userId) {
      setValidating(false);
      return;
    }
    // Validate that the session is still valid (JWT token accepted by backend)
    api.getAgents()
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
  // regardless of who wins the race — EXCEPT in forced deployments, where
  // /mode-select bounces back here, so we short-circuit to avoid the loop.
  if (!mode) {
    if (isForcedCloud() || isForcedLocal()) return <PageFallback />;
    return <Navigate to="/mode-select" replace />;
  }
  if (!isLoggedIn) {
    // Preserve the URL the user was trying to reach so LoginPage can send
    // them back after auth. This is what makes "Install in NarraNexus →
    // Cloud" from website.narra.nexus land on the import page, not /chat.
    // RegisterPage does NOT read `next` yet — fresh signups still land on
    // the default. Tracked as a known edge case.
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (validating) return <PageFallback />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  useAutoRestoreForcedMode();

  if (isLoggedIn) return <Navigate to="/" replace />;
  // Login/register need a resolved mode to know whether to render the
  // local (user_id only) or cloud (user_id + password) form and which
  // backend to hit. If we got here with mode=null (e.g. via a stale
  // persisted route after a mode switch), punt to mode-select first.
  //
  // EXCEPT in forced-mode deployments: /mode-select bounces back to
  // /login, so punting here causes an infinite redirect loop (the user
  // sees a black screen after logout). useAutoRestoreForcedMode() above
  // will fill mode in on the next tick; render a loading placeholder
  // in the meantime.
  if (!mode) {
    if (isForcedCloud() || isForcedLocal()) return <PageFallback />;
    return <Navigate to="/mode-select" replace />;
  }
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
    // Check if user has any providers configured. Use api.getProviders
    // so the X-User-Id / JWT headers travel with the request — bare
    // fetch sends neither, and the backend used to silently fall back
    // to "first user in users table" which scoped this probe to the
    // wrong account on multi-user local installs.
    const checkProviders = async () => {
      try {
        const data = await api.getProviders();
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
  const navigate = useNavigate();

  useEffect(() => {
    document.documentElement.classList.toggle('dark', effectiveTheme === 'dark');
  }, [effectiveTheme]);

  // Deep-link handler: route narranexus:// URLs from the website (or any
  // app firing `open narranexus://...`) into the in-app install flow.
  // The Rust side (tauri/src-tauri/src/lib.rs) registers an on_open_url
  // callback that BOTH emits this event AND stashes the URL in
  // AppState::pending_deep_link, so we cover the hot case (event listener
  // already mounted) and the cold case (URL arrived during app launch,
  // before React was alive to listen).
  useEffect(() => {
    if (!isTauri()) return;

    const handleUrl = (raw: string) => {
      try {
        const u = new URL(raw);
        // narranexus://install?url=...&sha256=... — host segment is "install".
        // Browsers/parsers sometimes surface custom-scheme URLs with the
        // path "/install" instead, so accept both shapes.
        if (u.host === 'install' || u.pathname === '/install' || u.pathname === 'install') {
          navigate(`/app/templates/install${u.search}`);
        } else {
          console.warn('[deep-link] unhandled URL shape:', raw);
        }
      } catch (e) {
        console.warn('[deep-link] failed to parse URL:', raw, e);
      }
    };

    // (1) Cold-start: drain the URL Rust buffered before we mounted.
    consumePendingDeepLink().then((url) => {
      if (url) handleUrl(url);
    });

    // (2) Hot: subscribe to live URL arrivals (already-running case forwarded
    //     via single-instance plugin's deep-link feature).
    let unlisten: (() => void) | null = null;
    listenTauri('deep-link-received', (ev) => {
      const payload =
        ev && typeof ev === 'object' && 'payload' in ev
          ? (ev as { payload: unknown }).payload
          : ev;
      if (typeof payload === 'string') handleUrl(payload);
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [navigate]);

  // Surface "quota exhausted" globally. api.ts dispatches a CustomEvent
  // on HTTP 402 + error_code=QUOTA_EXCEEDED_NO_USER_PROVIDER; we show a
  // dismissible top banner prompting the user to configure their own
  // provider. Auto-dismisses after 8s so it doesn't stick forever.
  const [quotaExceeded, setQuotaExceeded] = useState(false);
  useEffect(() => {
    const handler = () => {
      setQuotaExceeded(true);
      window.setTimeout(() => setQuotaExceeded(false), 8000);
    };
    window.addEventListener('narranexus:quota-exceeded', handler);
    return () => window.removeEventListener('narranexus:quota-exceeded', handler);
  }, []);

  // Stale JWT: api.ts dispatches narranexus:auth-expired when an
  // authenticated request comes back 401 (token rejected by backend).
  // Clear auth state via configStore so ProtectedRoute redirects to /login
  // instead of letting the UI keep firing 401s in a broken state.
  useEffect(() => {
    const handler = () => {
      const { isLoggedIn, logout } = useConfigStore.getState();
      if (isLoggedIn) logout();
    };
    window.addEventListener('narranexus:auth-expired', handler);
    return () => window.removeEventListener('narranexus:auth-expired', handler);
  }, []);

  return (
    <>
      <MockBanner />
      {quotaExceeded && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-red-500)] text-white px-4 py-2 text-sm text-center cursor-pointer font-[family-name:var(--font-sans)]"
          onClick={() => setQuotaExceeded(false)}
          role="alert"
        >
          Free-tier quota exhausted. Open Settings → Providers to add
          your own API key. (click to dismiss)
        </div>
      )}
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

        {/* NM design system gallery — public dev tool, no auth required */}
        <Route path="/nm-playground" element={<NMPlaygroundPage />} />

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
          <Route path="bundle/export" element={<BundleExportPage />} />
          <Route path="bundle/import" element={<BundleImportPage />} />
          {/* Deep-link entry point from narra.nexus templates marketplace.
              Same component as bundle/import; URL query (?url=&sha256=)
              triggers the auto-fetch-then-preflight path. */}
          <Route path="templates/install" element={<BundleImportPage />} />
          <Route path="teams/:teamId" element={<TeamDetailPage />} />
          <Route path="manage-agents" element={<ManageAgentsPage />} />
        </Route>

        {/* Root redirect + catch-all */}
        <Route path="/" element={<RootRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
    </>
  );
}

export default App;
