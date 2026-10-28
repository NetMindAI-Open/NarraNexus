/**
 * @file_name: TeamManagePanel.test.tsx
 * @author: NarraNexus
 * @date: 2026-09-03
 * @description: The management tab does what the four surfaces it replaced
 * did — bulletin, lead, patrol, members, clear, delete — through the same
 * calls, from one place.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const setPatrolMock = vi.fn();
const notePatrolMock = vi.fn();
let patrolByTeam: Record<string, boolean> = {};
let patrolInFlight: Record<string, boolean> = {};
const clearDataMock = vi.fn();
const addMemberMock = vi.fn();
const removeMemberMock = vi.fn();
const deleteTeamMock = vi.fn();
const updateTeamMock = vi.fn();
const navigateMock = vi.fn();
const confirmMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    clearTeamData: (...a: unknown[]) => clearDataMock(...a),
  },
}));

vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) =>
    select({
      teams: [],
      refresh: vi.fn(),
      addMember: (...a: unknown[]) => addMemberMock(...a),
      removeMember: (...a: unknown[]) => removeMemberMock(...a),
      deleteTeam: (...a: unknown[]) => deleteTeamMock(...a),
      updateTeam: (...a: unknown[]) => updateTeamMock(...a),
      patrolByTeam,
      patrolInFlight,
      notePatrol: (...a: unknown[]) => notePatrolMock(...a),
      setPatrol: (...a: unknown[]) => setPatrolMock(...a),
    }),
  useConfigStore: (select: (s: unknown) => unknown) => select({ agents: [] }),
}));

vi.mock('@/components/ui', () => ({
  Button: ({ children, onClick, ...rest }: React.ComponentProps<'button'>) => (
    <button onClick={onClick} data-testid={rest['data-testid' as keyof typeof rest] as string}>
      {children}
    </button>
  ),
  useNotice: () => ({
    confirm: (...a: unknown[]) => confirmMock(...a),
    notifyError: vi.fn(),
    dialog: null,
  }),
}));

// The clear dialog and the profile form have their own tests; here they only
// need to be mountable and to hand their result up.
vi.mock('@/components/teams/TeamProfileForm', () => ({
  TeamProfileForm: ({ onSave }: { onSave: (p: unknown) => Promise<void> }) => (
    <button
      data-testid="profile-form"
      onClick={() => void onSave({ name: 'Desk 2', color: '#123', intro_md: 'hi' })}
    >
      form
    </button>
  ),
}));
vi.mock('@/components/teams/ClearTeamDataDialog', () => ({
  ClearTeamDataDialog: ({
    onConfirm,
  }: {
    onConfirm: (s: { chat: boolean; files: boolean; bulletin: boolean }) => void;
  }) => (
    <button
      data-testid="clear-confirm"
      onClick={() => onConfirm({ chat: true, files: false, bulletin: true })}
    >
      confirm
    </button>
  ),
}));
vi.mock('../TeamBulletinPanel', () => ({
  TeamBulletinPanel: () => <div data-testid="bulletin-panel" />,
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamManagePanel } from '../TeamManagePanel';

const ANA = { agent_id: 'a1', name: 'Ana' };
const BRUNO = { agent_id: 'a2', name: 'Bruno' };
const CY = { agent_id: 'a3', name: 'Cy' };

function renderPanel(over: Partial<React.ComponentProps<typeof TeamManagePanel>> = {}) {
  const props: React.ComponentProps<typeof TeamManagePanel> = {
    teamId: 't1',
    teamName: 'Desk',
    team: { team_id: 't1', name: 'Desk', color: '#abc', intro_md: '' },
    members: [ANA, BRUNO] as never,
    allAgents: [ANA, BRUNO, CY] as never,
    leadAgentId: 'a1',
    onSetLead: vi.fn(),
    bulletin: null,
    bulletinLoading: false,
    bulletinError: null,
    memberNames: { a1: 'Ana', a2: 'Bruno' },
    onBulletinAdd: vi.fn(),
    onBulletinEdit: vi.fn(),
    onBulletinDelete: vi.fn(),
    onBulletinClearTier: vi.fn(),
    onCleared: vi.fn(),
    ...over,
  };
  render(<TeamManagePanel {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  patrolByTeam = { t1: true };
  patrolInFlight = {};
  setPatrolMock.mockResolvedValue(undefined);
  clearDataMock.mockResolvedValue({ success: true });
  addMemberMock.mockResolvedValue(undefined);
  removeMemberMock.mockResolvedValue(undefined);
  deleteTeamMock.mockResolvedValue(undefined);
  updateTeamMock.mockResolvedValue(undefined);
  confirmMock.mockResolvedValue(true);
});

describe('TeamManagePanel', () => {
  test('the bulletin is the first thing in the tab', () => {
    renderPanel();
    expect(screen.getByTestId('bulletin-panel')).toBeTruthy();
  });

  test('choosing a lead calls back with the member id', () => {
    const props = renderPanel();
    fireEvent.change(screen.getByTestId('manage-lead-select'), { target: { value: 'a2' } });
    expect(props.onSetLead).toHaveBeenCalledWith('a2');
  });

  test('patrol reads the store and writes through it — no board fetch of its own', async () => {
    renderPanel();
    const toggle = screen.getByTestId('patrol-toggle');
    expect(toggle.textContent).toBe('chat.team.manage.patrolOff');

    fireEvent.click(toggle);

    await waitFor(() => expect(setPatrolMock).toHaveBeenCalledWith('t1', false));
  });

  test('an unreported patrol state disables the switch instead of guessing', () => {
    patrolByTeam = {};
    renderPanel();
    const toggle = screen.getByTestId('patrol-toggle') as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(setPatrolMock).not.toHaveBeenCalled();
  });

  test('a write in flight disables the switch (no double click race)', () => {
    patrolInFlight = { t1: true };
    renderPanel();
    const toggle = screen.getByTestId('patrol-toggle') as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(setPatrolMock).not.toHaveBeenCalled();
  });

  test('a store value of OFF renders the ON action', () => {
    patrolByTeam = { t1: false };
    renderPanel();
    expect(screen.getByTestId('patrol-toggle').textContent).toBe('chat.team.manage.patrolOn');
  });

  test('members are added and removed through the store', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-member-a3'));
    await waitFor(() => expect(addMemberMock).toHaveBeenCalledWith('t1', 'a3'));

    fireEvent.click(screen.getByTestId('manage-member-a2'));
    await waitFor(() => expect(removeMemberMock).toHaveBeenCalledWith('t1', 'a2'));
  });

  test('the profile form is inline and saves through the store — no team manager modal', async () => {
    // The modal switches teams, creates teams and its delete does not leave
    // the room; none of that belongs in this tab. Lead, members, delete
    // each have one live editor here.
    renderPanel();
    // The team manager's own affordances — create / switch — must not be here.
    expect(screen.queryByText('teams.createTeam')).toBeNull();
    expect(screen.queryByText('teams.selectPrompt')).toBeNull();
    fireEvent.click(screen.getByTestId('profile-form'));
    await waitFor(() =>
      expect(updateTeamMock).toHaveBeenCalledWith('t1', { name: 'Desk 2', color: '#123', intro_md: 'hi' }),
    );
  });

  test('clearing data reports the scopes back so the room can drop them', async () => {
    const props = renderPanel();
    fireEvent.click(screen.getByTestId('manage-clear-data'));
    fireEvent.click(screen.getByTestId('clear-confirm'));

    await waitFor(() =>
      expect(clearDataMock).toHaveBeenCalledWith('t1', { chat: true, files: false, bulletin: true }),
    );
    await waitFor(() =>
      expect(props.onCleared).toHaveBeenCalledWith({ chat: true, files: false, bulletin: true }),
    );
  });

  test('deleting asks first, then leaves the room', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-delete-team'));

    await waitFor(() => expect(deleteTeamMock).toHaveBeenCalledWith('t1'));
    expect(confirmMock).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/app/chat');
  });

  test('a declined delete confirmation deletes nothing', async () => {
    confirmMock.mockResolvedValue(false);
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-delete-team'));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(deleteTeamMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
