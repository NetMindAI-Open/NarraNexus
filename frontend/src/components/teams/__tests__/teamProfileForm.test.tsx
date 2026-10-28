/**
 * @file_name: teamProfileForm.test.tsx
 * @date: 2026-09-03
 * @description: The shared profile form: three fields, one save, re-seeds
 * from a newer team row.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { TeamProfileForm } from '../TeamProfileForm';

const TEAM = { team_id: 't1', name: 'Desk', color: '#111111', intro_md: 'hello', updated_at: '1' };

describe('TeamProfileForm', () => {
  it('saves exactly name / colour / intro', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<TeamProfileForm team={TEAM} onSave={onSave} />);
    fireEvent.change(screen.getByTestId('team-profile-name'), { target: { value: 'Desk 2' } });
    fireEvent.change(screen.getByTestId('team-profile-intro'), { target: { value: 'bye' } });
    fireEvent.click(screen.getByTestId('team-profile-save'));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ name: 'Desk 2', color: '#111111', intro_md: 'bye' }),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('lead_agent_id');
  });

  it('offers no delete, no team switching, no create', () => {
    render(<TeamProfileForm team={TEAM} onSave={vi.fn()} />);
    expect(screen.queryByText('teams.deleteTeam')).toBeNull();
    expect(screen.queryByText('teams.createTeam')).toBeNull();
  });

  it('re-seeds when the server reports a newer row', () => {
    const { rerender } = render(<TeamProfileForm team={TEAM} onSave={vi.fn()} />);
    rerender(<TeamProfileForm team={{ ...TEAM, name: 'Renamed', updated_at: '2' }} onSave={vi.fn()} />);
    expect((screen.getByTestId('team-profile-name') as HTMLInputElement).value).toBe('Renamed');
  });
});
