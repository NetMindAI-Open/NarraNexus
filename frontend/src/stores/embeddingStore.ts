/**
 * Embedding status store
 * Manages embedding rebuild state and auto-polling
 */

import { create } from 'zustand';
import { api } from '@/lib/api';
import type { EmbeddingStatusData } from '@/types';

interface EmbeddingState {
  /** Current embedding status from backend */
  status: EmbeddingStatusData | null;
  /** Whether we're currently fetching status */
  loading: boolean;
  /** Polling interval ID */
  _pollTimer: ReturnType<typeof setInterval> | null;

  /** Fetch embedding status from backend */
  fetchStatus: () => Promise<void>;
  /** Start rebuilding embeddings */
  startRebuild: () => Promise<boolean>;
  /** Start auto-polling (every 5s when rebuilding, 30s otherwise) */
  startPolling: () => void;
  /** Stop auto-polling */
  stopPolling: () => void;
}

export const useEmbeddingStore = create<EmbeddingState>()((set, get) => ({
  status: null,
  loading: false,
  _pollTimer: null,

  fetchStatus: async () => {
    try {
      set({ loading: true });
      const res = await api.getEmbeddingStatus();
      if (res.success) {
        set({ status: res.data });
      }
    } catch (err) {
      // Silently fail — embedding status is non-critical
      console.debug('Embedding status fetch failed:', err);
    } finally {
      set({ loading: false });
    }
  },

  startRebuild: async () => {
    try {
      const res = await api.rebuildEmbeddings();
      if (res.success) {
        // Immediately refresh status and switch to fast polling
        await get().fetchStatus();
        get().startPolling();
        return true;
      }
      return false;
    } catch (err) {
      console.error('Failed to start embedding rebuild:', err);
      return false;
    }
  },

  startPolling: () => {
    const { _pollTimer } = get();
    if (_pollTimer) clearInterval(_pollTimer);

    const poll = async () => {
      await get().fetchStatus();
      const status = get().status;
      // If rebuild is done, slow down polling
      if (status && !status.migration.is_running && status.all_done) {
        get().stopPolling();
      }
    };

    // Poll interval: fast when rebuilding, slow otherwise
    const status = get().status;
    const interval = status?.migration.is_running ? 3000 : 15000;

    const timer = setInterval(poll, interval);
    set({ _pollTimer: timer });
  },

  stopPolling: () => {
    const { _pollTimer } = get();
    if (_pollTimer) {
      clearInterval(_pollTimer);
      set({ _pollTimer: null });
    }
  },
}));
