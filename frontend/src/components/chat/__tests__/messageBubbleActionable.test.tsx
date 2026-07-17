/**
 * @file_name: messageBubbleActionable.test.tsx
 * @date: 2026-07-14
 * @description: A config_actionable (deterministic self-serviceable) failure
 * must render "what you can do" guidance, not a generic "Run failed".
 *
 * The "black box" P1: a 32k model that can't hold the platform context failed
 * every turn and the fallback masked it. Now such a turn surfaces isError +
 * actionReason; the popover shows the localized actionable title + per-reason
 * guidance so the user knows to switch models. This guards that wiring.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

function msg(p: Partial<ChatMessage>): ChatMessage {
  return { id: 'm1', role: 'assistant', content: 'ctx too small', timestamp: 0, ...p };
}

describe('MessageBubble actionable error', () => {
  it('shows the badge for a config_actionable failure', () => {
    render(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    expect(screen.getByLabelText('Show error details')).toBeInTheDocument();
  });

  it('shows the localized guidance in the bubble body (not the raw error blob)', () => {
    render(<MessageBubble message={msg({ isError: true, actionReason: 'context_window', content: 'raw english + json blob' })} />);
    // Body renders the localized guidance, NOT the raw content blob.
    expect(screen.getByText(/larger context window/i)).toBeInTheDocument();
    expect(screen.queryByText('raw english + json blob')).not.toBeInTheDocument();
  });

  it('renders actionable popover instead of generic "Run failed"', () => {
    render(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    // Actionable title (popover only) + guidance now appears in BOTH body and
    // popover, so allow multiple matches.
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getAllByText(/larger context window/i).length).toBeGreaterThanOrEqual(1);
    // NOT the generic failure copy.
    expect(screen.queryByText('Run failed')).not.toBeInTheDocument();
  });

  it('falls back to generic failure copy when no actionReason', () => {
    render(<MessageBubble message={msg({ isError: true })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.queryByText('Action needed')).not.toBeInTheDocument();
  });
});
