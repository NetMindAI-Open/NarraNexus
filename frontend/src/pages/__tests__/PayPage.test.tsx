/**
 * @file_name: PayPage.test.tsx
 * @date: 2026-07-31
 * @description: /pay bounce-route branches — the P0 checkout-funnel fix.
 * The page's contract: a free Power user is redirected to Stripe with zero
 * interaction; every other state degrades to the account page or an
 * explicit retryable error, never a dead end.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { PayPage } from '../PayPage';

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

const replaceSpy = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  authState.netmindToken = 'tok_live';
  Object.defineProperty(window, 'location', {
    value: { ...window.location, replace: replaceSpy },
    writable: true,
    configurable: true,
  });
});

const FREE = { success: true, data: { subscription: null } };
const ACTIVE = { success: true, data: { subscription: { status: 'ACTIVE', auto_renew: true } } };

describe('PayPage', () => {
  // This page no longer mints a checkout. It is where the marketing pricing
  // CTA lands, and minting a CARD checkout made it a dead end for the users
  // this exists to serve — Alipay and WeChat cannot pay a Stripe subscription.
  // It now hands the rail choice to the account panel, which owns the one
  // implementation of "which rail, how many months".

  it('free power user: lands ON the purchase, not merely near it', async () => {
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockResolvedValue(FREE);
    render(<PayPage />);
    // intent=buy is load-bearing: without it the CTA would arrive at a settings
    // page with the purchase one click away, which moves the dead end rather
    // than removing it.
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account&intent=buy', {
        replace: true,
      }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it('already subscribed: goes to the account page, never re-subscribes', async () => {
    mockGetSubscription.mockResolvedValue(ACTIVE);

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it('probe failure is "unknown", not fatal: the buyer still reaches the purchase', async () => {
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockRejectedValue(new Error('billing 502'));
    render(<PayPage />);
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account&intent=buy', {
        replace: true,
      }),
    );
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

  it('StrictMode double-effect redirects exactly once', async () => {
    // <StrictMode>, not rerender(): the effect's deps never change on a
    // rerender, so it simply would not run twice and the assertion would hold
    // with the inFlight guard deleted. What has to be exercised is two effect
    // runs OVERLAPPING — the second entering while the first is still awaiting
    // the probe, which is the only situation the ref exists for.
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockResolvedValue(FREE);
    render(
      <StrictMode>
        <PayPage />
      </StrictMode>,
    );
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockGetSubscription).toHaveBeenCalledTimes(1);
  });
});
