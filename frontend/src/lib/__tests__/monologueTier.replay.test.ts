/**
 * Red line 3: refresh consistency.
 *
 * `segmentTurn` is built on the invariant "the live view and the post-refresh
 * view agree — one implementation, not two that happen to match". If the tier
 * existed only on the live path, the same turn would drop back to plain
 * thinking after a reload; carrying it through the backend timeline is exactly
 * why chat_history passes `monologue` at all.
 *
 * Pinned here:
 *  1. `timelineToEvents` carries the replayed tier onto the TurnEvent;
 *  2. for one turn, the live and replayed thinking blocks give the same
 *     (content, tier) sequence.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { timelineToEvents } from '../segmentTurn';
import { isMonologueFrame } from '../monologueTier';
import { useChatStore } from '@/stores/chatStore';
import type { AgentThinking, EventLogTimelineEntry, ProgressMessage, TurnEvent } from '@/types';

const AGENT = 'agent_replay_tier';

const thinkingFrame = (content: string, monologue?: string): AgentThinking => ({
  type: 'agent_thinking',
  timestamp: 1000,
  thinking_content: content,
  ...(monologue === undefined ? {} : { monologue }),
});

const toolProgress = (): ProgressMessage => ({
  type: 'progress',
  step: '3.4.1',
  title: 'tool',
  description: '',
  status: 'running',
  substeps: [],
  timestamp: 1200,
  details: { tool_name: 'bash', tool_call_id: 'call_1', arguments: { command: 'ls' } },
});

const tiers = (events: TurnEvent[]) =>
  events
    .filter((e): e is Extract<TurnEvent, { type: 'thinking' }> => e.type === 'thinking')
    .map((e) => [e.content, !!e.monologue] as const);

describe('isMonologueFrame', () => {
  it('is true for a pure monologue frame', () => {
    expect(isMonologueFrame('Saying something.', 'Saying something.')).toBe(true);
  });

  it('is false with no monologue subset (provider chain-of-thought)', () => {
    expect(isMonologueFrame('Thinking it over.', undefined)).toBe(false);
    expect(isMonologueFrame('Thinking it over.', '')).toBe(false);
  });

  it('is false for a mixed frame (subset is not the whole) — miss a promotion rather than make a wrong one', () => {
    expect(isMonologueFrame('CoT preamble. Then I speak.', 'Then I speak.')).toBe(false);
  });
});

describe('timelineToEvents carries the tier', () => {
  it('turns a replayed monologue=true row into a progress-tier event', () => {
    const events = timelineToEvents([
      { type: 'thinking', content: 'Checking the state.', monologue: true },
      { type: 'thinking', content: 'Hmm...', monologue: false },
    ] as EventLogTimelineEntry[]);

    expect(tiers(events)).toEqual([
      ['Checking the state.', true],
      ['Hmm...', false],
    ]);
  });

  it('renders legacy rows without the field as plain thinking, without throwing', () => {
    const events = timelineToEvents([{ type: 'thinking', content: 'old row' }]);

    expect(tiers(events)).toEqual([['old row', false]]);
  });
});

describe('live and replayed views of one turn agree', () => {
  beforeEach(() => {
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  // Scope, stated rather than implied: this equivalence is verified at the
  // tool_call boundary only. The two paths already differ at a native_output
  // boundary (not introduced here): chatStore's backward scan breaks only on
  // tool_call/reply and merges ACROSS native_output, while
  // `_build_event_timeline` flushes on it. That is a block-splitting
  // difference, unrelated to tiering, but it lands on the invariant this
  // change claims — tracked separately, not papered over here.
  it('think(monologue) -> tool -> think(monologue) gives the same tier sequence both ways', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, thinkingFrame('Official support confirmed.', 'Official support confirmed.'));
    store.processMessage(AGENT, toolProgress());
    store.processMessage(AGENT, thinkingFrame('Checking your machine now.', 'Checking your machine now.'));

    const live = tiers(useChatStore.getState().agentSessions[AGENT].currentEvents);

    // The same turn persisted to event_log and replayed once chat_history
    // passes the tier through:
    const replayed = tiers(
      timelineToEvents([
        { type: 'thinking', content: 'Official support confirmed.', monologue: true },
        { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
        { type: 'thinking', content: 'Checking your machine now.', monologue: true },
      ] as EventLogTimelineEntry[]),
    );

    expect(replayed).toEqual(live);
  });
});
