/**
 * Preload Store - Cache data on page load for faster tab switching
 *
 * Loads all panel data in parallel when the app initializes:
 * - Inbox messages
 * - Jobs
 * - Agent awareness
 * - Social network list (all contacts)
 * - Chat history (narratives + events)
 * - RAG files
 */

import { create } from 'zustand';
import { api } from '@/lib/api';
import type {
  ApiResponse,
  InboxMessage,
  MatrixRoom,
  RoomMessage,
  Job,
  SocialNetworkEntity,
  ChatHistoryEvent,
  ChatHistoryNarrative,
  RAGFileInfo,
  CostSummary,
} from '@/types';

// ────────────────────────────────────────────
// Public interface (fully backward compatible)
// ────────────────────────────────────────────

interface PreloadState {
  // Data
  inbox: InboxMessage[];
  inboxUnreadCount: number;
  inboxTotalCount: number;
  inboxPage: number;
  agentInboxRooms: MatrixRoom[];
  agentInboxUnreadCount: number;
  jobs: Job[];
  awareness: string | null;
  awarenessCreateTime: string | null;
  awarenessUpdateTime: string | null;
  socialNetworkList: SocialNetworkEntity[];
  chatHistoryEvents: ChatHistoryEvent[];
  chatHistoryNarratives: ChatHistoryNarrative[];
  ragFiles: RAGFileInfo[];
  ragCompletedCount: number;
  ragPendingCount: number;
  costSummary: CostSummary | null;

  // Loading states
  inboxLoading: boolean;
  agentInboxLoading: boolean;
  jobsLoading: boolean;
  awarenessLoading: boolean;
  socialNetworkLoading: boolean;
  chatHistoryLoading: boolean;
  ragFilesLoading: boolean;
  costLoading: boolean;

  // Error states
  inboxError: string | null;
  agentInboxError: string | null;
  jobsError: string | null;
  awarenessError: string | null;
  socialNetworkError: string | null;
  chatHistoryError: string | null;
  ragFilesError: string | null;
  costError: string | null;

  // Last loaded params (to detect changes)
  lastUserId: string | null;
  lastAgentId: string | null;

  // Actions (silent=true skips loading state toggle & deduplicates unchanged data)
  preloadAll: (agentId: string, userId: string) => Promise<void>;
  refreshInbox: (userId: string, silent?: boolean) => Promise<void>;
  loadInboxPage: (userId: string, page: number) => Promise<void>;
  refreshAgentInbox: (agentId: string, silent?: boolean) => Promise<void>;
  refreshJobs: (agentId: string, userId?: string, status?: string, silent?: boolean) => Promise<void>;
  refreshAwareness: (agentId: string, silent?: boolean) => Promise<void>;
  refreshSocialNetwork: (agentId: string, silent?: boolean) => Promise<void>;
  refreshChatHistory: (agentId: string, userId: string, silent?: boolean) => Promise<void>;
  refreshRAGFiles: (agentId: string, userId: string, silent?: boolean) => Promise<void>;
  refreshCost: (agentId: string, days?: number, silent?: boolean) => Promise<void>;
  addChatHistoryEvent: (event: ChatHistoryEvent) => void;
  updateInboxMessage: (messageId: string, updates: Partial<InboxMessage>) => void;
  updateAgentInboxMessage: (messageId: string, updates: Partial<RoomMessage>) => void;
  markAllInboxRead: () => void;
  clearAll: () => void;
}

// ────────────────────────────────────────────
// Internal helpers: eliminate duplicate code in refresh / preload
// ────────────────────────────────────────────

type SetFn = (partial: Partial<PreloadState>) => void;
type GetFn = () => PreloadState;

/**
 * Generic "load a domain" logic.
 *
 * When `silent` is true (used by background polling):
 * - Loading state is NOT toggled (no UI flicker)
 * - Data is compared with current state; set() is skipped if unchanged (no wasted re-renders)
 * - Errors are silently swallowed (transient network blips shouldn't disrupt UI)
 */
async function loadDomain<T extends ApiResponse>(
  set: SetFn,
  get: GetFn,
  loadingKey: keyof PreloadState,
  errorKey: keyof PreloadState,
  fetcher: () => Promise<T>,
  onSuccess: (data: T) => Partial<PreloadState>,
  fallbackError: string,
  silent = false,
) {
  if (!silent) {
    set({ [loadingKey]: true, [errorKey]: null } as Partial<PreloadState>);
  }
  try {
    const result = await fetcher();
    if (result.success) {
      const updates = onSuccess(result);
      if (silent) {
        // Skip set() entirely if data hasn't changed
        const current = get();
        const changed = Object.entries(updates).some(
          ([key, val]) => JSON.stringify(current[key as keyof PreloadState]) !== JSON.stringify(val),
        );
        if (!changed) return;
        set(updates as Partial<PreloadState>);
      } else {
        set({ ...updates, [loadingKey]: false } as Partial<PreloadState>);
      }
    } else if (!silent) {
      set({ [loadingKey]: false, [errorKey]: result.error || fallbackError } as Partial<PreloadState>);
    }
  } catch (error) {
    if (!silent) {
      set({ [loadingKey]: false, [errorKey]: String(error) } as Partial<PreloadState>);
    }
  }
}

// ────────────────────────────────────────────
// Store
// ────────────────────────────────────────────

export const usePreloadStore = create<PreloadState>()((set, get) => ({
  // Initial data
  inbox: [],
  inboxUnreadCount: 0,
  inboxTotalCount: 0,
  inboxPage: 1,
  agentInboxRooms: [],
  agentInboxUnreadCount: 0,
  jobs: [],
  awareness: null,
  awarenessCreateTime: null,
  awarenessUpdateTime: null,
  socialNetworkList: [],
  chatHistoryEvents: [],
  chatHistoryNarratives: [],
  ragFiles: [],
  ragCompletedCount: 0,
  ragPendingCount: 0,
  costSummary: null,

  // Initial loading / error
  inboxLoading: false,
  agentInboxLoading: false,
  jobsLoading: false,
  awarenessLoading: false,
  socialNetworkLoading: false,
  chatHistoryLoading: false,
  ragFilesLoading: false,
  costLoading: false,
  inboxError: null,
  agentInboxError: null,
  jobsError: null,
  awarenessError: null,
  socialNetworkError: null,
  chatHistoryError: null,
  ragFilesError: null,
  costError: null,

  lastUserId: null,
  lastAgentId: null,

  // ── Preload all data in parallel ──────────────────

  preloadAll: async (agentId, userId) => {
    const { lastUserId, lastAgentId } = get();
    if (lastUserId === userId && lastAgentId === agentId && get().inbox.length > 0) return;

    set({
      lastUserId: userId,
      lastAgentId: agentId,
      inboxLoading: true, agentInboxLoading: true, jobsLoading: true,
      awarenessLoading: true, socialNetworkLoading: true,
      chatHistoryLoading: true, ragFilesLoading: true, costLoading: true,
      inboxError: null, agentInboxError: null, jobsError: null,
      awarenessError: null, socialNetworkError: null,
      chatHistoryError: null, ragFilesError: null, costError: null,
    });

    // Fire all domains independently — each updates UI as soon as it resolves,
    // so fast APIs (awareness ~2ms) don't wait for slow ones (chat-history ~7MB).
    const tasks = [
      loadDomain(set, get, 'inboxLoading', 'inboxError',
        () => api.getInbox(userId),
        (r) => ({ inbox: r.messages, inboxUnreadCount: r.unread_count, inboxTotalCount: r.total_count, inboxPage: 1 }),
        'Failed to load inbox'),
      loadDomain(set, get, 'agentInboxLoading', 'agentInboxError',
        () => api.getAgentInbox(agentId),
        (r) => ({ agentInboxRooms: r.rooms, agentInboxUnreadCount: r.total_unread }),
        'Failed to load agent inbox'),
      loadDomain(set, get, 'jobsLoading', 'jobsError',
        () => api.getJobs(agentId),
        (r) => ({ jobs: r.jobs }),
        'Failed to load jobs'),
      loadDomain(set, get, 'awarenessLoading', 'awarenessError',
        () => api.getAwareness(agentId),
        (r) => ({ awareness: r.awareness || null, awarenessCreateTime: r.create_time || null, awarenessUpdateTime: r.update_time || null }),
        'Failed to load awareness'),
      loadDomain(set, get, 'socialNetworkLoading', 'socialNetworkError',
        () => api.getSocialNetworkList(agentId),
        (r) => ({ socialNetworkList: r.entities || [] }),
        'No social network data'),
      loadDomain(set, get, 'chatHistoryLoading', 'chatHistoryError',
        () => api.getChatHistory(agentId, userId),
        (r) => ({ chatHistoryEvents: r.events || [], chatHistoryNarratives: r.narratives || [] }),
        'No chat history'),
      loadDomain(set, get, 'ragFilesLoading', 'ragFilesError',
        () => api.listRAGFiles(agentId, userId),
        (r) => ({ ragFiles: r.files || [], ragCompletedCount: r.completed_count || 0, ragPendingCount: r.pending_count || 0 }),
        'Failed to load RAG files'),
      loadDomain(set, get, 'costLoading', 'costError',
        () => api.getCosts(agentId),
        (r) => ({ costSummary: r.summary || null }),
        'Failed to load cost data'),
    ];

    await Promise.allSettled(tasks);
  },

  // ── Individual refresh methods ────────────────────

  refreshInbox: (userId, silent?) => loadDomain(set, get,
    'inboxLoading', 'inboxError',
    () => api.getInbox(userId, undefined, 50, (get().inboxPage - 1) * 50),
    (r) => ({ inbox: r.messages, inboxUnreadCount: r.unread_count, inboxTotalCount: r.total_count }),
    'Failed to load inbox', silent),

  loadInboxPage: async (userId, page) => {
    set({ inboxLoading: true, inboxError: null });
    try {
      const result = await api.getInbox(userId, undefined, 50, (page - 1) * 50);
      if (result.success) {
        set({
          inbox: result.messages,
          inboxUnreadCount: result.unread_count,
          inboxTotalCount: result.total_count,
          inboxPage: page,
          inboxLoading: false,
        });
      } else {
        set({ inboxLoading: false, inboxError: result.error || 'Failed to load inbox' });
      }
    } catch (error) {
      set({ inboxLoading: false, inboxError: String(error) });
    }
  },

  refreshAgentInbox: (agentId, silent?) => loadDomain(set, get,
    'agentInboxLoading', 'agentInboxError',
    () => api.getAgentInbox(agentId),
    (r) => ({ agentInboxRooms: r.rooms, agentInboxUnreadCount: r.total_unread }),
    'Failed to load agent inbox', silent),

  refreshJobs: (agentId, _userId?, status?, silent?) => loadDomain(set, get,
    'jobsLoading', 'jobsError',
    () => api.getJobs(agentId, undefined, status),
    (r) => ({ jobs: r.jobs }),
    'Failed to load jobs', silent),

  refreshAwareness: (agentId, silent?) => loadDomain(set, get,
    'awarenessLoading', 'awarenessError',
    () => api.getAwareness(agentId),
    (r) => ({ awareness: r.awareness || null, awarenessCreateTime: r.create_time || null, awarenessUpdateTime: r.update_time || null }),
    'Failed to load awareness', silent),

  refreshSocialNetwork: (agentId, silent?) => loadDomain(set, get,
    'socialNetworkLoading', 'socialNetworkError',
    () => api.getSocialNetworkList(agentId),
    (r) => ({ socialNetworkList: r.entities || [] }),
    'No social network data', silent),

  refreshChatHistory: (agentId, userId, silent?) => loadDomain(set, get,
    'chatHistoryLoading', 'chatHistoryError',
    () => api.getChatHistory(agentId, userId),
    (r) => ({ chatHistoryEvents: r.events || [], chatHistoryNarratives: r.narratives || [] }),
    'No chat history', silent),

  refreshRAGFiles: (agentId, userId, silent?) => loadDomain(set, get,
    'ragFilesLoading', 'ragFilesError',
    () => api.listRAGFiles(agentId, userId),
    (r) => ({ ragFiles: r.files || [], ragCompletedCount: r.completed_count || 0, ragPendingCount: r.pending_count || 0 }),
    'Failed to load RAG files', silent),

  refreshCost: (agentId, days = 7, silent?) => loadDomain(set, get,
    'costLoading', 'costError',
    () => api.getCosts(agentId, days),
    (r) => ({ costSummary: r.summary || null }),
    'Failed to load cost data', silent),

  // ── Mutation helpers ──────────────────────────────

  addChatHistoryEvent: (event) => {
    set((state) => ({
      chatHistoryEvents: [...state.chatHistoryEvents, event].sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      ),
    }));
  },

  updateInboxMessage: (messageId, updates) => {
    set((state) => ({
      inbox: state.inbox.map((m) => m.message_id === messageId ? { ...m, ...updates } : m),
      inboxUnreadCount: updates.is_read === true
        ? Math.max(0, state.inboxUnreadCount - 1)
        : state.inboxUnreadCount,
    }));
  },

  updateAgentInboxMessage: (messageId, updates) => {
    set((state) => ({
      agentInboxRooms: state.agentInboxRooms.map((room) => ({
        ...room,
        messages: room.messages.map((m) =>
          m.message_id === messageId ? { ...m, ...updates } : m
        ),
        unread_count: updates.is_read === true
          ? Math.max(0, room.unread_count - (room.messages.some((m) => m.message_id === messageId && !m.is_read) ? 1 : 0))
          : room.unread_count,
      })),
      agentInboxUnreadCount: updates.is_read === true
        ? Math.max(0, state.agentInboxUnreadCount - 1)
        : state.agentInboxUnreadCount,
    }));
  },

  markAllInboxRead: () => {
    set((state) => ({
      inbox: state.inbox.map((m) => ({ ...m, is_read: true })),
      inboxUnreadCount: 0,
    }));
  },

  clearAll: () => {
    set({
      inbox: [], inboxUnreadCount: 0, inboxTotalCount: 0, inboxPage: 1,
      agentInboxRooms: [], agentInboxUnreadCount: 0,
      jobs: [],
      awareness: null, awarenessCreateTime: null, awarenessUpdateTime: null,
      socialNetworkList: [],
      chatHistoryEvents: [], chatHistoryNarratives: [],
      ragFiles: [], ragCompletedCount: 0, ragPendingCount: 0,
      costSummary: null,
      lastUserId: null, lastAgentId: null,
      inboxError: null, agentInboxError: null, jobsError: null,
      awarenessError: null, socialNetworkError: null,
      chatHistoryError: null, ragFilesError: null, costError: null,
    });
  },
}));
