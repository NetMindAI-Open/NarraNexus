/**
 * teamsStore - Subproject 1: Team Membership state
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api, ApiError } from '@/lib/api';
import type { TeamWithMembers } from '@/types';

interface TeamsState {
  teams: TeamWithMembers[];
  loading: boolean;
  loaded: boolean;

  refresh: () => Promise<void>;
  createTeam: (payload: { name: string; description?: string; color?: string }) => Promise<string | null>;
  updateTeam: (teamId: string, patch: { name?: string; description?: string; color?: string; intro_md?: string; lead_agent_id?: string }) => Promise<void>;
  deleteTeam: (teamId: string) => Promise<void>;
  addMember: (teamId: string, agentId: string) => Promise<void>;
  removeMember: (teamId: string, agentId: string) => Promise<void>;

  /** Patrol switch per team — ONE copy. The room poll and the work board
   *  poll both write it (`notePatrol`); the management tab reads it and
   *  writes through `setPatrol`. Not persisted: it is server state. */
  patrolByTeam: Record<string, boolean>;
  /** Per team: until when (epoch ms) a poll-reported value is ignored after a
   *  write commits, so a GET already in flight at click time cannot overwrite
   *  the optimistic write with the pre-click value. */
  patrolPendingUntil: Record<string, number>;
  /** Per team: a PUT is in flight. Polls are ignored meanwhile, and the
   *  switch is disabled so a second click cannot race the first. */
  patrolInFlight: Record<string, boolean>;
  notePatrol: (teamId: string, enabled: boolean) => void;
  setPatrol: (teamId: string, enabled: boolean) => Promise<void>;

  // selectors
  teamsForAgent: (agentId: string) => TeamWithMembers[];
}

/** Longer than one room poll (3s) so the in-flight GET is covered; short
 *  enough that a flip from another device shows within seconds. */
export const PATROL_SETTLE_MS = 4000;

export const useTeamsStore = create<TeamsState>()(
  persist(
    (set, get) => ({
      teams: [],
      loading: false,
      loaded: false,

      refresh: async () => {
        set({ loading: true });
        try {
          const r = await api.listTeams();
          set({ teams: r.teams, loaded: true });
        } catch (e) {
          console.error('listTeams failed', e);
        } finally {
          set({ loading: false });
        }
      },

      createTeam: async (payload) => {
        const r = await api.createTeam(payload);
        await get().refresh();
        return r.team?.team_id || null;
      },

      updateTeam: async (teamId, patch) => {
        await api.updateTeam(teamId, patch);
        await get().refresh();
      },

      deleteTeam: async (teamId) => {
        try {
          await api.deleteTeam(teamId);
        } catch (e) {
          // 404 = the team is already gone server-side; only the persisted
          // localStorage cache still shows it. Rethrowing here would skip
          // refresh() and trap the user in delete -> 404 -> still-shown.
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
        await get().refresh();
      },

      addMember: async (teamId, agentId) => {
        await api.addTeamMember(teamId, agentId);
        await get().refresh();
      },

      removeMember: async (teamId, agentId) => {
        await api.removeTeamMember(teamId, agentId);
        await get().refresh();
      },

      patrolByTeam: {},
      patrolPendingUntil: {},
      patrolInFlight: {},
      notePatrol: (teamId, enabled) =>
        set((s) => {
          // A poll value is only stale — never wrong — while a write is in
          // flight or inside the window it just opened; outside that,
          // another tab's real flip must land.
          if (s.patrolInFlight[teamId]) return s;
          if ((s.patrolPendingUntil[teamId] ?? 0) > Date.now()) return s;
          return s.patrolByTeam[teamId] === enabled
            ? s
            : { patrolByTeam: { ...s.patrolByTeam, [teamId]: enabled } };
        }),
      setPatrol: async (teamId, enabled) => {
        const prev = get().patrolByTeam[teamId];
        if (get().patrolInFlight[teamId]) return; // one write at a time
        set((s) => ({
          patrolByTeam: { ...s.patrolByTeam, [teamId]: enabled }, // optimistic
          patrolInFlight: { ...s.patrolInFlight, [teamId]: true },
        }));
        try {
          await api.setTeamPatrol(teamId, enabled);
          // Keep ignoring polls for one poll cycle: a GET that left before
          // the PUT committed still carries the old value.
          set((s) => ({
            patrolPendingUntil: { ...s.patrolPendingUntil, [teamId]: Date.now() + PATROL_SETTLE_MS },
          }));
        } catch (e) {
          set((s) => {
            const patrolByTeam = { ...s.patrolByTeam };
            // Roll back to what was reported; with nothing reported, back to
            // "unknown" — never to a guessed boolean the switch would trust.
            if (prev === undefined) delete patrolByTeam[teamId];
            else patrolByTeam[teamId] = prev;
            const patrolPendingUntil = { ...s.patrolPendingUntil };
            delete patrolPendingUntil[teamId];
            return { patrolByTeam, patrolPendingUntil };
          });
          throw e;
        } finally {
          set((s) => {
            const patrolInFlight = { ...s.patrolInFlight };
            delete patrolInFlight[teamId];
            return { patrolInFlight };
          });
        }
      },

      teamsForAgent: (agentId) => {
        return get().teams.filter((t) => t.member_agent_ids.includes(agentId));
      },
    }),
    {
      name: 'narra-nexus-teams',
      partialize: (s) => ({ teams: s.teams, loaded: s.loaded }),
    }
  )
);
