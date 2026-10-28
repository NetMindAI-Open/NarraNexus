/**
 * The agent's turn is a DOCUMENT, not a message.
 *
 * The user's message stays a bubble — it is one thing they sent. The agent's
 * turn is the page's main reading surface, so it sits directly on the ground:
 * no fill, no border, no radius, full width. Its reply is body prose (markdown
 * headings / lists / tables are page content), its narration reads inline in
 * the order it happened, and provider reasoning collapses out of the way.
 *
 * This exists because the previous shape failed visual acceptance: narration
 * was one ink step brighter than reasoning and buried inside it, so the whole
 * mechanism was invisible. Re-toning could not fix that; the reading surface
 * had to change.
 *
 * Iron rule #16 throughout: nothing here removes a character. Reasoning is
 * collapsed, which is one click from visible — never dropped.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MessageBubble } from '../MessageBubble';
import { useUIStore } from '@/stores/uiStore';
import type { ChatMessage } from '@/types';

const renderIn = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

const NARRATION = 'Reading the config first, then patching it.';
const REASONING = 'The user probably means the plugin, not the CLI.';
const REPLY = 'Done — the plugin is enabled.';

const agentTurn: ChatMessage = {
  id: 'm1', role: 'assistant', content: REPLY, timestamp: 0,
  segments: [{
    process: [
      { id: 'n1', ts: 1, type: 'thinking', content: NARRATION, monologue: true },
      { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'mcp__x__read_file', tool_input: { path: 'a' } },
      { id: 'r1', ts: 3, type: 'thinking', content: REASONING },
    ],
    reply: { content: REPLY },
  }],
};

const userMessage: ChatMessage = {
  id: 'u1', role: 'user', content: 'is the plugin supported?', timestamp: 0,
};

/** The element carrying a turn's own surface styling, if it has any. */
const surfaceOf = (text: string): CSSStyleDeclaration | null => {
  const el = screen.getByText(text);
  const styled = el.closest('[style*="background"]') as HTMLElement | null;
  return styled ? styled.style : null;
};

describe('agent turn renders as a document, not a bubble', () => {
  beforeEach(() => useUIStore.setState({ interimNarration: true }));

  it('carries no bubble chrome — no fill, no border, no radius', () => {
    const { container } = renderIn(<MessageBubble message={agentTurn} />);

    // Nothing in the agent turn paints its own surface.
    expect(surfaceOf(REPLY)).toBeNull();
    // And no species stripe: the left silicon rail retires with the bubble.
    expect(container.innerHTML).not.toContain('--color-silicon');
  });

  it('still renders the reply text', () => {
    renderIn(<MessageBubble message={agentTurn} />);
    expect(screen.getByText(REPLY)).toBeInTheDocument();
  });

  it('shows narration inline, with no click', () => {
    renderIn(<MessageBubble message={agentTurn} />);
    expect(screen.getByText(NARRATION)).toBeInTheDocument();
  });

  it('shows the tool call inline, with no click', () => {
    renderIn(<MessageBubble message={agentTurn} />);
    expect(screen.getByText('read_file')).toBeInTheDocument();
  });

  it('collapses provider reasoning but keeps it one click away (#16)', () => {
    renderIn(<MessageBubble message={agentTurn} />);

    expect(screen.queryByText(REASONING)).toBeNull();
    const toggles = screen.getAllByRole('button', { expanded: false });
    toggles.forEach((b) => fireEvent.click(b));
    expect(screen.getByText(REASONING)).toBeInTheDocument();
  });

  it('has no outer "Reasoning & tools" drawer left to open', () => {
    renderIn(<MessageBubble message={agentTurn} />);
    // The drawer counted its process events; the flow shows them instead.
    expect(screen.queryByText(/\(3\)/)).toBeNull();
    expect(screen.queryByText(/Reasoning & tools/i)).toBeNull();
  });

  it('turning the narration preference off recedes it — never hides it', () => {
    useUIStore.setState({ interimNarration: false });
    renderIn(<MessageBubble message={agentTurn} />);

    // Same characters on screen, one way or another (#16).
    const collapsed = screen.queryByText(NARRATION);
    if (!collapsed) {
      screen.getAllByRole('button', { expanded: false }).forEach((b) => fireEvent.click(b));
    }
    expect(screen.getByText(NARRATION)).toBeInTheDocument();
  });
});

describe('the user message keeps its bubble', () => {
  it('paints its own surface and keeps the carbon edge', () => {
    const { container } = renderIn(<MessageBubble message={userMessage} />);

    const surface = surfaceOf('is the plugin supported?');
    expect(surface).not.toBeNull();
    expect(surface!.background).toContain('var(--');
    expect(container.innerHTML).toContain('--color-carbon');
  });
});
