/**
 * Arena landing flow.
 *
 * When a user arrives from arena42.ai (URL carries `source=arena`, stashed in
 * sessionStorage by takeInboundToken), once they are logged in we call the
 * idempotent backend provision endpoint, then immediately refresh the left
 * agent panel and open the freshly-provisioned Arena agent's chat.
 *
 * Safe to call multiple times: an in-flight guard + a per-user "done" marker
 * prevent duplicate work, and the backend is idempotent regardless.
 */
import { api } from './api';
import { useConfigStore } from '../stores/configStore';
import { useChatStore } from '../stores/chatStore';
import { useArenaLandingStore } from '../stores/arenaLandingStore';

const ENTRY_KEY = 'nx-entry-source';
const DONE_KEY = 'nx-arena-provisioned';
const ARENA = 'arena';

let inFlight = false;

export function isArenaEntry(): boolean {
  try {
    if (sessionStorage.getItem(ENTRY_KEY) === ARENA) return true;
  } catch {
    /* sessionStorage may be unavailable */
  }
  try {
    return new URLSearchParams(window.location.search).get('source') === ARENA;
  } catch {
    return false;
  }
}

/**
 * Provision (or reuse) this user's Arena agent and open it. No-op unless the
 * entry source is Arena and the user is logged in.
 */
export async function runArenaLandingIfNeeded(): Promise<void> {
  if (inFlight) return;
  if (!isArenaEntry()) return;

  const cfg = useConfigStore.getState();
  if (!cfg.isLoggedIn || !cfg.userId) return; // wait until login completes

  try {
    if (sessionStorage.getItem(DONE_KEY) === cfg.userId) return; // already done
  } catch {
    /* ignore */
  }

  inFlight = true;
  useArenaLandingStore.getState().setProvisioning();

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  // The API client reads auth (X-User-Id / Bearer) from persisted storage,
  // which can lag the store by a tick right after login. Firing before it's
  // written sends an unauthenticated request → 401.
  const authReady = () => {
    try {
      const st = JSON.parse(localStorage.getItem('narra-nexus-config') || '{}')?.state;
      return st?.userId === cfg.userId || !!st?.token;
    } catch {
      return false;
    }
  };

  try {
    for (let i = 0; i < 20 && !authReady(); i++) await sleep(100); // wait ≤2s

    // Retry a few times: absorbs the auth-ready race tail and a transient Arena
    // hiccup instead of stranding the user on the error state.
    let res: Awaited<ReturnType<typeof api.provisionArena>> | null = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        res = await api.provisionArena();
        if (res && res.success && res.agent_id) break;
      } catch (e) {
        console.warn('[arena] provision attempt failed, retrying', e);
      }
      await sleep(600);
    }

    if (res && res.success && res.agent_id) {
      // Fast left-panel refresh: reload the list, then select + open the agent.
      await useConfigStore.getState().refreshAgents();
      useConfigStore.getState().setAgentId(res.agent_id);
      try {
        useChatStore.getState().setActiveAgent(res.agent_id);
      } catch {
        /* chat session wiring is optional for selection */
      }
      try {
        sessionStorage.setItem(DONE_KEY, cfg.userId);
        sessionStorage.removeItem(ENTRY_KEY);
      } catch {
        /* ignore */
      }
      useArenaLandingStore.getState().setReady(res.arena_name);
    } else {
      console.error('[arena] provisioning failed after retries', res);
      useArenaLandingStore.getState().setError('provision failed');
    }
  } finally {
    inFlight = false;
  }
}
