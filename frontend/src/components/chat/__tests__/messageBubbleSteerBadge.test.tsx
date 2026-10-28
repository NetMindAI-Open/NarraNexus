/**
 * The three-state steer badge under an owner's mid-run follow-up bubble:
 * queued → merged (folded into the run) → rejected (with a localized reason).
 * Guards that each state renders its own line and that the reject reason goes
 * through i18n — including the blank-reason fallback that must NOT leave a
 * dangling "Not sent — ".
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

function steerMsg(p: Partial<ChatMessage>): ChatMessage {
  return {
    id: 'm1',
    role: 'user',
    content: 'also send the summary',
    timestamp: 0,
    steerClientMsgId: 'c1',
    ...p,
  };
}

const renderBubble = (ui: React.ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

describe('MessageBubble steer badge', () => {
  it('queued shows the "Queued…" line', () => {
    renderBubble(<MessageBubble message={steerMsg({ steerStatus: 'queued' })} />);
    expect(screen.getByText('Queued…')).toBeInTheDocument();
  });

  it('merged shows the "Folded into this run" line', () => {
    renderBubble(<MessageBubble message={steerMsg({ steerStatus: 'merged' })} />);
    expect(screen.getByText(/Folded into this run/)).toBeInTheDocument();
  });

  it('rejected renders the localized reason (not the raw token)', () => {
    renderBubble(
      <MessageBubble message={steerMsg({ steerStatus: 'rejected', rejectReason: 'not_sent' })} />,
    );
    // "Not sent — connection wasn't ready" (en) — the reason resolved via i18n.
    expect(screen.getByText(/connection wasn't ready/)).toBeInTheDocument();
    expect(screen.queryByText(/not_sent/)).toBeNull();
  });

  it('rejected with a blank reason falls back to a generic phrase, no dangling dash', () => {
    renderBubble(<MessageBubble message={steerMsg({ steerStatus: 'rejected' })} />);
    expect(screen.getByText(/couldn't be delivered/)).toBeInTheDocument();
    // Must not render "Not sent — " with nothing after the dash.
    expect(screen.queryByText(/Not sent — $/)).toBeNull();
  });

  it('no badge on an ordinary message without steerStatus', () => {
    renderBubble(<MessageBubble message={steerMsg({ steerStatus: undefined })} />);
    expect(screen.queryByText('Queued…')).toBeNull();
    expect(screen.queryByText(/Folded into this run/)).toBeNull();
  });
});
