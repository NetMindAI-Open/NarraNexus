/**
 * @file_name: useBookmarkSignals.ts
 * @author:
 * @date: 2026-06-10
 * @description: Watches the existing data stores and feeds bookmarkStore.
 *
 * The bookmark strip is ambient UI — it must not own any data fetching.
 * This hook observes what preloadStore / configStore already know (jobs,
 * agent-inbox unread count, awareness external updates) and translates
 * CHANGES into bookmark notes. Mounted once by MainLayout's ChatView.
 *
 * Job semantics:
 *   - running/active            → noteJobRunning (spinner sub-bookmark)
 *   - failed / blocked_failed   → noteJobFailed (attention + badge; also
 *                                 on first load — an unresolved failure is
 *                                 actionable backlog, not stale news)
 *   - completed                 → noteJobCompleted ONLY on an observed
 *                                 in-session transition; historical
 *                                 completions on first load would spam
 *                                 info highlights with stale news
 *   - left failed state without user action (e.g. auto-retry succeeded,
 *     external resume)          → resolveJobAttention
 */

import { useEffect, useRef } from 'react';
import { usePreloadStore, useConfigStore, useBookmarkStore } from '@/stores';

const RUNNING_STATUSES = new Set(['running', 'active']);
const FAILED_STATUSES = new Set(['failed', 'blocked_failed']);

export function useBookmarkSignals(agentId: string | null): void {
  const jobs = usePreloadStore((s) => s.jobs);
  const inboxUnread = usePreloadStore((s) => s.agentInboxUnreadCount);
  const awarenessUpdatedAgents = useConfigStore((s) => s.awarenessUpdatedAgents);

  // Previous job-status snapshot for the CURRENT agent. preloadStore's
  // jobs are already scoped to the selected agent, so reset on switch.
  const prevStatusesRef = useRef<Map<string, string>>(new Map());
  const prevAgentRef = useRef<string | null>(null);

  useEffect(() => {
    if (!agentId) return;
    const store = useBookmarkStore.getState();

    const isAgentSwitch = prevAgentRef.current !== agentId;
    if (isAgentSwitch) {
      prevStatusesRef.current = new Map();
      prevAgentRef.current = agentId;
    }
    const prev = prevStatusesRef.current;
    const next = new Map<string, string>();

    for (const job of jobs) {
      const id = job.job_id;
      const status = job.status;
      next.set(id, status);
      const prevStatus = prev.get(id);
      if (status === prevStatus) continue;

      if (RUNNING_STATUSES.has(status)) {
        store.noteJobRunning(agentId, id, job.title || id);
      } else if (FAILED_STATUSES.has(status)) {
        store.noteJobFailed(agentId, id, job.title || id);
      } else if (status === 'completed') {
        // Only an observed transition counts as news.
        if (prevStatus !== undefined && prevStatus !== 'completed') {
          store.noteJobCompleted(agentId, id, job.title || id);
        }
      }

      // Job left the failed state without going through the panel's
      // cancel/resume buttons (auto-retry, external action) — the
      // attention badge no longer reflects reality.
      if (
        prevStatus !== undefined &&
        FAILED_STATUSES.has(prevStatus) &&
        !FAILED_STATUSES.has(status)
      ) {
        store.resolveJobAttention(agentId, id);
      }
    }

    prevStatusesRef.current = next;
  }, [agentId, jobs]);

  useEffect(() => {
    if (!agentId) return;
    useBookmarkStore.getState().noteInboxUnread(agentId, inboxUnread);
  }, [agentId, inboxUnread]);

  useEffect(() => {
    if (!agentId) return;
    if (awarenessUpdatedAgents.includes(agentId)) {
      useBookmarkStore.getState().noteProfileUpdate(agentId, 'awareness');
    }
  }, [agentId, awarenessUpdatedAgents]);
}
