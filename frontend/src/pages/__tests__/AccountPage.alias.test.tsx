/**
 * /app/account is a legacy alias: it must forward to the Settings account
 * pane with the caller's query preserved — Stripe return parameters ride
 * along wherever the payer lands first.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

let mockSearch = 'status=success&session_id=cs_123';
vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(mockSearch), vi.fn()] as const,
  Navigate: ({ to }: { to: string }) => <div data-testid="redirect" data-to={to} />,
}));

import AccountPage from '../AccountPage';

describe('AccountPage alias', () => {
  it('forwards to the settings account pane, query preserved', () => {
    render(<AccountPage />);
    const to = screen.getByTestId('redirect').getAttribute('data-to')!;
    expect(to.startsWith('/app/settings?')).toBe(true);
    const params = new URLSearchParams(to.split('?')[1]);
    expect(params.get('tab')).toBe('account');
    expect(params.get('status')).toBe('success');
    expect(params.get('session_id')).toBe('cs_123');
  });

  it('overrides any stale tab param rather than duplicating it', () => {
    mockSearch = 'tab=providers&status=x';
    render(<AccountPage />);
    const to = screen.getByTestId('redirect').getAttribute('data-to')!;
    expect([...new URLSearchParams(to.split('?')[1]).getAll('tab')]).toEqual(['account']);
  });
});
