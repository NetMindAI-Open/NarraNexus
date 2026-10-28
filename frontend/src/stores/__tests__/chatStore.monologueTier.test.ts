/**
 * Monologue tier: NexusPower's own assistant text rides the thinking lane but
 * is NOT the same tier as provider chain-of-thought — the frontend renders it
 * at the "progress" tier.
 *
 * Two of the three red lines are pinned here:
 *  - Red line 1: monologue must NOT be routed onto the native_output lane.
 *    chatStore's `alreadyReplied` guard drops every agent_response after a
 *    reply, so that shortcut would silently eat the back half of a turn's
 *    narration (iron rule #16: never reduce what the user can see). Asserted
 *    by keeping monologue that arrives AFTER a reply.
 *  - Red line 2: zero content change. Same bytes, different tier only.
 *
 * A mixed frame (thinking_content is the union, monologue only a subset) has
 * no recoverable split point, so it falls back to plain thinking — better to
 * leave narration among the CoT than to promote provider scratchpad.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { AgentThinking, ProgressMessage } from '@/types';

const AGENT = 'agent_monologue_tier';

const thinking = (content: string, monologue?: string): AgentThinking => ({
  type: 'agent_thinking',
  timestamp: 1000,
  thinking_content: content,
  ...(monologue === undefined ? {} : { monologue }),
});

/** Tool rows reach `currentEvents` through PROGRESS frames (step 3.4.x with
 *  details.tool_name + arguments), not through bare `tool_call` messages —
 *  those only feed `currentToolCalls`. */
const toolProgress = (
  toolName: string,
  args: Record<string, unknown>,
  callId: string,
): ProgressMessage => ({
  type: 'progress',
  step: '3.4.1',
  title: 'tool',
  description: '',
  status: 'running',
  substeps: [],
  timestamp: 1200,
  details: { tool_name: toolName, tool_call_id: callId, arguments: args },
});

const replyToolCall = () =>
  toolProgress('reply_owner', { content: 'Done — the plugin is enabled.' }, 'call_reply');

const plainToolCall = () => toolProgress('bash', { command: 'ls' }, 'call_bash');

const thinkingEvents = () =>
  useChatStore
    .getState()
    .agentSessions[AGENT].currentEvents.filter((e) => e.type === 'thinking') as Array<{
    type: 'thinking';
    content: string;
    monologue?: boolean;
  }>;

describe('chatStore monologue tier', () => {
  beforeEach(() => {
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  it('marks a pure monologue frame as the progress tier', () => {
    useChatStore.getState().processMessage(AGENT, thinking('Checking the plugin state.', 'Checking the plugin state.'));

    expect(thinkingEvents()).toEqual([
      expect.objectContaining({ content: 'Checking the plugin state.', monologue: true }),
    ]);
  });

  it('leaves a provider chain-of-thought frame off the progress tier', () => {
    useChatStore.getState().processMessage(AGENT, thinking('The user probably means...'));

    expect(thinkingEvents()).toEqual([
      expect.objectContaining({ content: 'The user probably means...', monologue: false }),
    ]);
  });

  it('falls back to plain thinking on a mixed frame (subset is not the whole)', () => {
    useChatStore.getState().processMessage(AGENT, thinking('CoT preamble. Then I speak.', 'Then I speak.'));

    expect(thinkingEvents()).toEqual([
      expect.objectContaining({ content: 'CoT preamble. Then I speak.', monologue: false }),
    ]);
  });

  it('still coalesces consecutive same-tier frames into one bubble', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('Check', 'Check'));
    store.processMessage(AGENT, thinking('ing the state.', 'ing the state.'));

    expect(thinkingEvents()).toEqual([
      expect.objectContaining({ content: 'Checking the state.', monologue: true }),
    ]);
  });

  it('treats a tier switch as a block boundary', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('Official support confirmed.', 'Official support confirmed.'));
    store.processMessage(AGENT, thinking('Though the version matters here...'));
    store.processMessage(AGENT, thinking('Checking your machine now.', 'Checking your machine now.'));

    expect(thinkingEvents().map((e) => [e.content, e.monologue])).toEqual([
      ['Official support confirmed.', true],
      ['Though the version matters here...', false],
      ['Checking your machine now.', true],
    ]);
  });

  it('red line 1: monologue after a reply still renders (must not use the native_output lane)', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('Replying first.', 'Replying first.'));
    store.processMessage(AGENT, replyToolCall());
    store.processMessage(AGENT, thinking('Now wrapping up.', 'Now wrapping up.'));

    // alreadyReplied only drops agent_response; monologue rides the thinking
    // lane and must survive.
    expect(thinkingEvents().map((e) => [e.content, e.monologue])).toEqual([
      ['Replying first.', true],
      ['Now wrapping up.', true],
    ]);
  });

  it('keeps the existing tool-call boundary between narration blocks', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('Taking a look.', 'Taking a look.'));
    store.processMessage(AGENT, plainToolCall());
    store.processMessage(AGENT, thinking('Found it.', 'Found it.'));

    expect(thinkingEvents().map((e) => [e.content, e.monologue])).toEqual([
      ['Taking a look.', true],
      ['Found it.', true],
    ]);
  });

  it('a replayed prefix and its live continuation stay ONE block', () => {
    // Mid-run refresh: the reconnect replays the in-flight segment, then the
    // live stream continues it. Both now carry the tier (run_recorder tags
    // the segment, broadcaster carries the partial, wsManager translates it),
    // so they merge instead of tearing the sentence at the reconnect.
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('Checking the plug', 'Checking the plug'));
    store.processMessage(AGENT, thinking('in state now.', 'in state now.'));

    expect(thinkingEvents().map((e) => [e.content, e.monologue])).toEqual([
      ['Checking the plugin state now.', true],
    ]);
  });

  it('a replayed reasoning prefix stays receded and does not absorb narration', () => {
    // The other direction: a replayed CoT segment followed by narration is a
    // genuine tier switch, so it opens a new block rather than promoting the
    // scratchpad.
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinking('weighing options'));
    store.processMessage(AGENT, thinking('Reading the file now.', 'Reading the file now.'));

    expect(thinkingEvents().map((e) => [e.content, e.monologue])).toEqual([
      ['weighing options', false],
      ['Reading the file now.', true],
    ]);
  });

  it('red line 2: tiering changes no content — every thinking byte survives verbatim', () => {
    const store = useChatStore.getState();
    const sent = ['Official support confirmed.', 'Though the version matters...', 'Checking your machine now.'];
    store.processMessage(AGENT, thinking(sent[0], sent[0]));
    store.processMessage(AGENT, thinking(sent[1]));
    store.processMessage(AGENT, thinking(sent[2], sent[2]));

    expect(thinkingEvents().map((e) => e.content).join('')).toBe(sent.join(''));
  });
});
