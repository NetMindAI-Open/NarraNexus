/**
 * @file_name: teamsStore.test.ts
 * @description: Behavior contract for the teams state store.
 *
 * Key invariants tested:
 *   - deleteTeam treats a backend 404 as "already deleted": it must NOT
 *     rethrow and MUST still refresh, so a stale localStorage-cached team
 *     is purged instead of looping forever (delete -> 404 -> still shown)
 *   - deleteTeam rethrows non-404 errors unchanged
 *   - refresh overwrites the cached teams list with the server response
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    api: {
      listTeams: vi.fn(),
      deleteTeam: vi.fn(),
    },
  };
});

import { api, ApiError } from '@/lib/api';
import { useTeamsStore } from '../teamsStore';
import type { TeamWithMembers } from '@/types';

const team = (id: string): TeamWithMembers =>
  ({
    team_id: id,
    name: `Team ${id}`,
    member_agent_ids: [],
  }) as unknown as TeamWithMembers;

beforeEach(() => {
  vi.clearAllMocks();
  useTeamsStore.setState({ teams: [team('stale')], loading: false, loaded: true });
});

describe('deleteTeam', () => {
  it('treats a 404 as success and refreshes so the stale cache is purged', async () => {
    vi.mocked(api.deleteTeam).mockRejectedValue(new ApiError(404, 'API error: 404 Not Found'));
    vi.mocked(api.listTeams).mockResolvedValue({ teams: [] } as never);

    await expect(useTeamsStore.getState().deleteTeam('stale')).resolves.toBeUndefined();

    expect(api.listTeams).toHaveBeenCalledTimes(1);
    expect(useTeamsStore.getState().teams).toEqual([]);
  });

  it('rethrows non-404 errors', async () => {
    vi.mocked(api.deleteTeam).mockRejectedValue(new ApiError(500, 'API error: 500'));

    await expect(useTeamsStore.getState().deleteTeam('stale')).rejects.toThrow('API error: 500');
    expect(api.listTeams).not.toHaveBeenCalled();
  });
});

describe('refresh', () => {
  it('overwrites the cached list with the server response (no merge)', async () => {
    vi.mocked(api.listTeams).mockResolvedValue({ teams: [team('fresh')] } as never);

    await useTeamsStore.getState().refresh();

    expect(useTeamsStore.getState().teams.map((t) => t.team_id)).toEqual(['fresh']);
  });
});
