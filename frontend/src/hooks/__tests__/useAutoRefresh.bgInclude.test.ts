/**
 * @file_name: useAutoRefresh.bgInclude.test.ts
 * @description: The background "new message" poll must read the CHAT stream only.
 *
 * The Activity Log (A2A/team activity) now shares simple-chat-history with the
 * conversation. tickBgMessages uses the newest row's timestamp as "this agent
 * replied to you" → toast + sidebar badge. If it polled the merged/activity
 * stream, every peer/team turn would fire a false "replied to you". So it must
 * request include='chat'. This pins that argument.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const BG_MESSAGE_INTERVAL = 15_000;

// vi.hoisted so the (hoisted) vi.mock factory can reference it without a TDZ.
const { getSimpleChatHistory } = vi.hoisted(() => ({
  getSimpleChatHistory: vi.fn(async () => ({
    success: true,
    messages: [{ timestamp: '2026-08-21T08:00:00Z' }],
  })),
}));

vi.mock('@/stores', () => ({
  useTeamsStore: { getState: () => ({ refresh: vi.fn(async () => {}), teams: [] }) },
  useConfigStore: {
    getState: () => ({ refreshAgents: vi.fn(), agents: [{ agent_id: 'a1', name: 'A' }] }),
  },
  useChatStore: { getState: () => ({ isAgentStreaming: () => false }) },
  useArtifactStore: (select: (s: unknown) => unknown) => select({ loadPinned: vi.fn() }),
  usePreloadStore: () => ({
    refreshAgentInbox: vi.fn(),
    refreshJobs: vi.fn(),
    refreshAwareness: vi.fn(),
    refreshChatHistory: vi.fn(),
    refreshSocialNetwork: vi.fn(),
  }),
}));

vi.mock('@/lib/api', () => ({ api: { getSimpleChatHistory } }));

import { useAutoRefresh } from '../useAutoRefresh';

beforeEach(() => {
  getSimpleChatHistory.mockClear();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('background message poll stream', () => {
  test("tickBgMessages polls include='chat', not the activity stream", async () => {
    renderHook(() => useAutoRefresh({ agentId: 'other', userId: 'usr_1' }));

    await vi.advanceTimersByTimeAsync(BG_MESSAGE_INTERVAL);

    expect(getSimpleChatHistory).toHaveBeenCalled();
    // Non-vacuous: drop the 'chat' argument (falls back to 'all') and this fails.
    expect(getSimpleChatHistory).toHaveBeenCalledWith('a1', 5, 0, 'chat');
  });
});
