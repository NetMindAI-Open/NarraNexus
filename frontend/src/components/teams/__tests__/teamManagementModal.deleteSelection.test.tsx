/**
 * Deleting a team from the management modal must land the selection on the
 * NEIGHBOURING team (the row after it, or before it at the end of the list) —
 * not the top of the list, and not the empty state. Every live caller passes
 * initialTeamId, which guards off the teams[0] fallback, so the delete
 * handler is the only thing standing between the user and a stranded pane.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const deleteTeam = vi.fn().mockResolvedValue(undefined);

const mkTeam = (id: string, name: string) => ({
  team: { team_id: id, name, color: '#3b82f6', intro_md: '', lead_agent_id: null, source: 'local' },
  member_agent_ids: [],
});

const teams = [mkTeam('t1', 'Team One'), mkTeam('t2', 'Team Two'), mkTeam('t3', 'Team Three')];

vi.mock('@/stores', () => ({
  useTeamsStore: () => ({
    teams,
    refresh: vi.fn(),
    createTeam: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam,
    addMember: vi.fn(),
    removeMember: vi.fn(),
    loading: false,
  }),
  useConfigStore: () => ({ agents: [] }),
}));

vi.mock('@/components/ui', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/components/ui')>();
  return {
    ...mod,
    useNotice: () => ({
      confirm: vi.fn().mockResolvedValue(true),
      notifyError: vi.fn(),
      dialog: null,
    }),
  };
});

import { TeamManagementModal } from '../TeamManagementModal';

describe('TeamManagementModal delete selection', () => {
  beforeEach(() => {
    deleteTeam.mockClear();
  });

  it('lands on the neighbouring team after deleting a middle row', async () => {
    render(<TeamManagementModal open initialTeamId="t2" onClose={vi.fn()} />);
    // The edit pane reflects the selected team's name.
    await waitFor(() => {
      expect(screen.getByDisplayValue('Team Two')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /delete team/i }));
    await waitFor(() => {
      expect(deleteTeam).toHaveBeenCalledWith('t2');
    });
    // Neighbour after the deleted row — NOT the top of the list.
    await waitFor(() => {
      expect(screen.getByDisplayValue('Team Three')).toBeInTheDocument();
    });
  });

  it('falls back to the previous row when deleting the last team', async () => {
    render(<TeamManagementModal open initialTeamId="t3" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('Team Three')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /delete team/i }));
    await waitFor(() => {
      expect(deleteTeam).toHaveBeenCalledWith('t3');
    });
    await waitFor(() => {
      expect(screen.getByDisplayValue('Team Two')).toBeInTheDocument();
    });
  });
});
