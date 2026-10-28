/**
 * The ⋯ menu offers the creation studio's Builder panel as a CONDITIONAL
 * item: only while the studio is open or resumable on this agent. With the
 * drawer closed this is the desktop's way back into a studio the user
 * collapsed — and a permanent entry would offer a panel the conversation
 * does not drive.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/stores', async () => ({
  // Real barrel underneath (the header reads the studio store from it); only
  // the two UI stores below are stubbed.
  ...(await vi.importActual<typeof import('@/stores')>('@/stores')),
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn(), requestPanel: vi.fn() }),
  useArtifactStore: (sel: (s: unknown) => unknown) => sel({ artifacts: [] }),
}));
vi.mock('@/stores/bookmarkStore', () => ({
  useBookmarkStore: (sel: (s: unknown) => unknown) => sel({ agents: {} }),
}));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('../ExecutionPopover', () => ({ ExecutionPopover: () => null }));

import { ChatHeader } from '../ChatHeader';
import { useStudioStore } from '@/stores/studioStore';

const props = {
  agentId: 'a1',
  agentName: 'Analyst',
  isStreaming: false,
  currentSteps: [],
  chatTab: 'conversation' as const,
  onChatTabChange: vi.fn(),
};

function openMenu() {
  render(
    <MemoryRouter>
      <ChatHeader {...props} />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: "Agent detail \u2014 panels" }));
}

beforeEach(() => {
  window.sessionStorage.clear();
  useStudioStore.setState({ open: {}, visited: {}, recommendations: {}, applyError: {} });
});

describe('chat header ⋯ menu — Builder entry', () => {
  it('is absent for an agent that never entered the studio', () => {
    openMenu();
    expect(screen.queryByRole('button', { name: /^Builder$/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^Workspace$/ })).toBeInTheDocument();
  });

  it('is offered while the studio is open, and after it was merely collapsed', () => {
    useStudioStore.getState().openStudio('a1');
    useStudioStore.getState().closeStudio('a1'); // collapsed, resumable
    openMenu();
    expect(screen.getByRole('button', { name: /^Builder$/ })).toBeInTheDocument();
  });

  it('disappears once the studio was finished with Done', () => {
    useStudioStore.getState().openStudio('a1');
    useStudioStore.getState().finishStudio('a1');
    openMenu();
    expect(screen.queryByRole('button', { name: /^Builder$/ })).toBeNull();
  });
});
