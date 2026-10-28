/**
 * @file_name: messageBubblePlugin.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: A registered message renderer owns the bubble of a message it recognises; registered message actions join the hover strip and run with the message.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { MessageBubble } from '../MessageBubble';
import { MESSAGE_ACTIONS, MESSAGE_RENDERERS } from '@/platform/registries';
import { onUiError, resetErrorSink } from '@/platform/errorSink';
import type { ChatMessage } from '@/types';

function msg(p: Partial<ChatMessage>): ChatMessage {
  return { id: 'm1', role: 'assistant', content: 'plain reply', timestamp: 1, ...p } as ChatMessage;
}

const disposers: (() => void)[] = [];
afterEach(() => {
  disposers.splice(0).forEach((d) => d());
  resetErrorSink();
});

describe('message renderers and actions', () => {
  it('a matching renderer replaces the shell bubble; others render normally', () => {
    disposers.push(MESSAGE_RENDERERS.register('acme.card', { match: (m) => (m as ChatMessage).content.startsWith('card:'), component: ({ message }) => <div data-testid="card">CARD {(message as ChatMessage).content}</div> }, { owner: 'acme.t' }));
    render(<MemoryRouter><MessageBubble message={msg({ content: 'card:42' })} agentId="a1" /></MemoryRouter>);
    expect(screen.getByTestId('card')).toHaveTextContent('CARD card:42');
    render(<MemoryRouter><MessageBubble message={msg({ id: 'm2', content: 'plain reply' })} agentId="a1" /></MemoryRouter>);
    expect(screen.getByText('plain reply')).toBeInTheDocument();
  });

  it('a throwing renderer falls back to the shell bubble instead of taking the surface down (I-6)', () => {
    disposers.push(MESSAGE_RENDERERS.register('acme.broken', { match: (m) => (m as ChatMessage).content.startsWith('broken:'), component: () => { throw new Error('boom'); } }, { owner: 'acme.broken_plugin' }));
    const seen: unknown[] = [];
    onUiError((r) => seen.push(r));
    render(<MemoryRouter><MessageBubble message={msg({ content: 'broken:1' })} agentId="a1" /></MemoryRouter>);
    // the shell's own bubble rendered — the crash never reached React's nearest
    // ancestor boundary (which would have blanked the whole conversation, not just this row).
    expect(screen.getByText('broken:1')).toBeInTheDocument();
    expect(seen).toEqual([expect.objectContaining({ source: 'acme.broken_plugin' })]);
  });

  it('message actions appear in the hover strip and receive the message', () => {
    const seen: unknown[] = [];
    disposers.push(MESSAGE_ACTIONS.register('acme.pin', { label: 'Pin', run: (ctx) => { seen.push(ctx); } }, { owner: 'acme.t' }));
    disposers.push(MESSAGE_ACTIONS.register('acme.team-only', { label: 'Team only', when: 'conversationKind:team', run: () => {} }, { owner: 'acme.t' }));
    render(<MemoryRouter><MessageBubble message={msg({ content: 'hello' })} agentId="a1" isLatest /></MemoryRouter>);
    expect(screen.queryByLabelText('Team only')).toBeNull();
    fireEvent.click(screen.getByLabelText('Pin'));
    expect(seen).toEqual([{ agentId: 'a1', message: expect.objectContaining({ content: 'hello' }) }]);
  });
});
