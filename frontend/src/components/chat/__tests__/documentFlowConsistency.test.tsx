/**
 * One shape for live, settled and replayed turns.
 *
 * The whole point of this pass is that nothing rearranges when a turn lands or
 * when the page is refreshed: the streaming turn already renders the document
 * it will keep. `segmentTurn` is the one implementation behind all three paths,
 * so this pins that the RENDERED result agrees too — the invariant would
 * otherwise hold in the data and quietly break in the view.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SegmentedReply } from '../SegmentedReply';
import { segmentTurn, timelineToEvents } from '@/lib/segmentTurn';
import { useUIStore } from '@/stores/uiStore';
import type { EventLogTimelineEntry, TurnEvent } from '@/types';

const NARRATION = 'Reading the config first.';
const REASONING = 'Weighing the two install paths.';
const REPLY = 'It is enabled.';

/** The live event stream, as chatStore builds it. */
const liveEvents: TurnEvent[] = [
  { id: 'n1', ts: 1, type: 'thinking', content: NARRATION, monologue: true },
  { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
  { id: 'r1', ts: 3, type: 'thinking', content: REASONING },
  { id: 'y1', ts: 4, type: 'reply', content: REPLY },
];

/** The same turn as the backend persists and replays it. */
const replayedTimeline: EventLogTimelineEntry[] = [
  { type: 'thinking', content: NARRATION, monologue: true },
  { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
  { type: 'thinking', content: REASONING, monologue: false },
  { type: 'tool_call', tool_name: 'reply_owner', tool_input: { content: REPLY } },
];

/** What the reader actually ends up with: block kinds, in order. */
function shapeOf(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-testid], button, [class*="markdown"]'))
    .map((el) => {
      const id = el.getAttribute('data-testid');
      if (id?.startsWith('segment-reply-')) return 'reply';
      if (id?.startsWith('tool-row-')) return 'tool';
      if (el.tagName === 'BUTTON' && el.getAttribute('aria-expanded') !== null) return 'reasoning';
      if (el.className.toString().includes('markdown-progress')) return 'narration';
      return '';
    })
    .filter(Boolean);
}

describe('live, settled and replayed turns render the same document', () => {
  beforeEach(() => useUIStore.setState({ interimNarration: true }));

  it('the streaming turn is already in its settled shape', () => {
    const live = render(
      <SegmentedReply segments={segmentTurn(liveEvents)} showProcess isStreaming />,
    );
    const streamingShape = shapeOf(live.container);
    live.unmount();

    const settled = render(
      <SegmentedReply segments={segmentTurn(liveEvents)} showProcess />,
    );
    expect(shapeOf(settled.container)).toEqual(streamingShape);
  });

  it('a refreshed turn matches the live one', () => {
    const live = render(<SegmentedReply segments={segmentTurn(liveEvents)} showProcess />);
    const liveShape = shapeOf(live.container);
    live.unmount();

    const replayed = render(
      <SegmentedReply segments={segmentTurn(timelineToEvents(replayedTimeline))} showProcess />,
    );
    expect(shapeOf(replayed.container)).toEqual(liveShape);
  });

  it('the rhythm is narration → tool → reasoning → reply, in that order', () => {
    // The acceptance shape: the agent says what it is about to do, does it,
    // and the answer lands underneath — reasoning folded out of the way.
    const { container } = render(
      <SegmentedReply segments={segmentTurn(liveEvents)} showProcess />,
    );
    expect(shapeOf(container)).toEqual(['narration', 'tool', 'reasoning', 'reply']);
  });

  it('renders the opening narration before any reply exists', () => {
    // The live block used to render nothing until a reply had content (it was
    // "answers only, in a bubble"). Removing that gate is the point: the first
    // thing the user should see is the agent saying what it is about to do,
    // BEFORE the tool runs. A turn with no reply yet must still draw.
    const opening: TurnEvent[] = [
      { id: 'n1', ts: 1, type: 'thinking', content: NARRATION, monologue: true },
    ];
    render(<SegmentedReply segments={segmentTurn(opening)} showProcess isStreaming />);

    expect(screen.getByText(NARRATION)).toBeInTheDocument();
    expect(screen.queryByTestId('segment-reply-0')).toBeNull();
  });

  it('every character survives the reshape (#16)', () => {
    const { container } = render(
      <SegmentedReply segments={segmentTurn(liveEvents)} showProcess />,
    );
    // Narration, tool name and reply read without a click; reasoning is
    // behind its own toggle, which is visible-with-one-click, not dropped.
    expect(screen.getByText(NARRATION)).toBeInTheDocument();
    expect(screen.getByText('bash')).toBeInTheDocument();
    expect(screen.getByText(REPLY)).toBeInTheDocument();
    expect(container.textContent).not.toContain(REASONING);
    expect(screen.getAllByRole('button', { expanded: false }).length).toBeGreaterThan(0);
  });
});
