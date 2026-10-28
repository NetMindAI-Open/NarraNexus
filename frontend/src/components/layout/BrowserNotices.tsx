/**
 * @file_name: BrowserNotices.tsx
 * @date: 2026-09-22
 * @description: Surface the agent's open browser login / verification requests wherever the user actually is.
 *
 * Mounted in the app shell, deliberately. A request asked from inside the
 * browser panel would only be seen when that panel happens to be open, while
 * the agent tells the user about it in *chat* — the message names a
 * destination that lives somewhere narrower than the message travels. So this
 * follows whichever agent the user is talking to and appears above
 * everything: an agent blocked on a login it cannot complete itself otherwise
 * looks hung.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';

import { api, ApiError } from '@/lib/api';
import { useChatStore } from '@/stores';
import type { BrowserLoginRequest } from '@/types/browser';
import BrowserLoginNotice from './BrowserLoginNotice';
import { Button } from '@/components/nm/button';
import { PANELS, useRegistryEntries } from '@/platform/registries';
import { bootEverSettled, subscribePluginsBoot } from '@/platform/loader';

/** How often to ask. Requests are raised by an agent turn, not by this tab. */
const POLL_MS = 2000;

export function BrowserNotices() {
  const agentId = useChatStore((s) => s.activeAgentId);
  const browserAvailable = useRegistryEntries(PANELS).some((entry) => entry.id === 'browser');
  // The Browser panel is registered unconditionally at import; only the factory listing
  // (`loadPlugins`) removes it when the running distribution excludes the feature. Until that
  // pass has settled the panel's presence proves nothing (and a LATER pass, e.g. the login flip, must
  // not unmount a live notice, hence "ever settled" rather than "settled now"), and polling now would hit a route
  // the backend never mounted (404 -> error banner).
  const pluginsSettled = useSyncExternalStore(subscribePluginsBoot, bootEverSettled, bootEverSettled);
  // A different agent owns different requests and errors.
  return agentId && browserAvailable && pluginsSettled ? <AgentBrowserNotices key={agentId} agentId={agentId} /> : null;
}

function AgentBrowserNotices({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState<BrowserLoginRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    // 404 = this server never mounted /api/browser (a distribution that excludes the feature).
    let featureAbsent = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      setChecking(true);
      try {
        const res = await api.getBrowserNotices(agentId);
        if (!active) return;
        setPending(res.pending.filter((request) => request.agent_id === agentId));
        setError(null);
      } catch (e) {
        if (!active) return;
        setPending([]);
        // Decided on the HTTP status, never on message text. Only 404 is read as "feature absent":
        // 5xx / network errors stay a visible, retried failure. This is the safety net for the case
        // the factory listing could not be read (5xx, timeout, expired token) and so never removed
        // the Browser panel; the listing remains the primary signal.
        if (e instanceof ApiError && e.status === 404) {
          featureAbsent = true;
          setError(null);
        } else {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (active) {
          setChecking(false);
          // Schedule only after settlement, so slow polls cannot overlap.
          if (!featureAbsent) timer = setTimeout(() => { void load(); }, POLL_MS);
        }
      }
    };
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [agentId, retry]);

  if (pending.length === 0 && error === null) return null;

  return (
    <div data-testid="browser-notices">
      {error !== null && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--nm-hairline)] p-3">
          <p role="alert" className="min-w-0 break-words text-xs text-[var(--color-error)]">
            {t('browser.notices.loadFailed', 'Could not check for browser login requests.')} {error}
          </p>
          <Button variant="secondary" size="sm" loading={checking}
            leading={<RefreshCw className="h-3.5 w-3.5" />}
            onClick={() => setRetry((n) => n + 1)}>
            {t('browser.notices.retry', 'Try again')}
          </Button>
        </div>
      )}
      {pending.map((request) => <BrowserLoginNotice key={request.id} request={request} />)}
    </div>
  );
}

export default BrowserNotices;
