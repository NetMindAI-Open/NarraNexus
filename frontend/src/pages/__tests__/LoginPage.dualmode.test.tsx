import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

// Local (non-cloud) deployment. Power availability is toggled per-test via the
// runtimeConfig mock below.
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => {
    const store = { login: vi.fn(), setNetmindToken: vi.fn(), setAgents: vi.fn(), setAgentId: vi.fn() };
    return sel ? sel(store) : store;
  },
  useRuntimeStore: (sel: (s: unknown) => unknown) =>
    sel({ mode: 'local', setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
}));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));

const powerAvailable = vi.fn(() => true);
vi.mock('@/lib/runtimeConfig', () => ({
  getNetmindConfig: () => ({ authApi: 'https://nm.test', accountsUrl: 'https://acc.test', sysCode: 'f925fc2c', registerUrl: 'https://reg.test' }),
  isPowerLoginAvailable: () => powerAvailable(),
}));

import { LoginPage } from '../LoginPage';

describe('LoginPage local dual-mode', () => {
  test('local + Power available: tabs shown, Power form by default (recommended)', () => {
    powerAvailable.mockReturnValue(true);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    // both tabs present
    expect(screen.getByRole('tab', { name: /local/i })).toBeTruthy();
    expect(screen.getByRole('tab', { name: /power/i })).toBeTruthy();
    // default tab = power → NetMind form visible, username form NOT shown
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /google/i })).toBeTruthy();
    expect(screen.queryByPlaceholderText('your_username')).toBeNull();
  });

  test('local + Power available: switching to Local tab reveals the username form', () => {
    powerAvailable.mockReturnValue(true);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole('tab', { name: /local/i }));
    expect(screen.getByPlaceholderText('your_username')).toBeTruthy();
    // NetMind form is now hidden
    expect(screen.queryByLabelText(/email/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /google/i })).toBeNull();
  });

  test('local + Power NOT available: username only, no tabs, no NetMind form', () => {
    powerAvailable.mockReturnValue(false);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    expect(screen.getByPlaceholderText('your_username')).toBeTruthy();
    expect(screen.queryByRole('tab', { name: /power/i })).toBeNull();
    expect(screen.queryByLabelText(/email/i)).toBeNull();
  });
});
