/**
 * Mid-run steering (owner follow-up folded into a live run):
 * - run_started.steerable drives currentSteerable
 * - addSteerMessage adds an optimistic 'queued' bubble
 * - steer_consumed flips matching bubbles 'queued' → 'merged'
 * - steer_rejected marks the bubble 'rejected' (never left hanging)
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../chatStore';

vi.mock('@/lib/productAnalytics', () => ({ captureProductEvent: vi.fn() }));

const AGENT = 'agent_steer';

const bubbles = () =>
  useChatStore.getState().agentSessions[AGENT]?.messages ?? [];
const steerable = () =>
  useChatStore.getState().agentSessions[AGENT]?.currentSteerable;

describe('chatStore mid-run steering', () => {
  beforeEach(() => {
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  it('run_started carries steerability; startStreaming resets it', () => {
    expect(steerable()).toBe(false); // reset by startStreaming
    useChatStore.getState().processMessage(AGENT, {
      type: 'run_started', run_id: 'r1', steerable: true,
    });
    expect(steerable()).toBe(true);
    // A non-steerable run reports false.
    useChatStore.getState().startStreaming(AGENT);
    useChatStore.getState().processMessage(AGENT, {
      type: 'run_started', run_id: 'r2', steerable: false,
    });
    expect(steerable()).toBe(false);
  });

  it('a queued steer bubble flips to merged on steer_consumed', () => {
    useChatStore.getState().addSteerMessage(AGENT, 'also send me the summary', 'c1');
    const q = bubbles().find((m) => m.steerClientMsgId === 'c1');
    expect(q?.steerStatus).toBe('queued');

    useChatStore.getState().processMessage(AGENT, {
      type: 'steer_consumed', ids: ['c1', 'other'],
    });
    expect(bubbles().find((m) => m.steerClientMsgId === 'c1')?.steerStatus).toBe('merged');
  });

  it('steer_rejected marks the bubble rejected with a reason (not left queued)', () => {
    useChatStore.getState().addSteerMessage(AGENT, 'x'.repeat(10), 'c2');
    useChatStore.getState().processMessage(AGENT, {
      type: 'steer_rejected', client_msg_id: 'c2', reason: 'too_large',
    });
    const m = bubbles().find((b) => b.steerClientMsgId === 'c2');
    expect(m?.steerStatus).toBe('rejected');
    expect(m?.rejectReason).toBe('too_large');
  });

  it('markSteerRejected flips a bubble to rejected when the send never left the client', () => {
    // steer() returned false (socket no longer steerable) → no backend ack will
    // come, so the client marks the bubble locally instead of leaving it queued.
    useChatStore.getState().addSteerMessage(AGENT, 'never sent', 'c3');
    expect(bubbles().find((m) => m.steerClientMsgId === 'c3')?.steerStatus).toBe('queued');
    useChatStore.getState().markSteerRejected(AGENT, 'c3', 'not_sent');
    const m = bubbles().find((b) => b.steerClientMsgId === 'c3');
    expect(m?.steerStatus).toBe('rejected');
    expect(m?.rejectReason).toBe('not_sent');
  });

  it('markSteerRejected leaves an unrelated bubble untouched', () => {
    useChatStore.getState().addSteerMessage(AGENT, 'keep me', 'stay');
    useChatStore.getState().markSteerRejected(AGENT, 'someone_else', 'not_sent');
    expect(bubbles().find((m) => m.steerClientMsgId === 'stay')?.steerStatus).toBe('queued');
  });

  it('a steer bubble still queued when the run ends is swept to rejected/run_ended', () => {
    // The run finishes (or errors) before draining a late steer, so no
    // steer_consumed/steer_rejected will ever arrive for it. stopStreaming must
    // reconcile the still-'queued' bubble instead of leaving it hung forever.
    useChatStore.getState().addSteerMessage(AGENT, 'squeeze this in', 'late');
    expect(bubbles().find((m) => m.steerClientMsgId === 'late')?.steerStatus).toBe('queued');
    useChatStore.getState().stopStreaming(AGENT);
    const m = bubbles().find((b) => b.steerClientMsgId === 'late');
    expect(m?.steerStatus).toBe('rejected');
    expect(m?.rejectReason).toBe('run_ended');
  });

  it('stopStreaming does not overwrite a steer bubble that already resolved', () => {
    useChatStore.getState().addSteerMessage(AGENT, 'made it', 'ok');
    useChatStore.getState().processMessage(AGENT, { type: 'steer_consumed', ids: ['ok'] });
    useChatStore.getState().stopStreaming(AGENT);
    expect(bubbles().find((m) => m.steerClientMsgId === 'ok')?.steerStatus).toBe('merged');
  });

  it('steer_consumed leaves an unrelated bubble untouched', () => {
    useChatStore.getState().addSteerMessage(AGENT, 'a', 'keep');
    useChatStore.getState().processMessage(AGENT, {
      type: 'steer_consumed', ids: ['someone_else'],
    });
    expect(bubbles().find((m) => m.steerClientMsgId === 'keep')?.steerStatus).toBe('queued');
  });
});
