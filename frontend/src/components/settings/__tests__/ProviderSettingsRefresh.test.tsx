/**
 * ProviderSettings external refresh signal (refreshToken prop).
 *
 * SetupPage's subscription card lives OUTSIDE ProviderSettings, so a card
 * added through it never went through refreshConfig — the "Your providers"
 * grid kept showing the stale (empty) list until a remount. The
 * refreshToken prop is the signal: bumping it refetches the provider list.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const authFetchMock = vi.fn();
vi.mock('@/lib/providersApi', () => ({
  providerApiUrl: (path = '') => `http://test/api/providers${path}`,
  authFetch: (...a: unknown[]) => authFetchMock(...a),
}));

const cfg = { userId: 'u1' };
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => (sel ? sel(cfg) : cfg),
}));
vi.mock('@/stores/runtimeStore', () => ({
  getApiBaseUrl: () => 'http://test',
}));
vi.mock('@/components/settings/OneKeyOnboard', () => ({
  OneKeyOnboard: () => null,
}));
vi.mock('@/components/settings/SubscriptionConnect', () => ({
  SubscriptionConnect: () => null,
}));
// The tab-gate hook lives in its own module now; a variable-backed mock
// so the tab-gate tests below can flip it per case.
let oauthAllowedValue: boolean | null = true;
vi.mock('@/components/settings/useOauthAllowed', () => ({
  useOauthAllowed: () => oauthAllowedValue,
}));
vi.mock('@/lib/api', () => ({
  api: {},
  ApiError: class ApiError extends Error {},
}));

import { ProviderSettings } from '@/components/settings/ProviderSettings';

beforeEach(() => {
  authFetchMock.mockReset();
  authFetchMock.mockResolvedValue({
    json: async () => ({ success: true, data: { providers: {} } }),
  });
});

const providerListCalls = () =>
  authFetchMock.mock.calls.filter(
    ([url]) => String(url) === 'http://test/api/providers',
  ).length;

describe('ProviderSettings Sign-in tab gate', () => {
  const openAddModal = async () => {
    // The "+ Add provider" card opens the add modal that hosts the tabs.
    fireEvent.click(await screen.findByText('Add a provider'));
  };

  test('cloud non-staff (allowed === false) has no Sign-in tab', async () => {
    oauthAllowedValue = false;
    render(<ProviderSettings />);
    await openAddModal();
    expect(screen.queryByText('CLI sign-in')).toBeNull();
    // Custom is the only remaining method (the API-key tab retired
    // 2026-09-03 — first-run already owns that card), and the body must
    // fall through to it rather than render an empty modal.
    expect(screen.getByText('Custom')).toBeTruthy();
    // The modal opens on the one-key tab (2026-09-08); Custom is one click away.
    fireEvent.click(screen.getByText('Custom'));
    expect(screen.getByText('Protocol')).toBeTruthy();
  });

  test('probing (null) keeps the Sign-in tab — a truthiness check would drop it for local users', async () => {
    // Fail-open: `allowed` is undefined on local, so any truthiness
    // check would drop the tab there — the exact P0 this PR fixes,
    // inverted. Pin `=== false` semantics from the entry-point side too.
    oauthAllowedValue = null;
    render(<ProviderSettings />);
    await openAddModal();
    expect(screen.getByText('CLI sign-in')).toBeTruthy();
  });
});

describe('ProviderSettings refreshToken', () => {
  test('bumping refreshToken refetches the provider list', async () => {
    const { rerender } = render(<ProviderSettings refreshToken={0} />);
    await waitFor(() => expect(providerListCalls()).toBe(1));

    rerender(<ProviderSettings refreshToken={1} />);
    await waitFor(() => expect(providerListCalls()).toBe(2));
  });

  test('re-render without a token change does not refetch', async () => {
    const { rerender } = render(<ProviderSettings refreshToken={0} />);
    await waitFor(() => expect(providerListCalls()).toBe(1));

    rerender(<ProviderSettings refreshToken={0} />);
    // Give any accidental effect a tick to fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(providerListCalls()).toBe(1);
  });
});
