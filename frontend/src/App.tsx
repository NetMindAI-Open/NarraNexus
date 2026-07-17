/**
 * Main App component with routing
 * Route-level code splitting: LoginPage and MainLayout use React.lazy for on-demand loading
 */

import { useState, useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { isTauri, listenTauri, consumePendingDeepLink } from '@/lib/tauri';
import { useTheme, useTimezoneSync } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { getInboundEntry, exchangeInboundToken } from '@/lib/netmindAuth/tokenInbound';
import { runArenaLandingIfNeeded } from '@/lib/arenaLanding';
import { useUpdaterStore } from '@/stores/updaterStore';
import { api } from '@/lib/api';
import { isForcedCloud } from '@/lib/runtimeConfig';
import { MockBanner } from '@/components/ui/MockBanner';
import UpdateBanner from '@/components/UpdateBanner';
import { ArenaProvisioningModal } from '@/components/arena/ArenaProvisioningModal';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const SetupPage = lazy(() => import('@/pages/SetupPage'));
const SystemPage = lazy(() => import('@/pages/SystemPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const BundleExportPage = lazy(() => import('@/pages/BundleExportPage'));
const BundleImportPage = lazy(() => import('@/pages/BundleImportPage'));
const TeamDetailPage = lazy(() => import('@/pages/TeamDetailPage'));
const ManageAgentsPage = lazy(() => import('@/pages/ManageAgentsPage'));
const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
const YouWorkspace = lazy(() => import('@/pages/YouWorkspace'));
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
 * Resolve the app mode for this build. There is no user-facing chooser.
 *
 * The cloud website is the ONLY cloud surface: the deploy pipeline injects
 * `mode='cloud'` via /config.js → cloud-web. Every other build — desktop DMG,
 * `bash run.sh`, dev — is LOCAL ONLY. So:
 *   - forced cloud  → cloud-web
 *   - anything else → local (this also coerces any stale `cloud-app` choice
 *                     a previous build may have persisted in localStorage).
 * Call from a useEffect; it's a no-op once mode already matches.
 */
function useResolveAppMode() {
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  useEffect(() => {
    if (isForcedCloud()) {
      if (mode !== 'cloud-web') setMode('cloud-web');
    } else if (mode !== 'local') {
      setMode('local');
    }
  }, [mode, setMode]);
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn, userId, logout } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const [validating, setValidating] = useState(true);
  const location = useLocation();
  useResolveAppMode();

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

  // Mode is resolved synchronously by useResolveAppMode (cloud-web on the
  // website, local everywhere else). The only window where it's null is the
  // first tick after a logout/wipe before the effect runs — hold a spinner
  // rather than rendering a login form against an unresolved API URL.
  if (!mode) return <PageFallback />;
  if (!isLoggedIn) {
    // Preserve the URL the user was trying to reach so LoginPage can send
    // them back after auth. This is what makes "Install in NarraNexus →
    // Cloud" from www.narra.nexus land on the import page, not /chat.
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
  useResolveAppMode();

  if (isLoggedIn) return <Navigate to="/" replace />;
  // LoginPage needs a resolved mode to know whether to render the local
  // (user_id only) or cloud-web (NetMind account) form. useResolveAppMode
  // fills it in on the first tick; hold a spinner in the meantime.
  if (!mode) return <PageFallback />;
  return <>{children}</>;
}

/** Redirect root based on runtime state */
function RootRedirect() {
  const { isLoggedIn, userId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const [checking, setChecking] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  useResolveAppMode();

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

  // Warm the MainLayout chunk the moment we know the user is logged in, in
  // parallel with the provider check — so the redirect to /app/chat doesn't sit
  // on a cold lazy-load (that's the "opening the page" spinner). The import is
  // deduped, so this just kicks off the transform/download early.
  useEffect(() => {
    if (isLoggedIn) {
      void import('@/components/layout/MainLayout');
    }
  }, [isLoggedIn]);

  if (!mode) {
    return <PageFallback />;
  }
  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
  }
  if (checking) {
    return <PageFallback />;
  }
  // Only local installs walk the user through provider setup on first
  // login. The cloud website (cloud-web) starts every account on the
  // system free-tier quota (SystemProviderService), so a fresh cloud user
  // can chat immediately — the provider screen confused users who had no
  // key to enter. They can still add their own provider later from
  // Settings; the onboarding checklist + quota panel surface that path.
  if (needsSetup && mode === 'local') {
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

  // Stale JWT: api.ts (REST 401) AND wsManager (WS AuthError frame) both
  // dispatch narranexus:auth-expired when the cloud rejects the token.
  // We logout via configStore so ProtectedRoute redirects to /login, AND
  // surface a banner so the user understands WHY they were bounced —
  // previously the handler was silent and the dmg user who hadn't opened
  // the app for a week got teleported to login with no explanation, or
  // (on the WS side) was stranded on the chat surface with red "Token
  // expired" bubbles and no way out.
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(() => {
    const handler = () => {
      const { isLoggedIn, logout } = useConfigStore.getState();
      if (isLoggedIn) {
        logout();
        setSessionExpired(true);
        // Auto-dismiss after 12s — long enough to read, short enough
        // not to clutter the freshly-rendered /login surface.
        window.setTimeout(() => setSessionExpired(false), 12000);
      }
    };
    window.addEventListener('narranexus:auth-expired', handler);
    return () => window.removeEventListener('narranexus:auth-expired', handler);
  }, []);

  // Agent circuit-breaker open: wsManager dispatches this when the backend
  // refuses to start a run because the agent is paused (repeated auth/quota
  // failures) or cooling. Show a banner with a one-click "Resume" that clears
  // the pause (agents_circuit_breaker.py). Mirrors the quota/auth banners.
  const [circuitOpen, setCircuitOpen] = useState<{ agentId: string; reason: string } | null>(null);
  const [resuming, setResuming] = useState(false);
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ agentId: string; reason: string }>).detail;
      if (detail?.agentId) setCircuitOpen(detail);
    };
    window.addEventListener('narranexus:agent-circuit-open', handler);
    return () => window.removeEventListener('narranexus:agent-circuit-open', handler);
  }, []);
  const handleResumeAgent = async () => {
    if (!circuitOpen || resuming) return;
    setResuming(true);
    try {
      await api.resetAgentCircuitBreaker(circuitOpen.agentId);
      setCircuitOpen(null);
    } catch {
      // Leave the banner up so the user can retry; the paused agent is
      // unchanged. (Best-effort UI action — no destructive side effect.)
    } finally {
      setResuming(false);
    }
  };

  // #48: when the free tier runs out and we auto-switch to the user's own
  // provider, the backend writes a one-time SYSTEM notice tagged
  // source.type="free_tier_switch". Surface it as a dismissible banner and
  // mark it read so the reminder shows exactly once. Checked on mount and on
  // window focus (the switch happens mid-session, server-side, on a request
  // that otherwise succeeds — there's no error for api.ts to catch).
  const [freeTierSwitched, setFreeTierSwitched] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      if (!useConfigStore.getState().isLoggedIn) return;
      try {
        const res = await api.getNotices(true);
        const hit = (res.notices ?? []).find(
          (n) => n.source?.type === 'free_tier_switch',
        );
        if (hit && !cancelled) {
          setFreeTierSwitched(true);
          window.setTimeout(() => setFreeTierSwitched(false), 10000);
          void api.markNoticeRead(hit.message_id).catch(() => {});
        }
      } catch {
        // non-critical surface — never break the app over a notice check.
      }
    };
    void check();
    window.addEventListener('focus', check);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', check);
    };
  }, []);

  // Bring the unified auto-updater store online once per app load.
  // No-op on web/cloud. The store fetches the current snapshot AND
  // subscribes to live `updater:state` events; the global
  // `<UpdateBanner />` mounted below then surfaces the Ready state at
  // the top of every page. Teardown on unmount keeps StrictMode happy
  // (idempotent — `init()` guards against double-mount internally).
  useEffect(() => {
    const store = useUpdaterStore.getState();
    store.init();
    return () => useUpdaterStore.getState().teardown();
  }, []);

  // NetMind inbound token bootstrap (scenario A): when the page is opened
  // with ?token=<NetMind loginToken> (e.g. a link from netmind.ai or Arena),
  // strip the token from the URL immediately to avoid it leaking into history,
  // then exchange it for our session. `source` is stashed in sessionStorage
  // for downstream Phase 2 provisioning (credits, api-key generation, etc.).
  useEffect(() => {
    // Read the inbound params captured synchronously at startup
    // (main.tsx → captureInboundEntry), NOT window.location: by the time this
    // effect runs, a logged-out arena/redirect path may have already rewritten
    // the URL, dropping ?token/?source. `source` is stashed there too.
    const r = getInboundEntry();
    if (!r.handled || !r.token) return;
    if (useConfigStore.getState().isLoggedIn) return;
    const token = r.token;
    void exchangeInboundToken(token, r.source).then((res) => {
      if (res.success && res.user_id) {
        useConfigStore.getState().login(res.user_id, res.token || undefined, res.role || undefined, {
          displayName: res.display_name, email: res.email,
        });
        useConfigStore.getState().setNetmindToken(token);
      }
    }).catch(() => { /* fall through to login page */ });
  }, []);

  // Arena landing (scenario from arena42.ai): once the user is logged in — via
  // the inbound token above, or the normal LoginPage flow — provision (or
  // reuse) their Arena agent and open it. Covers both the "already logged in"
  // case (runs on mount) and the "logs in after landing" case (login subscribe).
  useEffect(() => {
    void runArenaLandingIfNeeded();
    const unsub = useConfigStore.subscribe((s, prev) => {
      if (s.isLoggedIn && !prev.isLoggedIn) void runArenaLandingIfNeeded();
    });
    return unsub;
  }, []);

  return (
    <>
      <MockBanner />
      <UpdateBanner />
      <ArenaProvisioningModal />
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
      {sessionExpired && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-amber-500,#d97706)] text-white px-4 py-2 text-sm text-center cursor-pointer font-[family-name:var(--font-sans)]"
          onClick={() => setSessionExpired(false)}
          role="alert"
        >
          Your session expired. Please sign in again. (click to dismiss)
        </div>
      )}
      {circuitOpen && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-red-500)] text-white px-4 py-2 text-sm text-center font-[family-name:var(--font-sans)] flex items-center justify-center gap-3"
          role="alert"
        >
          <span>
            {circuitOpen.reason.startsWith('paused:quota')
              ? 'This agent is paused after repeated quota/balance failures. Fix its provider, then resume.'
              : circuitOpen.reason.startsWith('paused')
                ? 'This agent is paused after repeated authentication failures. Re-authenticate or set a provider, then resume.'
                : 'This agent recently failed and is briefly cooling down. Try again shortly.'}
          </span>
          {circuitOpen.reason.startsWith('paused') && (
            <button
              type="button"
              className="underline font-semibold disabled:opacity-60"
              onClick={handleResumeAgent}
              disabled={resuming}
            >
              {resuming ? 'Resuming…' : 'Resume agent'}
            </button>
          )}
          <button
            type="button"
            className="opacity-80 hover:opacity-100"
            onClick={() => setCircuitOpen(null)}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
      {freeTierSwitched && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-emerald-600,#059669)] text-white px-4 py-2 text-sm text-center cursor-pointer font-[family-name:var(--font-sans)]"
          onClick={() => setFreeTierSwitched(false)}
          role="status"
        >
          Free-tier quota used up — switched to your own provider. New runs
          use your own API key. (click to dismiss)
        </div>
      )}
      <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route
          path="/login"
          element={<PublicRoute><LoginPage /></PublicRoute>}
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
          <Route path="you" element={<YouWorkspace />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="bundle/export" element={<BundleExportPage />} />
          <Route path="bundle/import" element={<BundleImportPage />} />
          {/* Deep-link entry point from narra.nexus templates marketplace.
              Same component as bundle/import; URL query (?url=&sha256=)
              triggers the auto-fetch-then-preflight path. */}
          <Route path="templates/install" element={<BundleImportPage />} />
          <Route path="teams/:teamId" element={<TeamDetailPage />} />
          {/* Team group chat — element null; MainLayout renders TeamChatView
              in the main slot (like /app/chat) so it isn't a sub-page overlay. */}
          <Route path="teams/:teamId/chat" element={null} />
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
