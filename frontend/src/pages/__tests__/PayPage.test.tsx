/**
 * @file_name: PayPage.test.tsx
 * @date: 2026-07-31
 * @description: /pay bounce-route branches — the P0 checkout-funnel fix.
 * The page's contract: a free Power user is redirected to Stripe with zero
 * interaction; every other state degrades to the account page or an
 * explicit retryable error, never a dead end.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { StrictMode } from 'react';
import { PayPage } from '../PayPage';
import { ApiError } from '@/lib/api';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

const authState = { netmindToken: 'tok_live' };
vi.mock('@/stores', () => ({
  useConfigStore: (selector: (s: typeof authState) => unknown) => selector(authState),
}));

const mockGetSubscription = vi.fn();
const mockSubscribe = vi.fn();
vi.mock('@/lib/api', async (importOriginal) => {
  // Real ApiError class (instanceof checks in PayPage), stubbed api surface.
  const mod = await importOriginal<typeof import('@/lib/api')>();
  return {
    ApiError: mod.ApiError,
    api: {
      getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
      subscribe: (...a: unknown[]) => mockSubscribe(...a),
    },
  };
});

const mockIsTauri = vi.fn(() => false);
vi.mock('@/lib/tauri', () => ({
  isTauri: () => mockIsTauri(),
}));

const mockOpenExternal = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/platform', () => ({
  platform: { openExternal: (url: string) => mockOpenExternal(url) },
}));

const replaceSpy = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  authState.netmindToken = 'tok_live';
  mockIsTauri.mockReturnValue(false);
  Object.defineProperty(window, 'location', {
    value: { ...window.location, replace: replaceSpy },
    writable: true,
    configurable: true,
  });
});

const FREE = { success: true, data: { subscription: null } };
const ACTIVE = { success: true, data: { subscription: { status: 'ACTIVE', auto_renew: true } } };
const CHECKOUT = { success: true, data: { checkout_url: 'https://checkout.stripe.com/c/pay_123' } };

describe('PayPage', () => {
  it('free power user: mints a checkout session and replaces the history entry', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue(CHECKOUT);

    render(<PayPage />);

    // replace, not assign: Back from Stripe must NOT re-mount /pay and mint
    // a second session — the exact loop this page was reviewed for.
    await waitFor(() =>
      expect(replaceSpy).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay_123'),
    );
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('already subscribed: goes to the account page, never re-subscribes', async () => {
    mockGetSubscription.mockResolvedValue(ACTIVE);

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('probe failure is "unknown", not fatal: checkout still proceeds', async () => {
    // The duplicate-subscription invariant is enforced server-side (400);
    // a flaky read-only status call must not kill the P0 payment path.
    mockGetSubscription.mockRejectedValue(new ApiError(502, 'upstream sneezed'));
    mockSubscribe.mockResolvedValue(CHECKOUT);

    render(<PayPage />);

    await waitFor(() =>
      expect(replaceSpy).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay_123'),
    );
    expect(screen.queryByText("Couldn't start checkout")).not.toBeInTheDocument();
  });

  it('subscribe 400 "already subscribed" lands on the account page like the probe would', async () => {
    mockGetSubscription.mockRejectedValue(new ApiError(502, 'probe down'));
    mockSubscribe.mockRejectedValue(new ApiError(400, 'API error 400: Already subscribed to Pro.'));

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('billing 401 (dead loginToken) goes to the account page — retry can never fix it', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockRejectedValue(new ApiError(401, 'NetMind token invalid or expired'));

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('non-Power session: goes to the account page without touching billing APIs', async () => {
    authState.netmindToken = '';

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockGetSubscription).not.toHaveBeenCalled();
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it('subscribe failure: shows a retryable error, and retry re-runs the flow', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockRejectedValueOnce(new Error('upstream 502'));

    render(<PayPage />);

    expect(await screen.findByText("Couldn't start checkout")).toBeInTheDocument();
    expect(screen.getByText('upstream 502')).toBeInTheDocument();

    mockSubscribe.mockResolvedValueOnce({ success: true, data: { checkout_url: 'https://checkout.stripe.com/c/retry' } });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() =>
      expect(replaceSpy).toHaveBeenCalledWith('https://checkout.stripe.com/c/retry'),
    );
  });

  it('missing checkout_url in a success envelope is an error, not a silent stop', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue({ success: true, data: {} });

    render(<PayPage />);

    expect(await screen.findByText("Couldn't start checkout")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('desktop (Tauri): opens checkout in the system browser, webview stays in-app', async () => {
    mockIsTauri.mockReturnValue(true);
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue(CHECKOUT);

    render(<PayPage />);

    await waitFor(() =>
      expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay_123'),
    );
    expect(replaceSpy).not.toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true });
  });

  it('StrictMode double-effect mints exactly one checkout session', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue(CHECKOUT);

    render(
      <StrictMode>
        <PayPage />
      </StrictMode>,
    );

    await waitFor(() => expect(replaceSpy).toHaveBeenCalled());
    expect(mockSubscribe).toHaveBeenCalledTimes(1);
  });
});
