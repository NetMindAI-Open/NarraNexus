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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Assert WHERE the free-tier buttons send the user, not just that they exist:
// the whole point of this funnel is landing on the right Settings panel.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}));

import { MessageBubble } from '../MessageBubble';
import { useConfigStore } from '@/stores';
import type { ChatMessage } from '@/types';

function msg(p: Partial<ChatMessage>): ChatMessage {
  return { id: 'm1', role: 'assistant', content: 'ctx too small', timestamp: 0, ...p };
}

// MessageBubble reads `useNavigate` (the free-tier remedy buttons deep-link into
// Settings), so it needs a router in tests — as it always has at runtime, where
// chat lives under /app. Rendering it bare only worked while it happened to use
// no router hooks.
const renderBubble = (ui: React.ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

describe('MessageBubble actionable error', () => {
  it('shows the badge for a config_actionable failure', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    expect(screen.getByLabelText('Show error details')).toBeInTheDocument();
  });

  it('shows the localized guidance in the bubble body (not the raw error blob)', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window', content: 'raw english + json blob' })} />);
    // Body renders the localized guidance, NOT the raw content blob.
    expect(screen.getByText(/larger context window/i)).toBeInTheDocument();
    expect(screen.queryByText('raw english + json blob')).not.toBeInTheDocument();
  });

  it('renders actionable popover instead of generic "Run failed"', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    // Actionable title (popover only) + guidance now appears in BOTH body and
    // popover, so allow multiple matches.
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getAllByText(/larger context window/i).length).toBeGreaterThanOrEqual(1);
    // NOT the generic failure copy.
    expect(screen.queryByText('Run failed')).not.toBeInTheDocument();
  });

  it('falls back to generic failure copy when no actionReason', () => {
    renderBubble(<MessageBubble message={msg({ isError: true })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.queryByText('Action needed')).not.toBeInTheDocument();
  });
});

// ── free-tier exhaustion: the restored monetisation funnel (2026-07-30) ──────
// Before the free tier became an ordinary provider card this guidance lived in a
// global HTTP-402 banner. The 402 went away with the pre-run quota gate and took
// the funnel with it, leaving exhausted users a message whose two suggestions —
// top up, re-paste the key — are both impossible for this card. These pin the
// replacement: the paths that DO exist, offered inline.
describe('MessageBubble free-tier exhaustion', () => {
  beforeEach(() => mockNavigate.mockReset());

  it('offers both available paths', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    expect(screen.getByRole('button', { name: /Get Nexus Pro/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use my own provider/i })).toBeInTheDocument();
  });

  it('never suggests a top-up or re-pasting the key — neither is possible here', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    expect(screen.queryByText(/top up/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/re-paste/i)).not.toBeInTheDocument();
  });

  it('sends the upgrade button to /pay, not the settings detour', () => {
    // /pay mints the checkout session and redirects to Stripe in one hop (#223).
    // Its degenerate cases — already subscribed, desktop webview, non-Power,
    // 401 — all fall back to the account page this button used to target, so
    // routing through settings buys nothing.
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Get Nexus Pro/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/pay');
  });

  it('sends "Use my own provider" to the providers panel', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Use my own provider/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=providers');
  });

  it('shows NO buttons for any other actionable reason', () => {
    // `insufficient_balance` is excluded on purpose — it has its own, DIFFERENT
    // entry point (see the describe below). What must never leak into it is the
    // free-tier pair: telling a paying user their FREE credit ran out is wrong.
    for (const reason of ['context_window', 'model_not_found']) {
      const { unmount } = renderBubble(
        <MessageBubble message={msg({ isError: true, actionReason: reason })} />,
      );
      expect(screen.queryByRole('button', { name: /Get Nexus Pro/i })).not.toBeInTheDocument();
      unmount();
    }
  });
});

// ── a balance running dry: the second entry point (2026-08-01) ───────────────
// Owner decision: a user whose BALANCE is spent should also get one click to our
// plans, not just a sentence telling them to go find Settings.
//
// This deliberately reverses the earlier "no upsell here" stance, and the reason
// is worth recording: `insufficient_balance` is provider-agnostic by design (it
// fires for DeepSeek 402s, OpenAI insufficient_quota, Anthropic credit balance
// AND the user's own NetMind account alike), so the button cannot claim to top
// up "your account" — we do not know whose ran out. It therefore names OUR
// destination ("Plans & credits") and nothing else, which stays true whichever
// provider failed.
//
// Gated on a NetMind session: billing 404s for a pure-local username user, so a
// button would land them on a pane that renders nothing.
describe('MessageBubble balance exhausted', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    useConfigStore.setState({ netmindToken: 'tok' });
  });
  afterEach(() => useConfigStore.setState({ netmindToken: '' }));

  it('offers the plan in one hop to Stripe', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'insufficient_balance' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Get Nexus Pro/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/pay');
  });

  it('offers a top-up alongside it — the plan is not the only way to keep going', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'insufficient_balance' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Plans & credits/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account');
  });

  it('never offers "use my own provider" — this user already does', () => {
    // The one half of the free-tier pair that must not leak here. Its whole
    // premise is a user who has NO card of their own; saying it to someone whose
    // own key just ran dry is nonsense. The plan button is shared on purpose
    // (Owner, 2026-08-01) — /pay degrades to the account page for a subscriber.
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'insufficient_balance' })} />);
    expect(screen.queryByRole('button', { name: /Use my own provider/i })).not.toBeInTheDocument();
  });

  it('stays silent for a local user, who has no billing panel at all', () => {
    useConfigStore.setState({ netmindToken: '' });
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'insufficient_balance' })} />);
    expect(screen.queryByRole('button', { name: /Plans & credits/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Get Nexus Pro/i })).not.toBeInTheDocument();
  });

  it('does not reach other reasons', () => {
    for (const reason of ['context_window', 'model_not_found', 'invalid_credentials'] as const) {
      const { unmount } = renderBubble(
        <MessageBubble message={msg({ isError: true, actionReason: reason })} />,
      );
      expect(screen.queryByRole('button', { name: /Plans & credits/i })).not.toBeInTheDocument();
      unmount();
    }
  });
});
