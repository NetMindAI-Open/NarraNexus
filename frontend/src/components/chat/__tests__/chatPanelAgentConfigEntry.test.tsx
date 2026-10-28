/**
 * @file_name: chatPanelAgentConfigEntry.test.tsx
 * @author:
 * @date: 2026-09-11
 * @description: The chat view's own door to the agent's model & framework
 * configuration (Owner-required, reinstated after #383 removed it): the
 * owner gets a header button that opens AgentLlmConfigPanel for THIS agent,
 * a save refreshes the composer model chip, and a viewer who does not own the
 * agent gets no button (the llm-config routes would 403 them).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAgentWebSocket: () => ({
      run: vi.fn(),
      reconnect: vi.fn(),
      stop: vi.fn(),
      steer: vi.fn(() => true),
      isLoading: false,
    }),
    useFastMode: () => [false, vi.fn()],
  };
});

vi.mock('@/lib/api', () => ({
  api: {
    getSimpleChatHistory: vi.fn().mockResolvedValue({ success: true, messages: [], total_count: 0 }),
    getTranscriptionAvailability: vi.fn().mockResolvedValue({ available: false, reason: '' }),
    getAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
  },
}));

// The real panel is covered by its own tests; here only the wiring matters.
vi.mock('../AgentLlmConfigPanel', () => ({
  AgentLlmConfigPanel: (p: { agentId: string; isOpen: boolean; onSaved?: () => void }) =>
    p.isOpen ? (
      <div data-testid="llm-config-panel">
        {p.agentId}
        <button type="button" onClick={() => p.onSaved?.()}>stub-save</button>
      </div>
    ) : null,
}));
vi.mock('../ComposerModelBadge', () => ({
  ComposerModelBadge: (p: { reloadKey?: number }) => <span data-testid="model-badge">{`reload-${p.reloadKey ?? 0}`}</span>,
}));

import { ChatPanel } from '../ChatPanel';
import { useConfigStore, useChatStore } from '@/stores';

const AGENT = 'a1';

function seed(createdBy: string) {
  useChatStore.setState({ agentSessions: {}, activeAgentId: AGENT });
  useConfigStore.setState({
    agentId: AGENT,
    userId: 'u1',
    agents: [{ agent_id: AGENT, name: 'Analyst', created_by: createdBy } as never],
    refreshAgents: vi.fn().mockResolvedValue(undefined),
  });
}

describe('ChatPanel — Model & framework entry', () => {
  beforeEach(() => seed('u1'));

  it('the owner opens the config panel for this agent from the header', () => {
    render(<ChatPanel />, { wrapper: MemoryRouter });
    expect(screen.queryByTestId('llm-config-panel')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Model & framework' }));
    expect(screen.getByTestId('llm-config-panel').textContent).toContain(AGENT);
  });

  it('a save refreshes the composer model chip', () => {
    render(<ChatPanel />, { wrapper: MemoryRouter });
    expect(screen.getByTestId('model-badge').textContent).toBe('reload-0');
    fireEvent.click(screen.getByRole('button', { name: 'Model & framework' }));
    fireEvent.click(screen.getByRole('button', { name: 'stub-save' }));
    expect(screen.getByTestId('model-badge').textContent).toBe('reload-1');
  });

  it('someone else\'s agent gets no config button', () => {
    seed('someone-else');
    render(<ChatPanel />, { wrapper: MemoryRouter });
    expect(screen.queryByRole('button', { name: 'Model & framework' })).toBeNull();
  });
});
