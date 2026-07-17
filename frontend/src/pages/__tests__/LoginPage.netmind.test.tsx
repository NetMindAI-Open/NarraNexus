import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

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
  getNetmindConfig: () => ({ authApi: 'https://nm.test', accountsUrl: 'https://acc.test', sysCode: 'f925fc2c', registerUrl: 'https://reg.test' }),
  isPowerLoginAvailable: () => true,
}));

import { LoginPage } from '../LoginPage';

describe('LoginPage cloud branch (NetMind)', () => {
  test('renders email + password + OAuth buttons, Sign-up is an external link', () => {
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
    expect(screen.getByLabelText(/password/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /google/i })).toBeTruthy();
    const signup = screen.getByRole('link', { name: /sign up|create account/i }) as HTMLAnchorElement;
    expect(signup.href).toContain('reg.test');
  });

  test('shows the account-migration notice with support contact', () => {
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    expect(screen.getByText(/reorganized our account system/i)).toBeTruthy();
    const support = screen.getByRole('link', {
      name: /bin\.liang@netmind\.ai/i,
    }) as HTMLAnchorElement;
    expect(support.href).toContain('mailto:bin.liang@netmind.ai');
  });
});
