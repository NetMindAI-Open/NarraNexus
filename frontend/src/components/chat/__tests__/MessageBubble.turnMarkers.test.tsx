/**
 * @file_name: MessageBubble.turnMarkers.test.tsx
 * @date: 2026-09-11
 * @description: A no-reply turn's persisted marker renders as a localized
 * label (GitHub #87). The backend persists "(Interrupted by user)" /
 * "(Agent decided no response needed)" and the live session writes the same
 * strings, so the bubble — not the store — is where they become readable
 * text; a live bubble and its reloaded history row must therefore read the
 * same, in the viewer's language.
 */
import { afterEach, describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import i18n from '@/i18n';
import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

const bubble = (content: string): ChatMessage => ({
  id: `m-${content}`, role: 'assistant', content, timestamp: 0,
});

const renderBubble = (content: string) =>
  render(
    <MemoryRouter>
      <MessageBubble message={bubble(content)} />
    </MemoryRouter>,
  );

describe('MessageBubble no-reply markers', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('renders the interrupted marker as the "stopped by you" label', () => {
    renderBubble('(Interrupted by user)');
    expect(screen.getByText('Stopped by you')).toBeInTheDocument();
    expect(screen.queryByText('(Interrupted by user)')).toBeNull();
  });

  it('renders the no-response marker as its own label', () => {
    renderBubble('(Agent decided no response needed)');
    expect(screen.getByText('The agent decided no reply was needed')).toBeInTheDocument();
    expect(screen.queryByText('(Agent decided no response needed)')).toBeNull();
  });

  it('localizes the marker in the viewer language', async () => {
    await i18n.changeLanguage('zh');
    renderBubble('(Interrupted by user)');
    expect(screen.getByText('已被你停止')).toBeInTheDocument();
  });

  it('leaves a real reply that merely quotes a marker untouched', () => {
    renderBubble('You typed (Interrupted by user) earlier.');
    expect(screen.getByText('You typed (Interrupted by user) earlier.')).toBeInTheDocument();
  });
});
