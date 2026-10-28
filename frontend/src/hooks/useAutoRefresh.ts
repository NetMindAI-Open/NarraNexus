/**
 * @file_name: useAutoRefresh.ts
 * @author: Bin Liang
 * @date: 2026-03-04
 * @description: Smart auto-refresh hook for background data polling
 *
 * Features:
 * 1. Tiered polling — high-freq (10s): agentInbox, mid-freq (30s): teams/jobs/awareness/socialNetwork
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
import { usePreloadStore, useChatStore, useConfigStore, useArtifactStore, useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { isGuideCoachmarkPending } from '@/lib/guideCoachmark';
import { teamHasUnread } from '@/lib/unread';
import type { ToastItem } from '@/stores/chatStore';

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
    refreshAwareness,
    refreshChatHistory,
    refreshSocialNetwork,
  } = usePreloadStore();

  // Artifacts are NOT polled on a timer (they're event-driven — see the
  // mirror md). loadPinned is wired into refreshAll only, so a finished
  // agent run reliably surfaces any artifact it created even if the
  // mid-stream tool_output discovery path missed it.
  const loadPinnedArtifacts = useArtifactStore((s) => s.loadPinned);

  // Keep latest ids in refs so interval callbacks never capture stale values
  const agentIdRef = useRef(agentId);
  const userIdRef = useRef(userId);
  agentIdRef.current = agentId;
  userIdRef.current = userId;

  // Track the latest known chat history timestamp per agent for new-message detection
  const latestTimestampRef = useRef<Record<string, string>>({});

  // Was each team room unread at the previous tick? An absent entry means
  // "never observed", which is deliberately distinct from `false` — see
  // notifyWokenRooms.
  const roomUnreadRef = useRef<Record<string, boolean>>({});

  // ── Full refresh (call after agent execution, NOT silent — user sees loading) ──

  const refreshAll = useCallback(async () => {
    const aid = agentIdRef.current;
    const uid = userIdRef.current;
    if (!aid || !uid) return;

    await Promise.allSettled([
      refreshAgentInbox(aid),
      refreshJobs(aid),
      refreshAwareness(aid),
      refreshChatHistory(aid, uid),
      refreshSocialNetwork(aid),
      loadPinnedArtifacts(aid),
    ]);
  }, [refreshAgentInbox, refreshJobs, refreshAwareness, refreshChatHistory, refreshSocialNetwork, loadPinnedArtifacts]);

  // ── First-login fast poll ──
  // A brand-new user's guide agent is provisioned server-side, fire-and-
  // forget, AFTER the login response — so the login page's one-shot
  // getAgents usually races it and loses, and the 30s tick below is a long
  // time to stare at an empty sidebar next to a coachmark saying "your
  // first agent is already here". While the coachmark is armed and the
  // sidebar is empty, poll every 2s, capped at ~20s; the cap matters
  // because /api/auth/agents is enriched (active-run + last-message
  // preview) and must not be fast-polled indefinitely.
  useEffect(() => {
    if (!userId || !isGuideCoachmarkPending()) return;
    if (useConfigStore.getState().agents.length > 0) return;
    let attempts = 0;
    const id = window.setInterval(() => {
      // Count BEFORE the hidden skip so the ~20s cap is wall-clock time, not
      // foreground time — a backgrounded tab must not keep this interval
      // armed indefinitely (the 30s tick covers late returns).
      attempts += 1;
      if (attempts > 10 || useConfigStore.getState().agents.length > 0) {
        window.clearInterval(id);
        return;
      }
      if (document.hidden) return; // same zero-requests-in-background rule as the ticks
      void useConfigStore.getState().refreshAgents();
    }, 2_000);
    return () => window.clearInterval(id);
  }, [userId]);

  // ── Polling scheduler (all polls are silent) ──

  useEffect(() => {
    // Only userId is required. The polls that need an agent guard on one
    // themselves, and gating the whole scheduler on a selected agent left a
    // user sitting in a team room with no background refresh at all.
    if (!userId) return;

    /**
     * Toast the rooms that just woke up.
     *
     * The trigger is the EDGE — a room the user had caught up on has started
     * talking — not the level. A toast per new message in a room where six
     * agents answer at once is a notification people turn off, and a feature
     * users turn off is worse than one that was never built. Once a room is
     * unread it stays unread until they open it, and says nothing more.
     *
     * "Is the user reading it right now" needs no route knowledge: the open
     * room advances its own watermark every 3s (see TeamChatPanel), so by the
     * time this 30s tick sees a new message it is already read. `teamHasUnread`
     * is the same question the sidebar dot asks, answered from the same place.
     */
    const notifyWokenRooms = () => {
      const teams = useTeamsStore.getState().teams;
      const woken: ToastItem[] = [];
      for (const t of teams) {
        const id = t.team.team_id;
        const nowUnread = teamHasUnread(t.last_message_at, id);
        const before = roomUnreadRef.current[id];
        roomUnreadRef.current[id] = nowUnread;
        // undefined = never observed: a team created, joined, or seen for the
        // first time this session. Treating that as "was caught up" would
        // announce the entire backlog of a room the user just gained access to,
        // and would make every unread room shout on app start.
        if (before === undefined || before || !nowUnread) continue;
        woken.push({
          kind: 'team',
          teamId: id,
          teamName: t.team.name || id,
          timestamp: Date.now(),
        });
      }
      if (!woken.length) return;
      useChatStore.setState((state) => ({ toastQueue: [...state.toastQueue, ...woken] }));
    };

    // High-freq tick: agentInbox (silent — no loading flicker, no re-render if unchanged)
    const tickHigh = () => {
      if (document.hidden) return;
      const aid = agentIdRef.current;
      if (!aid) return;
      refreshAgentInbox(aid, true);
    };

    // Mid-freq tick: teams + jobs + awareness + agent list (silent)
    const tickMid = () => {
      if (document.hidden) return;
      const aid = agentIdRef.current;
      const uid = userIdRef.current;
      if (!uid) return;
      // Teams carry the room-activity mark the sidebar shows for a room the
      // user has left. Without this the mark would only ever appear on a full
      // reload, which is indistinguishable from it not working.
      //
      // Ahead of the agent guard on purpose: a team room needs no agent
      // selected, and the sidebar's team rows exist whether one is or not.
      void useTeamsStore
        .getState()
        .refresh()
        .then(() => notifyWokenRooms());
      // Agent list ahead of the agent guard ON PURPOSE: a user with zero
      // agents has no agentId, and before this moved up their sidebar never
      // refreshed at all — the server-provisioned onboarding guide agent
      // (created fire-and-forget AFTER login returns) only appeared on a
      // manual reload. This costs one /api/auth/agents call per tick for
      // logged-in users with nothing selected; that is intended.
      useConfigStore.getState().refreshAgents();
      if (!aid) return;
      refreshJobs(aid, undefined, undefined, true);
      refreshAwareness(aid, true);
      refreshSocialNetwork(aid, true);
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
          // include='chat' ONLY: the "new message → toast + badge" signal must
          // mean a real reply to the owner, not the agent's peer/team activity
          // (a2a / message_bus). Those live in the activity stream now and would
          // otherwise fire a "replied to you" toast on every peer turn.
          const response = await api.getSimpleChatHistory(aid, 5, 0, 'chat');
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
                    kind: 'agent' as const,
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
  }, [agentId, userId, refreshAgentInbox, refreshJobs, refreshAwareness, refreshSocialNetwork]);

  return { refreshAll };
}
