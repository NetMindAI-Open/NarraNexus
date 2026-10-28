/**
 * @file_name: AgentCompletionToast.team.test.tsx
 * @description: The toast that says a room started talking while you were away.
 *
 * The sidebar dot answers "has anything happened"; it only answers it when the
 * user looks at the sidebar. A room that wakes up while the user is reading
 * something else is the case the dot cannot cover, and it is the common one —
 * the room is async precisely so the user can be elsewhere.
 *
 * The toast queue was agent-shaped: keyed by `agentId`, and "View" switched the
 * active agent. A team toast has neither an agent to switch to nor an agent id
 * to be keyed by, so the item became a discriminated union rather than an agent
 * item with an empty agentId — one field carrying two meanings is exactly the
 * shape that makes a bug invisible later.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNavigate: () => navigate,
}));

import { AgentCompletionToast } from '../AgentCompletionToast';
import { useChatStore, useConfigStore } from '@/stores';

const agentToast = {
  kind: 'agent' as const,
  agentId: 'a1',
  agentName: 'Ada',
  timestamp: Date.now(),
};
const teamToast = {
  kind: 'team' as const,
  teamId: 't1',
  teamName: 'Desk',
  timestamp: Date.now(),
};

beforeEach(() => {
  navigate.mockReset();
  useChatStore.setState({ toastQueue: [] });
  useConfigStore.setState({ agentId: '' });
});

const renderToasts = () =>
  render(
    <MemoryRouter>
      <AgentCompletionToast />
    </MemoryRouter>,
  );

describe('team completion toast', () => {
  test('a team toast names the room', () => {
    useChatStore.setState({ toastQueue: [teamToast] });
    renderToasts();

    expect(screen.getByText(/Desk/)).toBeTruthy();
  });

  test('viewing a team toast opens the room, not an agent', () => {
    // The failure this guards: a team toast reusing the agent path would call
    // setAgentId with a team id and land the user in a chat that does not exist.
    useChatStore.setState({ toastQueue: [teamToast] });
    renderToasts();

    fireEvent.click(screen.getByRole('button', { name: /view/i }));

    expect(navigate).toHaveBeenCalledWith('/app/teams/t1/chat');
    expect(useConfigStore.getState().agentId).toBe('');
  });

  test('viewing a team toast dismisses it', () => {
    useChatStore.setState({ toastQueue: [teamToast] });
    renderToasts();

    fireEvent.click(screen.getByRole('button', { name: /view/i }));

    expect(useChatStore.getState().toastQueue).toEqual([]);
  });

  test('an agent toast still switches the active agent', () => {
    // The pre-existing behaviour, pinned because the union rewrote its path.
    useChatStore.setState({ toastQueue: [agentToast] });
    renderToasts();

    fireEvent.click(screen.getByRole('button', { name: /view/i }));

    expect(useConfigStore.getState().agentId).toBe('a1');
    expect(navigate).not.toHaveBeenCalled();
  });

  test('a team and an agent toast with the same id do not dismiss each other', () => {
    // They are keyed by kind AND id. Sharing a key space would let a room
    // dismiss an agent's notification, or the reverse — and the symptom would
    // be a toast that vanishes for no reason anyone can reproduce.
    useChatStore.setState({
      toastQueue: [
        { kind: 'agent', agentId: 'x', agentName: 'Ada', timestamp: Date.now() },
        { kind: 'team', teamId: 'x', teamName: 'Desk', timestamp: Date.now() },
      ],
    });
    renderToasts();

    useChatStore.getState().dismissToast('team:x');

    expect(useChatStore.getState().toastQueue).toHaveLength(1);
    expect(useChatStore.getState().toastQueue[0].kind).toBe('agent');
  });
});
