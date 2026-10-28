/**
 * M-1: the ⋯ menu's divider between the builtin panel entries and the
 * plugin (`ui.chatHeaderActions`) entries must not render when there are no
 * plugin entries — otherwise every user without a chat-header-action plugin
 * sees a dangling separator at the bottom of the menu.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/stores', async () => ({
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
import { CHAT_HEADER_ACTIONS } from '@/platform/registries';

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
  fireEvent.click(screen.getByRole('button', { name: 'Agent detail — panels' }));
}

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('chat header ⋯ menu — plugin-actions divider (M-1)', () => {
  it('is absent when no plugin registers a chatHeaderActions entry', () => {
    openMenu();
    expect(screen.queryByTestId('chat-header-plugin-divider')).toBeNull();
  });

  it('is present once a plugin registers a chatHeaderActions entry', () => {
    disposers.push(
      CHAT_HEADER_ACTIONS.register('acme.divider-test', { label: 'Do it', run: () => {} }, { owner: 'acme.plugin' }),
    );
    openMenu();
    expect(screen.getByTestId('chat-header-plugin-divider')).toBeInTheDocument();
  });
});
