/**
 * SegmentedReply — one turn that spoke m times renders as m bubbles, each
 * carrying the process that led to it. `showProcess` gates whether the
 * process draws at all — the chat now passes it in both directions, but the
 * off case stays a supported contract for callers that want answers only.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SegmentedReply } from '../SegmentedReply';
import type { Segment } from '@/types';
import { useUIStore } from '@/stores/uiStore';

/** Reasoning + tools, NO narration — the shape a claude/codex driver produces. */
const segments: Segment[] = [
  {
    process: [{ id: 't1', ts: 1, type: 'thinking', content: 'check the material' }],
    reply: { content: 'starting' },
  },
  {
    process: [
      { id: 'c2', ts: 2, type: 'tool_call', tool_name: 'bash', tool_input: {} },
      { id: 'o2', ts: 3, type: 'tool_output', tool_name: 'bash', output: 'ok' },
    ],
    reply: { content: 'done' },
  },
];

beforeEach(() => useUIStore.setState({ interimNarration: true }));

describe('SegmentedReply', () => {
  it('renders m bubbles for m replies', () => {
    render(<SegmentedReply segments={segments} />);
    expect(screen.getByText('starting')).toBeInTheDocument();
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('there is ONE shape: the process is in the flow, with no drawer, either way', () => {
    // The preference controls tone, never layout (2026-08-30 second pass) —
    // keeping a drawer for some turns meant two transcript shapes in one
    // product, and it hid the narration this whole branch exists to surface.
    useUIStore.setState({ interimNarration: false });
    render(<SegmentedReply segments={segments} showProcess />);
    expect(screen.getAllByTestId(/segment-details-/)).toHaveLength(2);
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.queryByText(/Reasoning & tools/i)).toBeNull();
  });

  it('preference on + the turn narrates: process is inline, with no outer drawer', () => {
    const narrated: Segment[] = [{
      process: [{ id: 't1', ts: 1, type: 'thinking', content: 'check the material', monologue: true }],
      reply: { content: 'starting' },
    }];
    render(<SegmentedReply segments={narrated} showProcess />);
    // The segment container is still there (the process renders inside it),
    // but there is no "Reasoning & tools (N)" master toggle — which is the
    // whole point: readable without clicking anything.
    expect(screen.getAllByTestId(/segment-details-/)).toHaveLength(1);
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.getByText('check the material')).toBeInTheDocument();
  });

  it('a turn that never narrates gets the same shape (claude/codex emit no monologue)', () => {
    // Its reasoning is collapsed rather than drawered — one click, same as
    // everywhere else, instead of a second shape for a whole class of agents.
    render(<SegmentedReply segments={segments} showProcess />);
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.queryByText('check the material')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { expanded: false })[0]);
    expect(screen.getByText('check the material')).toBeInTheDocument();
  });

  it('promotion is decided per TURN, not per segment', () => {
    // A turn whose first segment narrates and whose second does not must not
    // render half inline and half behind a drawer — two shapes in one reply
    // reads as a rendering bug.
    const mixed: Segment[] = [
      {
        process: [{ id: 't1', ts: 1, type: 'thinking', content: 'narrating here', monologue: true }],
        reply: { content: 'first' },
      },
      {
        process: [{ id: 't2', ts: 2, type: 'thinking', content: 'reasoning only' }],
        reply: { content: 'second' },
      },
    ];
    render(<SegmentedReply segments={mixed} showProcess />);
    // Neither segment shows the drawer's count label.
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.getByText('narrating here')).toBeInTheDocument();
  });

  it('showProcess off draws answers only, streaming or not', () => {
    render(<SegmentedReply segments={segments} isStreaming />);
    expect(screen.queryByTestId(/segment-details-/)).toBeNull();
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('a segment with no reply renders no bubble, but keeps its process', () => {
    const noReply: Segment[] = [
      { process: [{ id: 't1', ts: 1, type: 'thinking', content: 'thought about it' }], reply: null },
    ];
    render(<SegmentedReply segments={noReply} showProcess />);
    expect(screen.queryByTestId('segment-reply-0')).toBeNull();
    expect(screen.getByTestId('segment-details-0')).toBeInTheDocument();
  });

  // The helper_llm recovery badges used to live on TurnTimeline's ReplyBlock;
  // they moved here with the answer tier (legacy tag kept — persisted rows
  // from before the rename are not backfilled).
  it('helper_llm_no_reply shows the info badge', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Recovered reply', via: 'helper_llm_no_reply' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });

  it('helper_llm_after_error shows the warning badge and not the info one', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Partial recovery', via: 'helper_llm_after_error' } },
    ]} />);
    expect(screen.getByText(/recovered after error/i)).toBeInTheDocument();
    expect(screen.queryByText(/helper_llm fallback/i)).toBeNull();
  });

  it('the legacy helper_llm_fallback tag still shows the info badge', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Legacy recovered reply', via: 'helper_llm_fallback' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });
});

describe('SegmentedReply streaming render path', () => {
  // Re-parsing the whole markdown per delta saturates the main thread and
  // the UI visibly stalls, then the finished reply pops in at once (the
  // exact catch ThinkingBlock and the old ReplyBlock already documented).
  // While a segment streams, render plain pre-wrap text; markdown only on
  // settle.
  it('the streaming last segment is plain text (markdown not parsed)', () => {
    const segs: Segment[] = [
      { process: [], reply: { content: '**bold** text', streaming: true } },
    ];
    render(<SegmentedReply segments={segs} isStreaming />);
    // Literal asterisks visible = markdown NOT parsed.
    expect(screen.getByText('**bold** text')).toBeInTheDocument();
  });

  it('the same content goes through markdown once settled', () => {
    const segs: Segment[] = [
      { process: [], reply: { content: '**bold** text' } },
    ];
    render(<SegmentedReply segments={segs} />);
    expect(screen.queryByText('**bold** text')).toBeNull();
    expect(screen.getByText('bold')).toBeInTheDocument();
  });
});

describe('reasoning stays folded', () => {
  // Owner's call, 2026-08-31: no path auto-expands the provider's reasoning.
  // Opening a turn's process asks for its conclusions — the scratch paper is
  // bulkier than all of them and would bury what the reader came for.
  it('the reasoning is folded, and one click reaches it', () => {
    useUIStore.setState({ interimNarration: false });
    render(<SegmentedReply segments={segments} showProcess />);
    expect(screen.queryByText(/check the material/)).toBeNull();

    fireEvent.click(screen.getAllByRole('button', { expanded: false })[0]);
    expect(screen.getByText(/check the material/)).toBeInTheDocument();
  });
});
