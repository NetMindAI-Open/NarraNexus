/**
 * @file_name: chatViewArtifactsDefault.test.tsx
 * @author:
 * @date: 2026-09-11
 * @description: Owner 2026-09-11 — a new agent's chat opens with the drawer on
 * the Artifacts panel, pinned, so its explainer is visible from the first
 * view; for existing users too (the global first-run coach only ever reached
 * brand-new profiles). After that first view the agent keeps whatever drawer
 * state the user leaves it in; phones never auto-open.
 */

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/components/chat', () => ({ ChatPanel: () => <div data-testid="chat-panel" /> }));
vi.mock('@/components/chat/WakingOverlay', () => ({ WakingOverlay: () => null }));
vi.mock('@/components/onboarding/GuideAgentCoachmark', () => ({ GuideAgentCoachmark: () => null }));
vi.mock('@/components/onboarding/MigrationGuide', () => ({ MigrationGuide: () => null }));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('@/hooks/useBookmarkSignals', () => ({ useBookmarkSignals: () => undefined }));
vi.mock('@/components/bookmarks/BookmarkPanelHost', () => ({
  BookmarkPanelHost: ({ tab }: { tab: string }) => <div data-testid={`panel-${tab}`} />,
}));
vi.mock('@/hooks', async (orig) => ({
  ...(await orig<typeof import('@/hooks')>()),
  useAutoRefresh: () => ({ refreshAll: vi.fn() }),
}));

import { ChatView } from '../MainLayout';
import {
  DRAWER_AGENT_SEEN_KEY,
  DRAWER_FIRST_RUN_KEY,
  DRAWER_OPENED_ONCE_KEY,
  DRAWER_PINNED_KEY,
} from '../drawerLayout';
import { useConfigStore, useArtifactStore } from '@/stores';

const originalMatchMedia = window.matchMedia;

function renderView() {
  return render(
    <MemoryRouter>
      <ChatView />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // An EXISTING user: the global first-run coach is spent and the drawer has
  // been used before — the case the Owner hit (new agents showed nothing).
  window.localStorage.setItem(DRAWER_FIRST_RUN_KEY, '1');
  window.localStorage.setItem(DRAWER_OPENED_ONCE_KEY, '1');
  useConfigStore.setState({ agentId: 'agent_new', userId: 'user_1' });
  useArtifactStore.setState({ loadPinned: vi.fn() } as never);
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

describe('ChatView — Artifacts open and pinned on a new agent', () => {
  test('a never-shown agent opens on Artifacts, pinned', () => {
    renderView();
    expect(screen.getByTestId('panel-artifacts')).toBeTruthy();
    // Pinned drawer offers "Unpin" (a transient one offers "Pin").
    expect(screen.getByRole('button', { name: /unpin/i })).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem(DRAWER_AGENT_SEEN_KEY)!)).toEqual(['agent_new']);
  });

  test('an agent already shown keeps the state the user left (closed stays closed)', () => {
    window.localStorage.setItem(DRAWER_AGENT_SEEN_KEY, JSON.stringify(['agent_new']));
    renderView();
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();
  });

  test('switching to another new agent opens Artifacts again; back to a seen one does not re-open', () => {
    window.localStorage.setItem(DRAWER_AGENT_SEEN_KEY, JSON.stringify(['agent_old']));
    useConfigStore.setState({ agentId: 'agent_old' });
    renderView();
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();

    act(() => useConfigStore.setState({ agentId: 'agent_new' }));
    expect(screen.getByTestId('panel-artifacts')).toBeTruthy();

    // The user closes it, goes back to the old agent and returns: no re-open.
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();
    act(() => useConfigStore.setState({ agentId: 'agent_old' }));
    act(() => useConfigStore.setState({ agentId: 'agent_new' }));
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();
  });

  test('an explicit unpin is respected — the new agent opens Artifacts unpinned', () => {
    window.localStorage.setItem(DRAWER_PINNED_KEY, '0');
    renderView();
    expect(screen.getByTestId('panel-artifacts')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^pin/i })).toBeTruthy();
  });

  test('a phone neither opens the drawer nor spends the agent\'s desktop first view', () => {
    window.matchMedia = ((query: string) => ({
      matches: true, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    renderView();
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();
    expect(window.localStorage.getItem(DRAWER_AGENT_SEEN_KEY)).toBeNull();
  });
});
