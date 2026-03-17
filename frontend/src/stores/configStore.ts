/**
 * Configuration store
 * Manages authentication, agent selection, and app settings
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';
import type { AgentInfo } from '@/types';

export type { AgentInfo };

interface ConfigState {
  // Auth state
  isLoggedIn: boolean;
  userId: string;

  // Agent state
  agentId: string;
  agents: AgentInfo[];

  // Awareness update tracking (red dot notification)
  awarenessUpdatedAgents: string[];

  // Actions
  login: (userId: string) => void;
  logout: () => void;
  setAgentId: (id: string) => void;
  setAgents: (agents: AgentInfo[]) => void;
  refreshAgents: () => Promise<void>;
  checkAwarenessUpdate: (agentId: string) => Promise<void>;
  clearAwarenessUpdate: (agentId: string) => void;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set, get) => ({
      // Default values
      isLoggedIn: false,
      userId: '',
      agentId: '',
      agents: [],
      awarenessUpdatedAgents: [],

      // Actions
      login: (userId) => set({ isLoggedIn: true, userId }),

      logout: () => set({
        isLoggedIn: false,
        userId: '',
        agentId: '',
        agents: [],
        awarenessUpdatedAgents: [],
      }),

      setAgentId: (id) => set({ agentId: id }),

      setAgents: (agents) => set({ agents }),

      refreshAgents: async () => {
        const { userId } = get();
        if (!userId) return;
        try {
          const res = await api.getAgents(userId);
          if (res.success) {
            set({ agents: res.agents });
          }
        } catch (err) {
          console.error('Failed to refresh agents:', err);
        }
      },

      checkAwarenessUpdate: async (agentId: string) => {
        try {
          const res = await api.getAwareness(agentId);
          if (res.success && res.update_time) {
            const lastSeen = localStorage.getItem(`lastSeenAwarenessTime:${agentId}`);
            if (!lastSeen || res.update_time > lastSeen) {
              const current = get().awarenessUpdatedAgents;
              if (!current.includes(agentId)) {
                set({ awarenessUpdatedAgents: [...current, agentId] });
              }
            }
          }
        } catch (err) {
          console.error('Failed to check awareness update:', err);
        }
      },

      clearAwarenessUpdate: (agentId: string) => {
        // Store current time as last seen
        localStorage.setItem(`lastSeenAwarenessTime:${agentId}`, new Date().toISOString());
        set({
          awarenessUpdatedAgents: get().awarenessUpdatedAgents.filter((id) => id !== agentId),
        });
      },
    }),
    {
      name: 'narra-nexus-config',
    }
  )
);
