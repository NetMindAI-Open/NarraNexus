/**
 * SubscriptionConnect — the Claude Code / Codex CLI subscription connect
 * cards, extracted from ProviderSettings' add-modal "Sign in" tab so the
 * landing page (SetupPage) can reach subscription sign-in without the
 * modal detour.
 *
 * Pins:
 * - the three connect flows (claude add-as-provider, setup-token paste,
 *   codex add-as-provider) each post the right body via the
 *   parent-supplied addProvider. Connecting deliberately does NOT
 *   navigate anywhere — the Owner rejected auto-navigation; parents
 *   refresh their own state off addProvider's side effects.
 * - the cloud gate: statuses answering `allowed: false` (cloud
 *   non-staff) replace the cards with a one-line explanation — the UI
 *   must not advertise a path the backend 403s, and must not blank
 *   silently either. `allowed` is undefined on local / cloud-staff, so
 *   the component must check `=== false`, never truthiness.
 * - status probes failing shows a retry line instead of an eternal
 *   "Checking status…".
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('@/lib/tauri', () => ({
  isTauri: () => false,
  triggerClaudeLogin: vi.fn(),
  triggerClaudeLogout: vi.fn(),
  cancelClaudeLogin: vi.fn(),
}));

const claudeStatusMock = vi.fn();
const codexStatusMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getClaudeStatus: (...a: unknown[]) => claudeStatusMock(...a),
    getCodexStatus: (...a: unknown[]) => codexStatusMock(...a),
  },
  ApiError: class ApiError extends Error {},
}));
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ userId: 'u1' }) : { userId: 'u1' },
}));

import { SubscriptionConnect } from '@/components/settings/SubscriptionConnect';

const loggedIn = (email: string, extra: Record<string, unknown> = {}) => ({
  cli_installed: true,
  logged_in: true,
  email,
  expires_at: null,
  ...extra,
});

/** Distinct payloads per status route, so claude/codex assertions can't
 * pass off each other's state. */
function mockStatuses(
  claude: Record<string, unknown>,
  codex: Record<string, unknown>,
) {
  claudeStatusMock.mockResolvedValue({ success: true, data: claude });
  codexStatusMock.mockResolvedValue({ success: true, data: codex });
}

beforeEach(() => {
  claudeStatusMock.mockReset();
  codexStatusMock.mockReset();
  mockStatuses(loggedIn('claude@example.com'), loggedIn('codex@example.com'));
});

describe('SubscriptionConnect', () => {
  test('claude Add as Provider posts claude_oauth', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    const claudeCard = await screen.findByTestId('claude-connect-card');
    fireEvent.click(await within(claudeCard).findByText('Add as Provider'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({ card_type: 'claude_oauth' }),
    );
  });

  test('codex Add as Provider posts codex_oauth', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    const codexCard = await screen.findByTestId('codex-connect-card');
    fireEvent.click(await within(codexCard).findByText('Add as Provider'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({ card_type: 'codex_oauth' }),
    );
  });

  test('setup-token paste posts the token and clears the input on success', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    const input = await screen.findByPlaceholderText(/sk-ant-oat/i);
    fireEvent.change(input, { target: { value: 'sk-ant-oat01-xyz' } });
    fireEvent.click(screen.getByText('Connect with token'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({
        card_type: 'claude_oauth',
        api_key: 'sk-ant-oat01-xyz',
      }),
    );
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(''));
  });

  test('failed setup-token keeps the input (so the user can retry)', async () => {
    const addProvider = vi.fn().mockResolvedValue(false);
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    const input = await screen.findByPlaceholderText(/sk-ant-oat/i);
    fireEvent.change(input, { target: { value: 'sk-ant-oat01-xyz' } });
    fireEvent.click(screen.getByText('Connect with token'));
    await waitFor(() => expect(addProvider).toHaveBeenCalled());
    expect((input as HTMLInputElement).value).toBe('sk-ant-oat01-xyz');
  });

  test('cloud non-staff (allowed: false) shows an explanation, never the cards', async () => {
    mockStatuses(
      loggedIn('claude@example.com', { allowed: false }),
      loggedIn('codex@example.com', { allowed: false }),
    );
    const addProvider = vi.fn();
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    // A silent blank reads as "the page broke" — the gate must explain
    // itself (review round 3, Important 2).
    expect(await screen.findByTestId('subscription-cloud-managed')).toBeTruthy();
    expect(screen.queryByTestId('claude-connect-card')).toBeNull();
    expect(screen.queryByTestId('codex-connect-card')).toBeNull();
  });

  test('both status probes failing shows retry lines, not eternal "Checking status"', async () => {
    claudeStatusMock.mockRejectedValue(new Error('down'));
    codexStatusMock.mockRejectedValue(new Error('down'));
    render(
      <SubscriptionConnect providers={[]} addProvider={vi.fn()} />,
    );
    const retries = await screen.findAllByText('Retry');
    expect(retries.length).toBe(2);
    // Recovery: any retry re-probes BOTH and the cards appear.
    mockStatuses(loggedIn('claude@example.com'), loggedIn('codex@example.com'));
    fireEvent.click(retries[0]);
    expect(await screen.findByTestId('claude-connect-card')).toBeTruthy();
    expect(await screen.findByTestId('codex-connect-card')).toBeTruthy();
  });

  test('a SINGLE failing probe gets its own retry row while the other card renders', async () => {
    // The first version only handled "both failed": one route 5xx-ing
    // left that card on "Checking status…" forever (review round 4,
    // Important 1). The two routes fail independently in practice.
    codexStatusMock.mockRejectedValue(new Error('down'));
    render(
      <SubscriptionConnect providers={[]} addProvider={vi.fn()} />,
    );
    expect(await screen.findByTestId('claude-connect-card')).toBeTruthy();
    expect(await screen.findByText('Retry')).toBeTruthy();
    expect(screen.queryByText('Checking status...')).toBeNull();
  });

  test('an EXPIRED session keeps the account and expiry visible, and is not "Not logged in"', async () => {
    // The backend forces logged_in=false for an expired token (B-25). A
    // two-state status line then showed the never-logged-in copy and hid
    // the email and the date — the details the user needs to re-login
    // the right account. Expired is its own state.
    mockStatuses(
      {
        cli_installed: true,
        logged_in: false,
        expired: true,
        email: 'old@example.com',
        expires_at: '1592222400000', // 2020-06-15T12:00Z, epoch ms — the real format (mid-year: renders as 2020 in every zone)
      },
      loggedIn('codex@example.com'),
    );
    render(
      <SubscriptionConnect providers={[]} addProvider={vi.fn()} />,
    );
    const claudeCard = await screen.findByTestId('claude-connect-card');
    // One line, several nodes ("Session expired as <email> · expires …"):
    // assert on the line's text rather than on node boundaries.
    const line = (await within(claudeCard).findByTestId('cli-status-line')).textContent ?? '';
    expect(line).toContain('Session expired');
    expect(line).toContain('old@example.com');
    expect(line).toMatch(/expires/);
    // 2020, not 52,000-something: epoch ms is read as ms (threshold shared with the backend).
    expect(line).toMatch(/2020/);
    expect(line).not.toContain('Not logged in');
  });

  test('a provider record whose CLI session expired says so instead of a bare "added" tick', async () => {
    mockStatuses(
      { cli_installed: true, logged_in: false, expired: true, email: null, expires_at: null },
      loggedIn('codex@example.com'),
    );
    render(
      <SubscriptionConnect
        providers={[{ id: 'p1', source: 'claude_oauth', auth_type: 'oauth', is_active: true } as never]}
        addProvider={vi.fn()}
      />,
    );
    // The cards render before the status probes resolve ("Checking status..."),
    // so wait for the hint instead of asserting on the first paint.
    const claudeCard = await screen.findByTestId('claude-connect-card');
    expect(await within(claudeCard).findByTestId('provider-session-expired-hint')).toBeTruthy();
    // The codex card's session is fine: no hint there — asserted only once its
    // own probe has resolved, so the absence is not vacuous.
    const codexCard = await screen.findByTestId('codex-connect-card');
    await waitFor(() => expect(within(codexCard).queryByText(/Checking status/i)).toBeNull());
    expect(within(codexCard).queryByTestId('provider-session-expired-hint')).toBeNull();
  });

  test('local mode (allowed undefined) renders both cards', async () => {
    const addProvider = vi.fn();
    render(
      <SubscriptionConnect providers={[]} addProvider={addProvider} />,
    );
    expect(await screen.findByTestId('claude-connect-card')).toBeTruthy();
    expect(await screen.findByTestId('codex-connect-card')).toBeTruthy();
  });
});
