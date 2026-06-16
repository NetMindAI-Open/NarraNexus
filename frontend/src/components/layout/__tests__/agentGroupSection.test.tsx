/**
 * Tests for AgentGroupSection (collapse toggle, unread aggregation, nav arrow)
 * and AgentRowMenu (kebab menu entries: rename, delete, public toggle).
 * Also tests the AGENTS header ⋯ menu entries.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// We import the file under test (will fail until the file is created).
import { AgentGroupSection } from '../AgentGroupSection';
import { AgentRowMenu } from '../AgentRowMenu';
import { AgentsHeaderMenu } from '../AgentsHeaderMenu';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const wrapRouter = (ui: React.ReactNode) => (
  <MemoryRouter>{ui}</MemoryRouter>
);

// ---------------------------------------------------------------------------
// AgentGroupSection
// ---------------------------------------------------------------------------

describe('AgentGroupSection', () => {
  const defaultProps = {
    teamId: 't1',
    teamName: 'Trading Desk',
    teamColor: '#e56',
    agents: [
      { agent_id: 'a1', name: 'Analyst' },
      { agent_id: 'a2', name: 'Risk Officer' },
    ],
    agentId: null,
    collapsed: false,
    onToggleCollapse: vi.fn(),
    onSelectAgent: vi.fn(),
    onNavigateToTeam: vi.fn(),
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
    onStartEdit: vi.fn(),
    onDelete: vi.fn(),
    onTogglePublic: vi.fn(),
    deletingAgentId: null,
    editingAgentId: null,
    editingName: '',
    onEditNameChange: vi.fn(),
    onSaveEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    savingName: false,
  };

  it('renders the team name in the section header', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('Trading Desk')).toBeInTheDocument();
  });

  it('renders member count badge', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders agent names when not collapsed', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('Analyst')).toBeInTheDocument();
    expect(screen.getByText('Risk Officer')).toBeInTheDocument();
  });

  it('hides agent rows when collapsed=true', () => {
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} collapsed={true} />
      )
    );
    expect(screen.queryByText('Analyst')).not.toBeInTheDocument();
    expect(screen.queryByText('Risk Officer')).not.toBeInTheDocument();
  });

  it('calls onToggleCollapse when header is clicked', () => {
    const onToggle = vi.fn();
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} onToggleCollapse={onToggle} />
      )
    );
    // The header button with the triangle
    const headerBtn = screen.getByRole('button', { name: /trading desk/i });
    fireEvent.click(headerBtn);
    expect(onToggle).toHaveBeenCalledWith('t1');
  });

  it('shows aggregated unread pill in header when collapsed and unread > 0', () => {
    const getRowMeta = (aid: string) => ({
      preview: '',
      time: '',
      unread: aid === 'a1' ? 3 : 0,
    });
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          collapsed={true}
          getRowMeta={getRowMeta}
        />
      )
    );
    // The aggregated count should be visible in the header.
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('does NOT show aggregated unread when count is 0', () => {
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} collapsed={true} />
      )
    );
    // No unread pill — only the member count "2" should be present.
    // The digit "2" is the member count, not an unread count.
    // We're just verifying there is no separate unread pill showing 0.
    // The member count "2" is acceptable; there should be no "0" pill.
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('renders the Ungrouped section with hollow dot visual hint', () => {
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          teamId={null}
          teamName="Ungrouped"
          teamColor={null}
        />
      )
    );
    // The Ungrouped section header should be present.
    expect(screen.getByText('Ungrouped')).toBeInTheDocument();
  });

  it('does not render a team-detail nav arrow for the Ungrouped section', () => {
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          teamId={null}
          teamName="Ungrouped"
          teamColor={null}
          onNavigateToTeam={vi.fn()}
        />
      )
    );
    // No navigate-to-team button for Ungrouped
    expect(screen.queryByRole('button', { name: /go to team/i })).not.toBeInTheDocument();
  });

  it('calls onNavigateToTeam when the → nav button is clicked for a named team', () => {
    const onNavigate = vi.fn();
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          teamId="t1"
          onNavigateToTeam={onNavigate}
        />
      )
    );
    // The nav button should exist for a named team
    const navBtn = screen.getByRole('button', { name: /go to team/i });
    fireEvent.click(navBtn);
    expect(onNavigate).toHaveBeenCalledWith('t1');
  });
});

// ---------------------------------------------------------------------------
// AgentRowMenu (kebab ⋮)
// ---------------------------------------------------------------------------

describe('AgentRowMenu', () => {
  const defaultProps = {
    agentId: 'a1',
    agentName: 'Analyst',
    isOwner: true,
    isPublic: false,
    onStartEdit: vi.fn(),
    onDelete: vi.fn(),
    onTogglePublic: vi.fn(),
    showPublicToggle: true,
  };

  it('exposes a rename entry', () => {
    render(wrapRouter(<AgentRowMenu {...defaultProps} />));
    // Click the kebab trigger to open the menu.
    const trigger = screen.getByRole('button', { name: /agent options/i });
    fireEvent.click(trigger);
    expect(screen.getByText(/rename/i)).toBeInTheDocument();
  });

  it('exposes a delete entry', () => {
    render(wrapRouter(<AgentRowMenu {...defaultProps} />));
    const trigger = screen.getByRole('button', { name: /agent options/i });
    fireEvent.click(trigger);
    expect(screen.getByText(/delete/i)).toBeInTheDocument();
  });

  it('calls onStartEdit when rename is clicked', () => {
    const onStartEdit = vi.fn();
    render(wrapRouter(<AgentRowMenu {...defaultProps} onStartEdit={onStartEdit} />));
    fireEvent.click(screen.getByRole('button', { name: /agent options/i }));
    fireEvent.click(screen.getByText(/rename/i));
    expect(onStartEdit).toHaveBeenCalled();
  });

  it('calls onDelete when delete is clicked', () => {
    const onDelete = vi.fn();
    render(wrapRouter(<AgentRowMenu {...defaultProps} onDelete={onDelete} />));
    fireEvent.click(screen.getByRole('button', { name: /agent options/i }));
    fireEvent.click(screen.getByText(/delete/i));
    expect(onDelete).toHaveBeenCalled();
  });

  it('shows public toggle entry when showPublicToggle=true and isOwner', () => {
    render(wrapRouter(<AgentRowMenu {...defaultProps} showPublicToggle={true} />));
    fireEvent.click(screen.getByRole('button', { name: /agent options/i }));
    expect(screen.getByText(/public|private/i)).toBeInTheDocument();
  });

  it('hides public toggle entry when showPublicToggle=false', () => {
    render(wrapRouter(<AgentRowMenu {...defaultProps} showPublicToggle={false} />));
    fireEvent.click(screen.getByRole('button', { name: /agent options/i }));
    expect(screen.queryByText(/set to public|set to private/i)).not.toBeInTheDocument();
  });

  it('hides owner-only actions when isOwner=false', () => {
    render(wrapRouter(<AgentRowMenu {...defaultProps} isOwner={false} />));
    fireEvent.click(screen.getByRole('button', { name: /agent options/i }));
    expect(screen.queryByText(/delete/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AgentsHeaderMenu (⋯ menu on AGENTS header)
// ---------------------------------------------------------------------------

describe('AgentsHeaderMenu', () => {
  const defaultProps = {
    onImport: vi.fn(),
    onExport: vi.fn(),
    onManageTeams: vi.fn(),
  };

  it('exposes an Import entry', () => {
    render(wrapRouter(<AgentsHeaderMenu {...defaultProps} />));
    const trigger = screen.getByRole('button', { name: /agents menu/i });
    fireEvent.click(trigger);
    expect(screen.getByText(/import/i)).toBeInTheDocument();
  });

  it('exposes an Export entry', () => {
    render(wrapRouter(<AgentsHeaderMenu {...defaultProps} />));
    fireEvent.click(screen.getByRole('button', { name: /agents menu/i }));
    expect(screen.getByText(/export/i)).toBeInTheDocument();
  });

  it('exposes a Manage Teams entry', () => {
    render(wrapRouter(<AgentsHeaderMenu {...defaultProps} />));
    fireEvent.click(screen.getByRole('button', { name: /agents menu/i }));
    expect(screen.getByText(/manage teams/i)).toBeInTheDocument();
  });

  it('calls onImport when Import is clicked', () => {
    const onImport = vi.fn();
    render(wrapRouter(<AgentsHeaderMenu {...defaultProps} onImport={onImport} />));
    fireEvent.click(screen.getByRole('button', { name: /agents menu/i }));
    fireEvent.click(screen.getByText(/import/i));
    expect(onImport).toHaveBeenCalled();
  });

  it('calls onManageTeams when Manage Teams is clicked', () => {
    const onManageTeams = vi.fn();
    render(wrapRouter(<AgentsHeaderMenu {...defaultProps} onManageTeams={onManageTeams} />));
    fireEvent.click(screen.getByRole('button', { name: /agents menu/i }));
    fireEvent.click(screen.getByText(/manage teams/i));
    expect(onManageTeams).toHaveBeenCalled();
  });
});


describe('kebab menu stacking (2026-06-11 fix)', () => {
  const props = {
    teamId: 't1',
    teamName: 'Trading Desk',
    teamColor: '#e56',
    agents: [
      { agent_id: 'a1', name: 'Analyst', created_by: 'u1' },
      { agent_id: 'a2', name: 'Risk Officer', created_by: 'u1' },
    ],
    agentId: null,
    collapsed: false,
    currentUserId: 'u1',
    showPublicToggle: false,
    onToggleCollapse: vi.fn(),
    onSelectAgent: vi.fn(),
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
    onStartEdit: vi.fn(),
    onDelete: vi.fn(),
    onTogglePublic: vi.fn(),
    deletingAgentId: null,
    editingAgentId: null,
    editingName: '',
    onEditNameChange: vi.fn(),
    onSaveEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    savingName: false,
  };

  it('opening the row menu lifts the row above sibling stacking contexts', () => {
    render(wrapRouter(<AgentGroupSection {...props} />));

    const kebab = screen.getAllByLabelText('Agent options')[0];
    fireEvent.click(kebab);

    // The row container must carry the z-lift while the menu is open —
    // without it, the next row's retained-transform stacking context
    // paints over the panel and Delete becomes unclickable.
    const row = kebab.closest('div.group');
    expect(row?.className).toContain('z-30');

    fireEvent.click(kebab);
    expect(row?.className).not.toContain('z-30');
  });
});
