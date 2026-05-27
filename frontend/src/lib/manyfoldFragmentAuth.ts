/**
 * Manyfold-handoff auto-login.
 *
 * When the native UI is opened via the Manyfold UI's "Open Native UI"
 * link, the URL fragment carries both the gateway token (the shared
 * secret the platform injected as MANYFOLD_GATEWAY_TOKEN), the
 * Manyfold-side user id, and optionally the specific agent_id the user
 * was viewing in Manyfold:
 *
 *     http://<runtime-host>/#token=<gateway>&user=mf_user_local_admin&agent=<agent_id>
 *
 * The `agent` hint lets Manyfold's per-agent "Endpoint" link land
 * directly in that agent's chat tab rather than whichever agent the user
 * last viewed (defaulting to configStore.agentId from prior session).
 *
 * Why fragment (not query)?
 *   - URL fragments are NEVER sent to the server in the HTTP request
 *     line, so this token cannot accidentally leak into nginx access
 *     logs or be captured by the platform's TLS terminator.
 *   - Cf. openclaw's control UI URL pattern in
 *     `k8s-runtime-sidecar.service.ts:215-218` — same idea.
 *
 * What we do on load:
 *   1. Pull token + user out of `location.hash`.
 *   2. Stash the gateway token in memory; all subsequent fetch / WS
 *      calls inject it as `Authorization: Bearer <token>`.
 *   3. Drive zustand's configStore.login() with the supplied user_id so
 *      the rest of the SPA treats this as a fully-logged-in session.
 *   4. Scrub the fragment from the URL via history.replaceState — so a
 *      page refresh / shared screenshot doesn't keep the secret.
 *   5. Tell `api` how to fetch the bearer for outbound requests. (Done
 *      via setManyfoldBearerProvider; api.ts wires this into its
 *      request interceptor.)
 *
 * Idempotent: if there's no fragment we no-op, and the existing local-
 * mode X-User-Id flow continues to work.
 */

import { useConfigStore } from '@/stores/configStore'
import { useRuntimeStore } from '@/stores/runtimeStore'

let inMemoryGatewayToken: string | null = null

/** Read the bearer token captured from the URL fragment, if any. */
export const getManyfoldBearerToken = (): string | null =>
    inMemoryGatewayToken

/**
 * Parse the URL fragment once on app boot. Returns true iff the
 * fragment carried a Manyfold handoff and we successfully logged in.
 *
 * Note on timing: this function runs in main.tsx BEFORE the React tree
 * mounts, so we set both the configStore (auth) and the runtimeStore
 * (deployment mode = 'local'). Without the mode write, the App's route
 * guard would see mode=null and redirect to /mode-select instead of
 * honouring our just-set login.
 */
export const initManyfoldFragmentAuth = (): boolean => {
    if (typeof window === 'undefined') return false
    const hash = window.location.hash || ''
    if (!hash || hash.length < 2) return false

    // The hash starts with '#'. Strip and treat the rest as
    // application/x-www-form-urlencoded (the standard fragment params
    // shape — both '?' and '&' separators).
    const raw = hash.startsWith('#') ? hash.slice(1) : hash
    const params = new URLSearchParams(raw.replace(/^\?/, ''))
    const token = params.get('token')?.trim() ?? ''
    const userId = params.get('user')?.trim() ?? ''
    const agentId = params.get('agent')?.trim() ?? ''

    if (!token && !userId && !agentId) return false

    if (token) {
        inMemoryGatewayToken = token
    }

    if (userId) {
        // The Manyfold handoff is authoritative — if a prior tab left
        // localStorage saying "logged in as bin" we MUST clear that
        // state before login() so the new identity actually sticks.
        // (Without the explicit logout zustand persist's setItem fires
        // for each set() in order; the visible end-state is then the
        // last write, which is correct in steady state — but downstream
        // listeners observe a transient `userId=bin` between
        // hydration and our overwrite, and some of them race-fetch
        // /api/auth/agents under bin before they see the switch.)
        useConfigStore.getState().logout()
        useConfigStore.getState().login(userId, token || undefined)
    }

    if (agentId) {
        // Set this AFTER login() so the configStore.login() path doesn't
        // wipe our just-set agent selection. The chat layout reads
        // configStore.agentId to decide which agent's transcript /
        // sidebar / config to render.
        useConfigStore.getState().setAgentId(agentId)
    }

    // Manyfold-driven sessions are functionally "local mode" from the
    // NarraNexus frontend's POV (no Clerk, no JWT, identity comes from
    // X-User-Id header). Set the mode so the App's `<Navigate
    // to="/mode-select">` guard doesn't bounce us out of the auto-login
    // we just performed.
    useRuntimeStore.getState().setMode('local')

    // Scrub the fragment so refreshes don't keep the secret visible.
    // Preserve the path + query, drop the hash.
    try {
        const cleanUrl =
            window.location.pathname + window.location.search
        window.history.replaceState(null, '', cleanUrl)
    } catch {
        // history.replaceState can throw in sandboxed iframes — non-fatal.
    }

    // Diagnostic — drops in production-friendly format so we can
    // verify in DevTools that the fragment really fired.
    // eslint-disable-next-line no-console
    console.info(
        `[manyfold-fragment-auth] logged in as ${userId} ` +
            `(token present: ${Boolean(token)}, mode=local` +
            (agentId ? `, agent=${agentId}` : '') +
            `)`
    )

    return Boolean(token || userId || agentId)
}

/**
 * Re-run fragment auth on `hashchange`.
 *
 * Browsers do NOT reload the page when only the URL fragment changes,
 * which means a Manyfold handoff URL pasted into the address bar of an
 * already-open NarraNexus tab would not fire `initManyfoldFragmentAuth`
 * again. Listen for hashchange so the auto-login still works in that
 * scenario.
 *
 * Idempotent: calling twice attaches one listener (we track installed
 * flag) so HMR / StrictMode double-init doesn't double-trigger.
 */
let hashListenerInstalled = false
export const installManyfoldFragmentHashListener = (): void => {
    if (typeof window === 'undefined') return
    if (hashListenerInstalled) return
    hashListenerInstalled = true
    window.addEventListener('hashchange', () => {
        initManyfoldFragmentAuth()
    })
}
