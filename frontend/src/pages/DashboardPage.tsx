/**
 * @file_name: DashboardPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-13
 * @description: Agent Dashboard v2 main page.
 *
 * Polling FSM driven by dashboardStore (visibility × tauri focus × any_running).
 * Paired with setTrayBadge for Tauri; web mode no-op. Handles 429 with
 * exponential backoff (store.onRateLimited).
 */
import { useEffect, useState } from 'react';
import { useDashboardStore } from '@/stores/dashboardStore';
import { api } from '@/lib/api';
import { setTrayBadge, listenTauri } from '@/lib/tauri';
import { AgentCard } from '@/components/dashboard/AgentCard';
import { DashboardSummary } from '@/components/dashboard/DashboardSummary';

export function DashboardPage() {
  const agents = useDashboardStore((s) => s.agents);
  const error = useDashboardStore((s) => s.error);
  const setVisibility = useDashboardStore((s) => s.setVisibility);
  const setTauriFocused = useDashboardStore((s) => s.setTauriFocused);
  const onFetchSuccess = useDashboardStore((s) => s.onFetchSuccess);
  const onFetchError = useDashboardStore((s) => s.onFetchError);
  const onRateLimited = useDashboardStore((s) => s.onRateLimited);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // --- Visibility API ---
  useEffect(() => {
    const onVis = () => setVisibility(!document.hidden);
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [setVisibility]);

  // --- Tauri focus/blur ---
  useEffect(() => {
    let unlistenBlur: (() => void) | null = null;
    let unlistenFocus: (() => void) | null = null;
    (async () => {
      unlistenBlur = await listenTauri('tauri://blur', () => setTauriFocused(false));
      unlistenFocus = await listenTauri('tauri://focus', () => setTauriFocused(true));
    })();
    return () => {
      unlistenBlur?.();
      unlistenFocus?.();
    };
  }, [setTauriFocused]);

  // --- Polling loop ---
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const res = await api.getDashboardStatus();
        if (!active) return;
        if (!res.success) throw new Error(res.error ?? 'Unknown error');
        onFetchSuccess(res.agents);
        const state = useDashboardStore.getState();
        const running = state.computeRunningCount();
        if (running !== state.lastTrayCount) {
          await setTrayBadge(running);
          useDashboardStore.setState({ lastTrayCount: running });
        }
      } catch (e: unknown) {
        const status = (e as { status?: number })?.status;
        if (status === 429) {
          onRateLimited();
        } else {
          const msg = e instanceof Error ? e.message : String(e);
          onFetchError(msg);
        }
      } finally {
        const interval = useDashboardStore.getState().computeInterval();
        if (active && interval !== Infinity) {
          timer = setTimeout(tick, interval);
        }
      }
    }

    tick();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [onFetchSuccess, onFetchError, onRateLimited]);

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-xl font-semibold">Agent Dashboard</h1>
      {error && (
        <div className="p-3 border border-[var(--color-red-500)] text-sm">
          {error}
        </div>
      )}
      {agents.length === 0 && !error && (
        <div className="p-8 text-center text-[var(--text-secondary)] text-sm">
          No agents yet.
        </div>
      )}
      {agents.length > 0 && <DashboardSummary agents={agents} />}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {agents.map((a) => (
          <div key={a.agent_id}>
            <AgentCard
              agent={a}
              expanded={expandedId === a.agent_id}
              onToggleExpand={() =>
                setExpandedId(expandedId === a.agent_id ? null : a.agent_id)
              }
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export default DashboardPage;
