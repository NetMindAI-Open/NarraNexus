/**
 * @file_name: messageBubbleError.test.tsx
 * @date: 2026-07-03
 * @description: The red error badge on a chat bubble.
 *
 * Regression guard: the May-2026 unified-timeline refactor dropped
 * isError/warnings on the session→timeline hop, so a failed turn (no reply /
 * silent fallback / expired login) rendered as an innocuous message with no
 * error surfaced. The badge (and the data wiring behind it) must show for any
 * error and stay hidden for a clean reply.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

function msg(p: Partial<ChatMessage>): ChatMessage {
  return { id: 'm1', role: 'assistant', content: 'hi', timestamp: 0, ...p };
}

describe('MessageBubble error badge', () => {
  it('shows the badge when the whole turn failed (isError)', () => {
    render(<MessageBubble message={msg({ isError: true, content: 'Login expired' })} />);
    expect(screen.getByLabelText('Show error details')).toBeInTheDocument();
  });

  it('shows the badge when the run finished with non-fatal warnings', () => {
    render(<MessageBubble message={msg({ warnings: ['module decision LLM failed'] })} />);
    expect(screen.getByLabelText('Show error details')).toBeInTheDocument();
  });

  it('shows no badge for a clean reply', () => {
    render(<MessageBubble message={msg({ content: 'all good' })} />);
    expect(screen.queryByLabelText('Show error details')).not.toBeInTheDocument();
  });

  // Executor-infra failures (OOM / unreachable) render the localized "what you
  // can do" copy in the bubble body via chat.error.action.<reason>, keyed on
  // the action_reason the backend surfaces (infra_transient error type).
  it('renders the executor OOM guidance for an executor_oom reason', () => {
    render(<MessageBubble message={msg({ isError: true, actionReason: 'executor_oom' })} />);
    expect(screen.getByText(/ran out of memory/i)).toBeInTheDocument();
  });

  it('renders the unreachable guidance for an executor_unreachable reason', () => {
    render(
      <MessageBubble message={msg({ isError: true, actionReason: 'executor_unreachable' })} />
    );
    expect(screen.getByText(/temporarily unreachable/i)).toBeInTheDocument();
  });
});
