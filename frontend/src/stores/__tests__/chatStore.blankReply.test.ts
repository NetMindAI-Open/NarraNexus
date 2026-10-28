/**
 * Blank replies never become bubbles: when reply_owner
 * carries whitespace-only content ("\n"), stopStreaming falls through to
 * the placeholder branch — the same line the backend persist_turn
 * strip guard draws. Otherwise the session shows a blank bubble that
 * vanishes on refresh (the DB side is caught by the strip guard, the
 * session side let it through via filter(Boolean)).
 *
 * Also covers the sibling branch: a turn with no reply that the USER
 * cancelled must not be labeled the same as one the agent silently chose
 * not to answer (GitHub #87) — the two are different events for the
 * reader, and only one of them is a decision the agent actually made.
 * The markers are the literal strings the backend persists; see
 * lib/turnMarkers.ts.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../chatStore';
import type { ProgressMessage } from '@/types';

const { captureProductEvent } = vi.hoisted(() => ({
  captureProductEvent: vi.fn(),
}));
vi.mock('@/lib/productAnalytics', () => ({ captureProductEvent }));

const AGENT = 'agent_blank_reply';

const replyProgress = (content: string, id: string, ts = 1000): ProgressMessage => ({
  type: 'progress',
  step: '3.4.1',
  title: 'reply_owner',
  description: '',
  status: 'completed',
  substeps: [],
  // Distinct timestamps: currentToolCalls dedups on tool_name+timestamp.
  timestamp: ts,
  details: {
    tool_name: 'mcp__chat_module__reply_owner',
    tool_call_id: id,
    arguments: { content },
  },
});

describe('chatStore blank reply guard', () => {
  beforeEach(() => {
    captureProductEvent.mockClear();
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  it('stopStreaming: whitespace-only reply falls through to the placeholder', () => {
    useChatStore.getState().processMessage(AGENT, replyProgress('\n', 'c1'));
    useChatStore.getState().stopStreaming(AGENT);
    const messages = useChatStore.getState().agentSessions[AGENT].messages;
    const last = messages[messages.length - 1];
    expect(last.role).toBe('assistant');
    expect(last.content).toBe('(Agent decided no response needed)');
  });

  it('stopStreaming: whitespace parts are dropped, real reply survives', () => {
    useChatStore.getState().processMessage(AGENT, replyProgress('   ', 'c1'));
    useChatStore.getState().processMessage(AGENT, replyProgress('real reply', 'c2', 2000));
    useChatStore.getState().stopStreaming(AGENT);
    const messages = useChatStore.getState().agentSessions[AGENT].messages;
    expect(messages[messages.length - 1].content).toBe('real reply');
  });

  it('a cancelled event with no reply settles on the interrupted marker, not "no response needed"', () => {
    // GitHub #87: stopping a turn mid-way used to fall into the same
    // placeholder as a turn the agent genuinely chose not to answer —
    // implying a decision the agent never made. Driven through the real
    // WS event so the 'cancelled' case's wiring is under test too. The
    // content is the backend's persisted marker (chat_module.py), so the
    // live bubble and its reloaded history row agree; MessageBubble
    // localizes it.
    useChatStore.getState().processMessage(AGENT, { type: 'cancelled' });
    const session = useChatStore.getState().agentSessions[AGENT];
    expect(session.isStreaming).toBe(false);
    const last = session.messages[session.messages.length - 1];
    expect(last.role).toBe('assistant');
    expect(last.content).toBe('(Interrupted by user)');
  });

  it('stopStreaming: an uncancelled turn with no reply keeps the existing label', () => {
    useChatStore.getState().stopStreaming(AGENT);
    const messages = useChatStore.getState().agentSessions[AGENT].messages;
    expect(messages[messages.length - 1].content).toBe('(Agent decided no response needed)');
  });

  it('complete records reply_rendered only for a nonblank user-facing reply', () => {
    useChatStore.getState().processMessage(AGENT, replyProgress('real reply', 'c3'));
    useChatStore.getState().processMessage(AGENT, { type: 'complete' });
    expect(captureProductEvent).toHaveBeenCalledWith(
      'reply_rendered',
      expect.objectContaining({ agent_id: AGENT }),
    );

    captureProductEvent.mockClear();
    useChatStore.getState().startStreaming(AGENT);
    useChatStore.getState().processMessage(AGENT, replyProgress('  ', 'c4', 3000));
    useChatStore.getState().processMessage(AGENT, { type: 'complete' });
    expect(captureProductEvent).not.toHaveBeenCalled();
  });
});
