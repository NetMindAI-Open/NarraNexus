/**
 * @file_name: useAutoRefresh.ts
 * @author: Bin Liang
 * @date: 2026-03-04
 * @description: Smart auto-refresh hook for background data polling
 *
 * Features:
 * 1. Tiered polling — high-freq (10s): agentInbox, mid-freq (30s): jobs/ragFiles/awareness/socialNetwork
 * 2. Background message detection (15s): polls chat history to detect new messages from jobs/lark
 * 3. Visibility API — pauses all polling when the tab is hidden, refreshes immediately on re-focus
 * 4. Exposes refreshAll() for full data reload after agent execution completes
 *
 * Design:
 * - Zero requests while the tab is hidden
 * - Uses setInterval for simplicity (no recursive setTimeout)
 * - Pure scheduling layer — does not modify the preloadStore interface
 */

import { useEffect, useCallback, useRef } from 'react';
import { usePreloadStore, useChatStore, useConfigStore } from '@/stores';
import { api } from '@/lib/api';

// ── Polling interval config ─────────────────────

/** High-frequency polling: Agent Inbox */
const HIGH_FREQ_INTERVAL = 10_000; // 10s

/** Mid-frequency polling: Jobs / RAG Files / Awareness */
const MID_FREQ_INTERVAL = 30_000; // 30s

/** Background message check interval */
const BG_MESSAGE_INTERVAL = 15_000; // 15s

// ── Hook ────────────────────────────────────────

interface UseAutoRefreshOptions {
  agentId: string;
  userId: string;
}

/**
 * Smart auto-refresh hook
 *
 * Returns refreshAll() for callers to trigger a full data reload
 * (e.g. after agent execution completes).
 */
export function useAutoRefresh({ agentId, userId }: UseAutoRefreshOptions) {
  const {
    refreshAgentInbox,
    refreshJobs,
    refreshRAGFiles,
    refreshAwareness,
    refreshChatHistory,
    refreshSocialNetwork,
  } = usePreloadStore();

  // Keep latest ids in refs so interval callbacks never capture stale values
  const agentIdRef = useRef(agentId);
  const userIdRef = useRef(userId);
  agentIdRef.current = agentId;
  userIdRef.current = userId;

  // Track the latest known chat history timestamp per agent for new-message detection
  const latestTimestampRef = useRef<Record<string, string>>({});

  // ── Full refresh (call after agent execution, NOT silent — user sees loading) ──

  const refreshAll = useCallback(async () => {
    const aid = agentIdRef.current;
    const uid = userIdRef.current;
    if (!aid || !uid) return;

    await Promise.allSettled([
      refreshAgentInbox(aid),
      refreshJobs(aid),
      refreshRAGFiles(aid, uid),
      refreshAwareness(aid),
      refreshChatHistory(aid, uid),
      refreshSocialNetwork(aid),
    ]);
  }, [refreshAgentInbox, refreshJobs, refreshRAGFiles, refreshAwareness, refreshChatHistory, refreshSocialNetwork]);

  // ── Polling scheduler (all polls are silent) ──

  useEffect(() => {
    if (!agentId || !userId) return;

    // High-freq tick: agentInbox (silent — no loading flicker, no re-render if unchanged)
    const tickHigh = () => {
      if (document.hidden) return;
      const aid = agentIdRef.current;
      if (!aid) return;
      refreshAgentInbox(aid, true);
    };

    // Mid-freq tick: jobs + ragFiles + awareness + agent list (silent)
    const tickMid = () => {
      if (document.hidden) return;
      const aid = agentIdRef.current;
      const uid = userIdRef.current;
      if (!aid || !uid) return;
      refreshJobs(aid, undefined, undefined, true);
      refreshRAGFiles(aid, uid, true);
      refreshAwareness(aid, true);
      refreshSocialNetwork(aid, true);
      // Refresh agent list so newly created agents appear
      useConfigStore.getState().refreshAgents();
    };

    // Background message detection: check all agents for new chat messages
    const tickBgMessages = async () => {
      if (document.hidden) return;
      const uid = userIdRef.current;
      const activeAid = agentIdRef.current;
      if (!uid) return;

      const agents = useConfigStore.getState().agents;
      const { isAgentStreaming } = useChatStore.getState();

      for (const agent of agents) {
        const aid = agent.agent_id;
        // Skip if this agent is currently streaming (live session in progress)
        if (isAgentStreaming(aid)) continue;

        try {
          const response = await api.getSimpleChatHistory(aid, uid, 5);
          if (!response.success || response.messages.length === 0) continue;

          const latestMsg = response.messages[response.messages.length - 1];
          const latestTs = latestMsg.timestamp || '';
          const knownTs = latestTimestampRef.current[aid] || '';

          if (!knownTs) {
            // First check — just record the timestamp, don't notify
            latestTimestampRef.current[aid] = latestTs;
            continue;
          }

          if (latestTs > knownTs) {
            latestTimestampRef.current[aid] = latestTs;

            if (aid !== activeAid) {
              // Non-active agent has new messages → toast + badge
              const chatStore = useChatStore.getState();
              if (!chatStore.completedAgentIds.includes(aid)) {
                useChatStore.setState((state) => ({
                  completedAgentIds: [...state.completedAgentIds, aid],
                  toastQueue: [...state.toastQueue, {
                    agentId: aid,
                    agentName: agent.name || aid,
                    timestamp: Date.now(),
                  }],
                }));
              }
            }
            // Active agent — ChatPanel's own polling will pick up the new messages
          }
        } catch {
          // Silently ignore per-agent polling errors
        }
      }
    };

    const highTimer = setInterval(tickHigh, HIGH_FREQ_INTERVAL);
    const midTimer = setInterval(tickMid, MID_FREQ_INTERVAL);
    const bgMsgTimer = setInterval(tickBgMessages, BG_MESSAGE_INTERVAL);

    // Refresh immediately when tab becomes visible again (also silent)
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        tickHigh();
        tickMid();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(highTimer);
      clearInterval(midTimer);
      clearInterval(bgMsgTimer);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [agentId, userId, refreshAgentInbox, refreshJobs, refreshRAGFiles, refreshAwareness, refreshSocialNetwork]);

  return { refreshAll };
}
