/**
 * Tests for AgentGroupSection (collapse toggle, unread aggregation, and the
 * agent row's ⋯ menu — Owner-required, reinstated 2026-09-11 after #383
 * removed it: owner rows get Rename / Model & framework / Delete, other
 * users' public agents stay display-only).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AgentGroupSection } from '../AgentGroupSection';

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
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
    currentUserId: 'u1',
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
});

// ---------------------------------------------------------------------------
// Row ⋯ menu (Owner-required, reinstated 2026-09-11)
// ---------------------------------------------------------------------------

describe('agent row ⋯ menu', () => {
  const makeActions = () => ({
    onRename: vi.fn(),
    onOpenModelConfig: vi.fn(),
    onDelete: vi.fn(),
  });
  const props = {
    teamId: null,
    teamName: '',
    teamColor: null,
    agents: [
      { agent_id: 'a1', name: 'Analyst', created_by: 'u1' },
      { agent_id: 'p1', name: 'Public Bot', created_by: 'someone-else', is_public: true },
    ],
    agentId: null,
    collapsed: false,
    hideHeader: true,
    currentUserId: 'u1',
    onToggleCollapse: vi.fn(),
    onSelectAgent: vi.fn(),
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
  };

  it('offers Rename / Model & framework / Delete on the owner\'s row only', () => {
    render(wrapRouter(<AgentGroupSection {...props} rowActions={makeActions()} />));
    // One kebab: the owner's row. Someone else's public agent has none.
    const kebabs = screen.getAllByLabelText(/agent options/i);
    expect(kebabs).toHaveLength(1);
    fireEvent.click(kebabs[0]);
    const labels = ['Rename', 'Model & framework', 'Delete'];
    for (const label of labels) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('routes each item to the host with the row\'s agent id, without selecting the row', () => {
    const actions = makeActions();
    const onSelectAgent = vi.fn();
    render(wrapRouter(<AgentGroupSection {...props} onSelectAgent={onSelectAgent} rowActions={actions} />));

    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Model & framework' }));
    expect(actions.onOpenModelConfig).toHaveBeenCalledWith('a1');

    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(actions.onDelete).toHaveBeenCalledWith('a1');

    expect(onSelectAgent).not.toHaveBeenCalled();
  });

  it('Rename opens an inline input; Enter commits a changed name once, Escape cancels', () => {
    const actions = makeActions();
    render(wrapRouter(<AgentGroupSection {...props} rowActions={actions} />));

    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const input = screen.getByLabelText('Rename') as HTMLInputElement;
    expect(input.value).toBe('Analyst');
    fireEvent.change(input, { target: { value: '  Chief Analyst ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(actions.onRename).toHaveBeenCalledTimes(1);
    expect(actions.onRename).toHaveBeenCalledWith('a1', 'Chief Analyst');
    expect(screen.queryByLabelText('Rename')).toBeNull();

    // Escape: no call, input gone.
    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const again = screen.getByLabelText('Rename');
    fireEvent.change(again, { target: { value: 'Something else' } });
    fireEvent.keyDown(again, { key: 'Escape' });
    expect(actions.onRename).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText('Rename')).toBeNull();
  });

  it('an unchanged or blank name is not sent', () => {
    const actions = makeActions();
    render(wrapRouter(<AgentGroupSection {...props} rowActions={actions} />));
    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.keyDown(screen.getByLabelText('Rename'), { key: 'Enter' });
    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('Rename'), { target: { value: '   ' } });
    fireEvent.blur(screen.getByLabelText('Rename'));
    expect(actions.onRename).not.toHaveBeenCalled();
  });

  it('a blur fired by the unmounting input does not commit a second time (WebKit)', () => {
    // WebKit dispatches blur when the focused input is removed; that handler
    // runs before React re-renders, from the same closure as Enter/Escape.
    // Firing both inside one act() reproduces it (no render in between).
    const actions = makeActions();
    render(wrapRouter(<AgentGroupSection {...props} rowActions={actions} />));
    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const input = screen.getByLabelText('Rename');
    fireEvent.change(input, { target: { value: 'Chief Analyst' } });
    act(() => {
      fireEvent.keyDown(input, { key: 'Enter' });
      fireEvent.blur(input);
    });
    expect(actions.onRename).toHaveBeenCalledTimes(1);

    // Escape followed by the same unmount blur must not commit the draft.
    fireEvent.click(screen.getByLabelText(/agent options/i));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const again = screen.getByLabelText('Rename');
    fireEvent.change(again, { target: { value: 'Discarded' } });
    act(() => {
      fireEvent.keyDown(again, { key: 'Escape' });
      fireEvent.blur(again);
    });
    expect(actions.onRename).toHaveBeenCalledTimes(1);
  });

  it('no host actions → no menu at all', () => {
    render(wrapRouter(<AgentGroupSection {...props} />));
    expect(screen.queryByLabelText(/agent options/i)).toBeNull();
  });

  it('still selects the agent when the row is clicked', () => {
    const onSelectAgent = vi.fn();
    render(wrapRouter(<AgentGroupSection {...props} onSelectAgent={onSelectAgent} rowActions={makeActions()} />));
    fireEvent.click(screen.getByText('Analyst'));
    expect(onSelectAgent).toHaveBeenCalledWith('a1');
  });
});
