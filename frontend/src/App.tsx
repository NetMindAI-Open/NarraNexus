/**
 * Main App component with routing
 * Route-level code splitting: LoginPage and MainLayout use React.lazy for on-demand loading
 */

import { useState, useEffect, useSyncExternalStore, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { isTauri, listenTauri, consumePendingDeepLink } from '@/lib/tauri';
import { useCircuitBannerAutoClear, usePluginTheme, useTheme, useTimezoneSync } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { getInboundEntry, exchangeInboundToken } from '@/lib/netmindAuth/tokenInbound';
import { runArenaLandingIfNeeded } from '@/lib/arenaLanding';
import { useUpdaterStore } from '@/stores/updaterStore';
import { usePowerStore } from '@/stores/powerStore';
import { api } from '@/lib/api';
import { getSessionToken } from '@/lib/authHeaders';
import {
  EXPIRY_CHECK_INTERVAL_MS,
  formatExpiryDistance,
  msUntilExpiry,
  shouldShowExpiryWarning,
} from '@/lib/tokenExpiry';
import { isForcedCloud } from '@/lib/runtimeConfig';
import { captureProductEvent } from '@/lib/productAnalytics';
import { PAGES, useRegistryEntries } from '@/platform/registries';
import { pageRouteElements } from '@/platform/pageRoutes';
import { pluginsBootSettled, subscribePluginsBoot } from '@/platform/loader';
import { owesWelcomeFlow } from '@/lib/onboardingGate';
import { initWebAnalytics } from '@/lib/analytics/webAnalytics';
import { MockBanner } from '@/components/ui/MockBanner';
import UpdateBanner from '@/components/UpdateBanner';
import { ArenaProvisioningModal } from '@/components/arena/ArenaProvisioningModal';
import { ChunkErrorBoundary } from '@/components/ChunkErrorBoundary';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));

/** Full-screen loading placeholder */
function PageFallback() {
  return (
    <div className="h-dvh-safe w-screen flex items-center justify-center bg-[var(--bg-deep)]">
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

function ProtectedRoute({
  children,
  // /welcome gates itself (it IS the flow) and /pay must not be interrupted —
  // that route carries a payment intent through login and back out to Stripe.
  skipWelcomeGate = false,
}: {
  children: React.ReactNode;
  skipWelcomeGate?: boolean;
}) {
  const { isLoggedIn, userId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const [validating, setValidating] = useState(true);
  const [owesWelcome, setOwesWelcome] = useState<boolean | null>(null);
  const location = useLocation();
  useResolveAppMode();

  /** Whether this route has to consult the first-run gate at all. Derived, not
   *  state, so the "no" cases need no setState (and no effect). */
  const gateNeeded = !skipWelcomeGate && isLoggedIn && Boolean(userId);

  // First-run gate. Here rather than only in RootRedirect because `/` is just
  // one way in: a ?next=/app/chat login, a bookmark or a refresh lands on a
  // protected route directly, and a new account must not skip the welcome flow
  // by arriving through one of those. Answer is cached per session
  // (lib/onboardingGate), so this costs one request per login, not per mount.
  useEffect(() => {
    if (!gateNeeded) return;
    let alive = true;
    owesWelcomeFlow(userId)
      .then((owes) => { if (alive) setOwesWelcome(owes); })
      .catch(() => { if (alive) setOwesWelcome(false); });
    return () => { alive = false; };
  }, [gateNeeded, userId]);

  useEffect(() => {
    if (!isLoggedIn || !userId) {
      setValidating(false);
      return;
    }
    // Touch the backend once so a session that died while the tab was shut
    // is noticed now rather than on the user's next click. The verdict is
    // not made here: a 401 goes through lib/api into lib/sessionGuard, which
    // confirms before anything is torn down. The response body is irrelevant.
    //
    // Two things this deliberately is NOT:
    //
    // - It is not `getAgents()` any more. That pulls the user's whole agent
    //   list (plus active-run and preview enrichment) out of the database on
    //   every protected mount, to then discard it. `/api/auth/session` does
    //   no database work at all — same signal, none of the cost.
    // - It does not `logout()` on `!res.success`, as it used to. GET
    //   /api/auth/agents answers 200 + {success:false, error} for ANY
    //   unhandled exception in the handler — a database hiccup during mount,
    //   say. That is not an authentication failure, and ending the session
    //   over it is the same nuclear reflex the 401 path just stopped doing.
    api.getSession()
      .then(() => captureProductEvent('workspace_ready'))
      .catch(() => {
        // Backend unreachable, or a 401 already handed to the session
        // guard by lib/api. Either way, nothing to do here.
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
    // Cloud" from www.narra.nexus land on the import page, not /chat, and
    // what carries the /pay payment intent through login. Signup honors it
    // too: SignUpDialog lives ON /login (the URL keeps ?next=) and its
    // onRegistered chains into the same emailLogin onSuccess that reads it —
    // verified 2026-07-31, all three auth paths return to `next`.
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (validating || (gateNeeded && owesWelcome === null)) return <PageFallback />;
  if (gateNeeded && owesWelcome) {
    // Remember where they were headed so the flow can hand them back to it.
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/welcome?next=${next}`} replace />;
  }
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
  const [needsWelcome, setNeedsWelcome] = useState(false);
  useResolveAppMode();

  useEffect(() => {
    if (!isLoggedIn || !userId) {
      setChecking(false);
      return;
    }
    // Does this user still owe the one-time welcome flow? Same cached,
    // server-side answer ProtectedRoute uses — see lib/onboardingGate for why
    // it is not a provider count any more.
    owesWelcomeFlow(userId)
      .then(setNeedsWelcome)
      .catch(() => setNeedsWelcome(false))
      .finally(() => setChecking(false));
  }, [isLoggedIn, userId]);

  // Warm the MainLayout chunk the moment we know the user is logged in, in
  // parallel with the provider check — so the redirect to /app/chat doesn't sit
  // on a cold lazy-load (that's the "opening the page" spinner). The import is
  // deduped, so this just kicks off the transform/download early.
  useEffect(() => {
    if (isLoggedIn) {
      // Background prefetch: a failure here is not user-facing (the real
      // navigation retries + ChunkErrorBoundary covers that path), so swallow
      // the rejection explicitly rather than leaving an unhandled one.
      import('@/components/layout/MainLayout').catch(() => {});
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
  // Both modes get the welcome flow now — its steps adapt (cloud has a
  // free-tier provider already and no importable filesystem, so it lands on
  // "meet your first agent"). WelcomePage itself bails out to /app/chat when
  // its step list comes back empty, so this redirect can never trap anyone.
  if (needsWelcome) {
    return <Navigate to="/welcome" replace />;
  }
  return <Navigate to="/app/chat" replace />;
}

function App() {
  const { t } = useTranslation();
  const { effectiveTheme } = useTheme();
  usePluginTheme();
  useTimezoneSync();
  const navigate = useNavigate();

  useEffect(() => {
    document.documentElement.classList.toggle('dark', effectiveTheme === 'dark');
  }, [effectiveTheme]);

  // Locked Use: re-assert a persisted prevent-sleep state on startup — the
  // previous process's OS assertion (caffeinate child) died with it.
  // No-op on web and when the toggle is off (see stores/powerStore).
  useEffect(() => {
    void usePowerStore.getState().applyOnStartup();
  }, []);

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
    const handler = (e: Event) => {
      const { isLoggedIn, logout } = useConfigStore.getState();
      if (isLoggedIn) {
        // Record what actually triggered the teardown. The 2026-08-02
        // incident was reported as "the page reloaded and everything got
        // confusing" and we had nothing client-side to tie that to a
        // specific endpoint — the server logs were a list of 401s with no
        // reason attached. lib/sessionGuard puts both on the event.
        const detail = (e as CustomEvent<{ endpoint?: string; code?: string | null }>).detail;
        console.warn(
          `[auth] session confirmed dead — trigger=${detail?.endpoint ?? 'unknown'} ` +
            `code=${detail?.code ?? 'none'}`,
        );
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

  // Pre-expiry warning. The JWT lasts 7 days and cannot be refreshed, so
  // without this the session simply stops working mid-click. Warning a day
  // ahead lets the user re-login when it costs them nothing, instead of
  // losing whatever was on screen at the moment the clock ran out.
  const [expiresInMs, setExpiresInMs] = useState<number | null>(null);
  // Remaining time at the moment of dismissal — see shouldShowExpiryWarning.
  const [expiryDismissedAt, setExpiryDismissedAt] = useState<number | null>(null);
  // Subscribed, not read via getState(): logging out must clear the banner
  // immediately. Polling alone would leave "your session expires in 5 hours"
  // sitting on top of the /login page for up to a full tick.
  const isLoggedIn = useConfigStore((s) => s.isLoggedIn);

  // Third-party web analytics (GTM). Loaded only once the user is authenticated
  // so the loader can read their per-user opt-out first. No-op on desktop, off
  // the official host, when unconfigured, or when the user opted out; see
  // lib/analytics/webAnalytics.ts.
  useEffect(() => {
    if (isLoggedIn) void initWebAnalytics();
  }, [isLoggedIn]);

  useEffect(() => {
    const check = () => {
      // null in local mode (no JWT) — there is nothing to expire there.
      setExpiresInMs(isLoggedIn ? msUntilExpiry(getSessionToken()) : null);
    };
    check();
    // Cheap (one base64 decode), so a slow tick is plenty; `focus` covers
    // the laptop-was-asleep case, where hours pass between two ticks.
    const id = window.setInterval(check, EXPIRY_CHECK_INTERVAL_MS);
    window.addEventListener('focus', check);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('focus', check);
    };
  }, [isLoggedIn]);

  // Agent circuit-breaker open: wsManager dispatches this when the backend
  // refuses to start a run because the agent is paused (repeated auth/quota
  // failures) or cooling. Show a banner with a one-click "Resume" that clears
  // the pause (agents_circuit_breaker.py). Mirrors the quota/auth banners.
  const [circuitOpen, setCircuitOpen] = useState<{ agentId: string; reason: string } | null>(null);
  const [resuming, setResuming] = useState(false);
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ agentId: string; reason: string }>).detail;
      if (!detail?.agentId) return;
      // Same agent + same reason → keep the existing state object. Every
      // rejected turn fires this event; a fresh object per retry would
      // re-render and re-key everything hanging off the banner for nothing.
      setCircuitOpen((prev) =>
        prev && prev.agentId === detail.agentId && prev.reason === detail.reason ? prev : detail,
      );
    };
    window.addEventListener('narranexus:agent-circuit-open', handler);
    return () => window.removeEventListener('narranexus:agent-circuit-open', handler);
  }, []);
  // GitHub #117: the banner above is purely event-driven — it never re-checks
  // reality, so it stays up even after the agent self-heals. While a "paused"
  // banner is showing (and the user is logged in), poll the existing
  // per-agent status endpoint and close the banner once the breaker is no
  // longer paused. See hooks/useCircuitBannerAutoClear for the contract.
  useCircuitBannerAutoClear(circuitOpen, setCircuitOpen, isLoggedIn);
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
      {sessionExpired && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-amber-500,#d97706)] text-white px-4 py-2 text-sm text-center cursor-pointer font-[family-name:var(--font-sans)]"
          onClick={() => setSessionExpired(false)}
          role="alert"
        >
          {t('appBanners.sessionExpired')}
        </div>
      )}
      {!sessionExpired &&
        expiresInMs !== null &&
        shouldShowExpiryWarning(expiresInMs, expiryDismissedAt) && (
          <div
            className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-amber-500,#d97706)] text-white px-4 py-2 text-sm text-center cursor-pointer font-[family-name:var(--font-sans)]"
            onClick={() => setExpiryDismissedAt(expiresInMs)}
            role="status"
          >
            {t('appBanners.sessionExpiringSoon', { time: formatExpiryDistance(expiresInMs) })}
          </div>
        )}
      {circuitOpen && (
        <div
          className="fixed top-0 left-0 right-0 z-50 bg-[var(--color-error)] text-white px-4 py-2 text-sm text-center font-[family-name:var(--font-sans)] flex items-center justify-center gap-3"
          role="alert"
        >
          <span>
            {circuitOpen.reason.startsWith('paused:quota')
              ? t('appBanners.circuitPausedQuota')
              : circuitOpen.reason.startsWith('paused')
                ? t('appBanners.circuitPausedAuth')
                : t('appBanners.circuitCooling')}
          </span>
          {circuitOpen.reason.startsWith('paused') && (
            <button
              type="button"
              className="underline font-semibold disabled:opacity-60"
              onClick={handleResumeAgent}
              disabled={resuming}
            >
              {resuming ? t('appBanners.resuming') : t('appBanners.resumeAgent')}
            </button>
          )}
          <button
            type="button"
            className="opacity-80 hover:opacity-100"
            onClick={() => setCircuitOpen(null)}
            aria-label={t('appBanners.dismiss')}
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
          {t('appBanners.freeTierSwitched')}
        </div>
      )}
      <ChunkErrorBoundary>
      <Suspense fallback={<PageFallback />}>
        <AppRoutes />
      </Suspense>
      </ChunkErrorBoundary>
    </>
  );
}

/**
 * The route table: registry pages around the fixed skeleton (the protected
 * /app layout with its index redirect, the root redirect, the catch-all).
 * Exported so `src/__tests__/appRoutes.test.tsx` can prove the registry is
 * what the shell renders, without mounting the banners and store effects
 * above. Pages come from the registry; a plugin registering after first
 * render re-renders the table.
 */
/** Exported for `appRoutes.test.tsx`: the hold is what keeps a plugin deep link alive across a reload. */
export function PluginPagePending() {
  const settled = useSyncExternalStore(subscribePluginsBoot, pluginsBootSettled, pluginsBootSettled);
  if (!settled) return <PageFallback />;
  return <Navigate to="/app/chat" replace />;
}

export function AppRoutes() {
  const pageRoutes = pageRouteElements(useRegistryEntries(PAGES), { ProtectedRoute, PublicRoute });
  return (
    <Routes>
      {pageRoutes.top}

      {/* Protected app routes: MainLayout is the shell; its children come
          from the page registry in registration order (builtin first). */}
      <Route
        path="/app"
        element={<ProtectedRoute><MainLayout /></ProtectedRoute>}
      >
        <Route index element={<Navigate to="chat" replace />} />
        {pageRoutes.app}
        {/* Plugin pages live under x/. On a hard reload the factory has not
            answered yet when this table first renders, so an x/ URL that no
            registered page matches waits for the plugin boot to settle instead
            of being sent to chat; a registered x/<page> always ranks above x/*. */}
        <Route path="x/*" element={<PluginPagePending />} />
      </Route>

      {/* Root redirect + catch-all */}
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
