/**
 * @file_name: chatStore.notify.test.ts
 * @description: Behavior contract for the OS-level completion notification
 * hook in chatStore.stopStreaming (#44).
 *
 * Key invariants:
 *   - reply completion while the window is unfocused fires the desktop
 *     notification (the in-app toast is invisible from another app)
 *   - a focused window suppresses it (the in-app UI is enough)
 *   - user-cancelled runs never notify
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/desktopNotify', () => ({
  notifyAgentReplyCompleted: vi.fn(),
}));

import { notifyAgentReplyCompleted } from '@/lib/desktopNotify';
import { useChatStore } from '../chatStore';

beforeEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

/** Put the agent into a live streaming turn so stopStreaming has work to do. */
function startTurn(agentId: string) {
  useChatStore.getState().startStreaming(agentId);
}

describe('stopStreaming desktop notification', () => {
  it('fires when the window is not focused', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    startTurn('agent_1');

    useChatStore.getState().stopStreaming('agent_1', 'Riley');

    expect(notifyAgentReplyCompleted).toHaveBeenCalledWith('Riley');
  });

  it('falls back to the agent id when no name is known', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    startTurn('agent_2');

    useChatStore.getState().stopStreaming('agent_2');

    expect(notifyAgentReplyCompleted).toHaveBeenCalledWith('agent_2');
  });

  it('does not fire when the window has focus', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    startTurn('agent_1');

    useChatStore.getState().stopStreaming('agent_1', 'Riley');

    expect(notifyAgentReplyCompleted).not.toHaveBeenCalled();
  });

  it('does not fire for a user-cancelled run', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    startTurn('agent_1');

    useChatStore.getState().stopStreaming('agent_1', 'Riley', { cancelled: true });

    expect(notifyAgentReplyCompleted).not.toHaveBeenCalled();
  });

  it('does not fire when no run was streaming (duplicate stop)', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    useChatStore.getState().stopStreaming('agent_idle', 'Riley');

    expect(notifyAgentReplyCompleted).not.toHaveBeenCalled();
  });
});
