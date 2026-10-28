/**
 * The Activity Log tab is a READ-ONLY surface: it shows runs the agent started
 * on its own, and there is no reply channel to type into. So the whole composer
 * footer — textarea, send button, attach/voice row, model badge — must not
 * render there, and the Card-root drag handlers must not accept a dropped file
 * either (nothing would show the resulting attachment chip).
 *
 * Guarding both halves here: hiding only the textarea while leaving the intake
 * path live is exactly the drift this test exists to catch.
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

// hoisted so the vi.mock factory (lifted above these lines) can close over it.
const h = vi.hoisted(() => ({ uploadAttachment: vi.fn() }));

vi.mock('@/lib/api', () => ({
  api: {
    getSimpleChatHistory: vi.fn().mockResolvedValue({ success: true, messages: [], total_count: 0 }),
    getTranscriptionAvailability: vi.fn().mockResolvedValue({ available: false, reason: '' }),
    uploadAttachment: h.uploadAttachment,
  },
}));

import { ChatPanel } from '../ChatPanel';
import { useConfigStore, useChatStore } from '@/stores';

const AGENT = 'a1';

// Both the header toggle and the mobile tab row carry this label (CSS decides
// which is visible); either one flips the same state, so take the first.
function switchTo(label: string) {
  fireEvent.click(screen.getAllByRole('button', { name: label })[0]);
}

// The drop target is the Card root (users drag files anywhere in the panel,
// not just onto the box), which is the rendered tree's outermost element.
function dropFileOnPanelRoot(container: HTMLElement) {
  const file = new File(['x'], 'note.txt', { type: 'text/plain' });
  const dataTransfer = { files: [file], types: ['Files'], dropEffect: 'none' };
  fireEvent.drop(container.firstElementChild!, { dataTransfer });
}

describe('ChatPanel — Activity Log tab has no composer', () => {
  beforeEach(() => {
    h.uploadAttachment.mockClear();
    useChatStore.setState({ agentSessions: {}, activeAgentId: AGENT });
    useConfigStore.setState({
      agentId: AGENT,
      userId: 'u1',
      agents: [{ agent_id: AGENT, name: 'Analyst' } as never],
    });
  });

  it('renders the composer on the conversation tab and drops it on the Activity Log tab', () => {
    render(<ChatPanel />, { wrapper: MemoryRouter });

    // Conversation tab: composer present.
    expect(screen.getByRole('textbox')).toBeTruthy();
    expect(screen.getByTitle('Send (Enter)')).toBeTruthy();
    expect(screen.getByTitle('Attach file')).toBeTruthy();

    switchTo('Activity Log');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByTitle('Send (Enter)')).toBeNull();
    expect(screen.queryByTitle('Attach file')).toBeNull();

    // And back — the footer returns (state, not a one-way teardown).
    switchTo('Conversation');
    expect(screen.getByRole('textbox')).toBeTruthy();
  });

  it('ignores a file dropped on the Activity Log tab instead of queueing an invisible attachment', () => {
    const { container } = render(<ChatPanel />, { wrapper: MemoryRouter });
    // Baseline: the same drop IS accepted on the conversation tab, so the
    // assertion below can't pass just because the drop never reached a handler.
    dropFileOnPanelRoot(container);
    expect(h.uploadAttachment).toHaveBeenCalledTimes(1);

    h.uploadAttachment.mockClear();
    switchTo('Activity Log');
    dropFileOnPanelRoot(container);
    expect(h.uploadAttachment).not.toHaveBeenCalled();
  });
});
