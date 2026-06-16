/**
 * Configuration store
 * Manages authentication, agent selection, and app settings
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';
import { useTeamsStore } from './teamsStore';
import type { AgentInfo } from '@/types';

export type { AgentInfo };

interface ConfigState {
  // Auth state
  isLoggedIn: boolean;
  userId: string;
  token: string;  // JWT token (cloud mode only)
  role: string;   // 'user' | 'staff'
  netmindToken: string;  // NetMind loginToken, retained for Phase 2/3 actions
  displayName: string;   // NetMind nickname, for display (userId is opaque hex)
  email: string;         // NetMind account email

  // Agent state
  agentId: string;
  agents: AgentInfo[];

  // Awareness update tracking (red dot notification)
  awarenessUpdatedAgents: string[];

  // Actions
  login: (userId: string, token?: string, role?: string, profile?: { displayName?: string; email?: string }) => void;
  setNetmindToken: (token: string) => void;
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
      token: '',
      role: '',
      netmindToken: '',
      displayName: '',
      email: '',
      agentId: '',
      agents: [],
      awarenessUpdatedAgents: [],

      // Actions
      login: (userId, token?, role?, profile?) => {
        const prevUserId = get().userId;
        set({
          isLoggedIn: true,
          userId,
          token: token || '',
          role: role || '',
          displayName: profile?.displayName || '',
          email: profile?.email || '',
        });
        // If we just switched accounts (or just logged in fresh after a
        // logout), wipe per-user persisted caches so the next consumer
        // refetches against the right identity. teamsStore is the only
        // store currently using zustand persist for per-user data — see
        // its frontmatter and `partialize: { teams, loaded }`. Without
        // this reset, AgentList's `if (!teamsLoaded) teamsRefresh()` guard
        // keeps showing the previous user's team sections because
        // `loaded` survives in localStorage.
        if (prevUserId !== userId) {
          useTeamsStore.setState({ teams: [], loaded: false });
        }
      },

      setNetmindToken: (token) => set({ netmindToken: token }),

      logout: () => {
        set({
          isLoggedIn: false,
          userId: '',
          token: '',
          role: '',
          netmindToken: '',
          displayName: '',
          email: '',
          agentId: '',
          agents: [],
          awarenessUpdatedAgents: [],
        });
        // Symmetric reset — see comment in login(). Logging out and back
        // in as the same user still benefits from this (we cleared
        // anyway, refresh will repopulate); logging in as a different
        // user is the actual bug this prevents.
        useTeamsStore.setState({ teams: [], loaded: false });
      },

      setAgentId: (id) => set({ agentId: id }),

      setAgents: (agents) => set({ agents }),

      refreshAgents: async () => {
        const { userId } = get();
        if (!userId) return;
        try {
          const res = await api.getAgents();
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
