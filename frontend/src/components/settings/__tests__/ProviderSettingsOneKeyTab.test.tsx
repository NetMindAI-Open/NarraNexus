/**
 * @file_name: ProviderSettingsOneKeyTab.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-08
 * @description: The add-provider modal opens on the one-key preset tab (NetMind / Anthropic / OpenAI) for every user — cloud non-staff included.
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
  OneKeyOnboard: () => <div data-testid="onekey-card">one key</div>,
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
  authFetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true, data: { providers: {}, slots: {} } }) });
});

async function openAddModal() {
  render(<ProviderSettings />);
  fireEvent.click(await screen.findByText(/Add a provider/i));
}

describe('ProviderSettings add modal — one-key tab', () => {
  test('cloud non-staff (no Sign-in tab) still gets the one-key presets, as the default tab', async () => {
    oauthAllowedValue = false;
    await openAddModal();
    await waitFor(() => expect(screen.getByTestId('onekey-card')).toBeInTheDocument());
    expect(screen.queryByText('CLI sign-in')).toBeNull();
    expect(screen.getByText('Custom')).toBeInTheDocument();
  });

  test('switching to Custom hides the one-key card, switching back shows it', async () => {
    oauthAllowedValue = true;
    await openAddModal();
    await waitFor(() => expect(screen.getByTestId('onekey-card')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Custom'));
    expect(screen.queryByTestId('onekey-card')).toBeNull();
    fireEvent.click(screen.getByText('API key'));
    expect(screen.getByTestId('onekey-card')).toBeInTheDocument();
  });
});
