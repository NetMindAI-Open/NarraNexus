/**
 * @file_name: LoginPage.enter.test.tsx
 * @description: Enter-to-submit on the NetMind ("Power") login form.
 *
 * The local username form already submitted on Enter (handleLocalKeyDown); the
 * email/password form did not, so keyboard users pressing Enter got no response.
 * These pin that Enter triggers the same emailLogin as the Sign In button, and
 * only when both fields are present.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';

const { emailLogin, netmindState } = vi.hoisted(() => ({
  emailLogin: vi.fn(),
  netmindState: { loading: false },
}));

vi.mock('@/lib/netmindAuth/useNetmindAuth', () => ({
  useNetmindAuth: () => ({
    emailLogin,
    loading: netmindState.loading,
    error: '',
    startOAuth: vi.fn(),
    bindInfo: null,
    submitBind: vi.fn(),
    closeBind: vi.fn(),
  }),
}));
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => {
    const store = { login: vi.fn(), setNetmindToken: vi.fn(), setAgents: vi.fn(), setAgentId: vi.fn() };
    return sel ? sel(store) : store;
  },
  useRuntimeStore: (sel: (s: unknown) => unknown) =>
    sel({ mode: 'cloud-web', setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
}));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));
vi.mock('@/lib/runtimeConfig', () => ({
  getNetmindConfig: () => ({ authApi: 'https://nm.test', accountsUrl: 'https://acc.test', sysCode: 'x', registerUrl: 'https://reg.test' }),
  isPowerLoginAvailable: () => true,
}));

import { LoginPage } from '../LoginPage';

beforeEach(() => {
  emailLogin.mockClear();
  netmindState.loading = false;
});

test('pressing Enter in the Power form submits when email + password are present', () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'a@b.com' } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'secret' } });
  fireEvent.keyDown(screen.getByLabelText(/password/i), { key: 'Enter' });
  expect(emailLogin).toHaveBeenCalledWith('a@b.com', 'secret');
});

test('Enter does nothing while the password is empty', () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'a@b.com' } });
  fireEvent.keyDown(screen.getByLabelText(/email/i), { key: 'Enter' });
  expect(emailLogin).not.toHaveBeenCalled();
});

test('Enter does not submit a second time while a login is already in flight', () => {
  netmindState.loading = true; // canSubmitNetmind must be false regardless of fields
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  fireEvent.keyDown(screen.getByLabelText(/password/i), { key: 'Enter' });
  expect(emailLogin).not.toHaveBeenCalled();
});
