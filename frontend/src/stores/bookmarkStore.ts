/**
 * @file_name: bookmarkStore.ts
 * @author:
 * @date: 2026-06-10
 * @description: Per-agent bookmark state — highlights, sub-bookmarks, badges.
 *
 * Two-layer model (spec §5.2):
 *   - Highlight layer: ephemeral "this run touched X", reset on NEW run start.
 *   - Badge layer: persistent "you have pending work", survives run resets.
 *     Badge-backed sub-bookmarks (failed jobs, inbox unread) are exempt from
 *     the per-run clear — they stay until the user acts.
 *
 * Highlight tiers: 'attention' (requires action) | 'info' (FYI only).
 * Running jobs emit 'running' status (spinner, not a highlight tier).
 *
 * Key: sub-bookmark keys follow the pattern "job:<jobId>" for jobs and
 * "inbox" for agent inbox.  Profile/awareness keys use "profile:<section>".
 */

import { create } from 'zustand';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type HighlightTier = 'attention' | 'info';
export type SubBookmarkStatus = 'running' | 'attention' | 'info';

export interface SubBookmark {
  key: string;
  label: string;
  status: SubBookmarkStatus;
  /** True when this sub-bookmark is kept by a persistent badge. */
  isBadgeBacked: boolean;
}

export interface AgentBookmarkState {
  /** Map from sub-bookmark key to highlight tier.  Running items have NO
   *  entry here — running is a spinner status, not a highlight (spec §5.3). */
  highlights: Record<string, HighlightTier>;
  subBookmarks: SubBookmark[];
  badges: {
    failedJobs: number;
    inboxUnread: number;
  };
}

interface BookmarkStoreState {
  agents: Record<string, AgentBookmarkState>;

  /** Call when a NEW agent run starts (run_started frame).
   *  Resets info highlights and non-badge-backed sub-bookmarks.
   *  Badge-backed items (failed jobs, inbox) survive. */
  onRunStart: (agentId: string) => void;

  /** A job entered the running state — adds a running sub-bookmark. */
  noteJobRunning: (agentId: string, jobId: string, label: string) => void;

  /** A job completed successfully — upserts with 'info' status + highlight. */
  noteJobCompleted: (agentId: string, jobId: string, label: string) => void;

  /** A job failed or requires attention — upserts with 'attention' + badge. */
  noteJobFailed: (agentId: string, jobId: string, label: string) => void;

  /** Inbox unread count changed.  count === 0 clears the badge + sub-bookmark. */
  noteInboxUnread: (agentId: string, count: number) => void;

  /** Awareness / profile section updated externally — emits an 'info' sub-bookmark. */
  noteProfileUpdate: (agentId: string, section: string) => void;

  /** User has resolved (viewed / cancelled / resumed) a failed job.
   *  Removes its highlight and sub-bookmark and decrements failedJobs badge. */
  resolveJobAttention: (agentId: string, jobId: string) => void;

  /** User opened the drawer at a specific sub-bookmark key.
   *  Clears only 'info' highlights — 'attention' requires explicit resolution. */
  markOpened: (agentId: string, key: string) => void;

  /** Remove all state for one agent (e.g. agent was deleted). */
  clearAgent: (agentId: string) => void;

  /** Reset entire store (used in tests / logout). */
  clearAll: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEmptyAgentState(): AgentBookmarkState {
  return {
    highlights: {},
    subBookmarks: [],
    badges: { failedJobs: 0, inboxUnread: 0 },
  };
}

function getOrCreate(
  agents: Record<string, AgentBookmarkState>,
  agentId: string,
): AgentBookmarkState {
  return agents[agentId] ?? makeEmptyAgentState();
}

/** Return a copy of the highlight map without `key`. */
function omitHighlight(
  highlights: Record<string, HighlightTier>,
  key: string,
): Record<string, HighlightTier> {
  return Object.fromEntries(
    Object.entries(highlights).filter(([k]) => k !== key),
  );
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useBookmarkStore = create<BookmarkStoreState>((set) => ({
  agents: {},

  onRunStart: (agentId) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);

      // Keep only badge-backed sub-bookmarks.
      const survivingSubs = agent.subBookmarks.filter((s) => s.isBadgeBacked);

      // Rebuild highlights: keep only those whose sub-bookmark survives.
      const survivingKeys = new Set(survivingSubs.map((s) => s.key));
      const newHighlights: Record<string, HighlightTier> = {};
      for (const [key, tier] of Object.entries(agent.highlights)) {
        if (survivingKeys.has(key)) {
          newHighlights[key] = tier;
        }
      }

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            highlights: newHighlights,
            subBookmarks: survivingSubs,
          },
        },
      };
    });
  },

  noteJobRunning: (agentId, jobId, label) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const key = `job:${jobId}`;

      // Upsert — replace existing entry or append.
      const existingIdx = agent.subBookmarks.findIndex((s) => s.key === key);
      const newSub: SubBookmark = { key, label, status: 'running', isBadgeBacked: false };
      const subs =
        existingIdx >= 0
          ? agent.subBookmarks.map((s, i) => (i === existingIdx ? newSub : s))
          : [...agent.subBookmarks, newSub];

      return {
        agents: {
          ...state.agents,
          [agentId]: { ...agent, subBookmarks: subs },
        },
      };
    });
  },

  noteJobCompleted: (agentId, jobId, label) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const key = `job:${jobId}`;

      const existingIdx = agent.subBookmarks.findIndex((s) => s.key === key);
      const newSub: SubBookmark = { key, label, status: 'info', isBadgeBacked: false };
      const subs =
        existingIdx >= 0
          ? agent.subBookmarks.map((s, i) => (i === existingIdx ? newSub : s))
          : [...agent.subBookmarks, newSub];

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            subBookmarks: subs,
            highlights: { ...agent.highlights, [key]: 'info' },
          },
        },
      };
    });
  },

  noteJobFailed: (agentId, jobId, label) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const key = `job:${jobId}`;

      const alreadyFailed = agent.subBookmarks.some(
        (s) => s.key === key && s.status === 'attention' && s.isBadgeBacked,
      );

      const existingIdx = agent.subBookmarks.findIndex((s) => s.key === key);
      const newSub: SubBookmark = { key, label, status: 'attention', isBadgeBacked: true };
      const subs =
        existingIdx >= 0
          ? agent.subBookmarks.map((s, i) => (i === existingIdx ? newSub : s))
          : [...agent.subBookmarks, newSub];

      // failedJobs badge counts distinct failed keys — don't double-count.
      const newFailedCount = alreadyFailed
        ? agent.badges.failedJobs
        : agent.badges.failedJobs + 1;

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            subBookmarks: subs,
            highlights: { ...agent.highlights, [key]: 'attention' },
            badges: { ...agent.badges, failedJobs: newFailedCount },
          },
        },
      };
    });
  },

  noteInboxUnread: (agentId, count) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);

      if (count === 0) {
        // Clear badge, highlight and sub-bookmark.
        const remainingHighlights = omitHighlight(agent.highlights, 'inbox');
        const subs = agent.subBookmarks.filter((s) => s.key !== 'inbox');
        return {
          agents: {
            ...state.agents,
            [agentId]: {
              ...agent,
              highlights: remainingHighlights,
              subBookmarks: subs,
              badges: { ...agent.badges, inboxUnread: 0 },
            },
          },
        };
      }

      const existingIdx = agent.subBookmarks.findIndex((s) => s.key === 'inbox');
      const newSub: SubBookmark = {
        key: 'inbox',
        label: `Inbox (${count})`,
        status: 'attention',
        isBadgeBacked: true,
      };
      const subs =
        existingIdx >= 0
          ? agent.subBookmarks.map((s, i) => (i === existingIdx ? newSub : s))
          : [...agent.subBookmarks, newSub];

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            subBookmarks: subs,
            highlights: { ...agent.highlights, inbox: 'attention' },
            badges: { ...agent.badges, inboxUnread: count },
          },
        },
      };
    });
  },

  noteProfileUpdate: (agentId, section) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const key = `profile:${section}`;

      const existingIdx = agent.subBookmarks.findIndex((s) => s.key === key);
      const newSub: SubBookmark = { key, label: section, status: 'info', isBadgeBacked: false };
      const subs =
        existingIdx >= 0
          ? agent.subBookmarks.map((s, i) => (i === existingIdx ? newSub : s))
          : [...agent.subBookmarks, newSub];

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            subBookmarks: subs,
            highlights: { ...agent.highlights, [key]: 'info' },
          },
        },
      };
    });
  },

  resolveJobAttention: (agentId, jobId) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const key = `job:${jobId}`;

      const wasFailedBadge = agent.subBookmarks.some(
        (s) => s.key === key && s.isBadgeBacked,
      );

      const remainingHighlights = omitHighlight(agent.highlights, key);
      const subs = agent.subBookmarks.filter((s) => s.key !== key);
      const newFailedCount = wasFailedBadge
        ? Math.max(0, agent.badges.failedJobs - 1)
        : agent.badges.failedJobs;

      return {
        agents: {
          ...state.agents,
          [agentId]: {
            ...agent,
            highlights: remainingHighlights,
            subBookmarks: subs,
            badges: { ...agent.badges, failedJobs: newFailedCount },
          },
        },
      };
    });
  },

  markOpened: (agentId, key) => {
    set((state) => {
      const agent = getOrCreate(state.agents, agentId);
      const tier = agent.highlights[key];

      // Only clear 'info' highlights — 'attention' requires explicit resolution.
      if (tier !== 'info') return state;

      const remainingHighlights = omitHighlight(agent.highlights, key);
      return {
        agents: {
          ...state.agents,
          [agentId]: { ...agent, highlights: remainingHighlights },
        },
      };
    });
  },

  clearAgent: (agentId) => {
    set((state) => {
      const newAgents = { ...state.agents };
      delete newAgents[agentId];
      return { agents: newAgents };
    });
  },

  clearAll: () => {
    set({ agents: {} });
  },
}));
