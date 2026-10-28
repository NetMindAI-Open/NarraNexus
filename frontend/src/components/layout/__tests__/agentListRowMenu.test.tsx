/**
 * @file_name: agentListRowMenu.test.tsx
 * @author:
 * @date: 2026-09-11
 * @description: Wiring of the sidebar agent row's ⋯ menu inside AgentList
 * (Owner-required, reinstated 2026-09-11 after #383 removed it): Model &
 * framework opens the shared AgentLlmConfigPanel for that agent; Rename goes
 * through the current update API; Delete asks first, deletes through the
 * current API, re-points the active agent and — only when the deleted agent
 * was the one on screen — lands on the Dashboard like the profile page does.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  getAgents: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => h.navigate };
});

vi.mock('@/lib/api', () => ({
  api: {
    getAgents: h.getAgents,
    getTeams: vi.fn().mockResolvedValue({ success: true, teams: [] }),
    updateAgent: h.updateAgent,
    deleteAgent: h.deleteAgent,
  },
}));

vi.mock('@/components/chat/AgentLlmConfigPanel', () => ({
  AgentLlmConfigPanel: (p: { agentId: string; isOpen: boolean; onClose: () => void }) =>
    p.isOpen ? (
      <div data-testid="llm-config-panel">
        {p.agentId}
        <button type="button" onClick={p.onClose}>stub-close</button>
      </div>
    ) : null,
}));

import { AgentList } from '../AgentList';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';

const ALPHA = { agent_id: 'a1', name: 'Alpha', created_by: 'u1' };
const BRAVO = { agent_id: 'a2', name: 'Bravo', created_by: 'u1' };

function openMenuOf(name: string) {
  const row = screen.getByText(name).closest('.group') as HTMLElement;
  fireEvent.click(row.querySelector('[aria-label="Agent options"]')!);
}

beforeEach(() => {
  h.navigate.mockReset();
  h.updateAgent.mockReset().mockResolvedValue({ success: true, agent: { ...ALPHA, name: 'Alpha 2' } });
  h.deleteAgent.mockReset().mockResolvedValue({ success: true });
  h.getAgents.mockReset().mockResolvedValue({ success: true, agents: [BRAVO], count: 1 });
  useTeamsStore.setState({ teams: [], loaded: true });
  useChatStore.setState({ agentSessions: {}, activeAgentId: 'a1' });
  useConfigStore.setState({ userId: 'u1', agentId: 'a1', agents: [ALPHA, BRAVO] as never });
});

describe('AgentList — agent row ⋯ menu', () => {
  it('Model & framework opens the config panel for that row\'s agent', () => {
    render(<AgentList />, { wrapper: MemoryRouter });
    openMenuOf('Bravo');
    fireEvent.click(screen.getByRole('button', { name: 'Model & framework' }));
    expect(screen.getByTestId('llm-config-panel').textContent).toContain('a2');
    fireEvent.click(screen.getByRole('button', { name: 'stub-close' }));
    expect(screen.queryByTestId('llm-config-panel')).toBeNull();
  });

  it('Rename saves the new name through the update API and refreshes the list', async () => {
    render(<AgentList />, { wrapper: MemoryRouter });
    openMenuOf('Alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    const input = screen.getByLabelText('Rename');
    fireEvent.change(input, { target: { value: 'Alpha 2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(h.updateAgent).toHaveBeenCalledWith('a1', 'Alpha 2'));
    await waitFor(() => expect(h.getAgents).toHaveBeenCalled());
  });

  it('Delete asks first — Cancel deletes nothing', async () => {
    render(<AgentList />, { wrapper: MemoryRouter });
    openMenuOf('Alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Delete agent')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Delete agent')).toBeNull());
    expect(h.deleteAgent).not.toHaveBeenCalled();
  });

  it('deleting the agent on screen re-points the active agent and lands on the Dashboard', async () => {
    render(<AgentList />, { wrapper: MemoryRouter });
    openMenuOf('Alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await screen.findByText('Delete agent');
    // The dialog's confirm button carries the same "Delete" label as the menu
    // item (now closed) — take the last one, the dialog's.
    const buttons = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(h.deleteAgent).toHaveBeenCalledWith('a1'));
    await waitFor(() => expect(h.navigate).toHaveBeenCalledWith('/app/dashboard'));
    expect(useConfigStore.getState().agentId).toBe('a2');
    expect(useChatStore.getState().activeAgentId).toBe('a2');
  });

  it('deleting another agent leaves the current view alone', async () => {
    h.getAgents.mockResolvedValue({ success: true, agents: [ALPHA], count: 1 });
    render(<AgentList />, { wrapper: MemoryRouter });
    openMenuOf('Bravo');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await screen.findByText('Delete agent');
    const buttons = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(h.deleteAgent).toHaveBeenCalledWith('a2'));
    await waitFor(() => expect(h.getAgents).toHaveBeenCalled());
    expect(h.navigate).not.toHaveBeenCalled();
    expect(useConfigStore.getState().agentId).toBe('a1');
  });
});
