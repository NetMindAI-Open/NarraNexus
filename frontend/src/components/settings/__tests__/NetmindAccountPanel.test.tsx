/**
 * @file NetmindAccountPanel.test.tsx
 * @description Renders the four panel states (S1/S2/S3) and confirms S0 (local
 * mode) renders nothing. api + i18n + runtimeStore are mocked — no network.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { NetmindAccountPanel } from '../NetmindAccountPanel';

// i18n: return the inline default string (2nd arg) so assertions read real copy.
// Interpolation ({{date}}) is ignored — tests don't assert on interpolated copy.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

let mockMode = 'cloud-web';
vi.mock('@/stores/runtimeStore', () => ({
  useRuntimeStore: (sel: (s: { mode: string }) => unknown) => sel({ mode: mockMode }),
}));

const mockGetSubscription = vi.fn();
const mockGetFeeInfo = vi.fn();
const mockGetRecords = vi.fn();
const mockSubscribe = vi.fn();
const mockCancel = vi.fn();
const mockReactivate = vi.fn();
const mockUseSubscription = vi.fn();
const mockRecharge = vi.fn();
const mockRechargeStatus = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
    getFeeInfo: (...a: unknown[]) => mockGetFeeInfo(...a),
    getRecords: (...a: unknown[]) => mockGetRecords(...a),
    subscribe: (...a: unknown[]) => mockSubscribe(...a),
    cancelSubscription: (...a: unknown[]) => mockCancel(...a),
    reactivateSubscription: (...a: unknown[]) => mockReactivate(...a),
    useSubscription: (...a: unknown[]) => mockUseSubscription(...a),
    recharge: (...a: unknown[]) => mockRecharge(...a),
    rechargeStatus: (...a: unknown[]) => mockRechargeStatus(...a),
  },
}));

const mockOpenExternal = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/platform', () => ({
  platform: { openExternal: (...a: unknown[]) => mockOpenExternal(...a) },
}));

beforeEach(() => {
  mockMode = 'cloud-web';
  mockGetSubscription.mockReset();
  mockGetFeeInfo.mockReset();
  mockGetFeeInfo.mockRejectedValue(new Error('no fee')); // default: balance hidden unless a test opts in
  mockGetRecords.mockReset();
  mockGetRecords.mockRejectedValue(new Error('no records')); // default: activity hidden
  mockSubscribe.mockReset();
  mockCancel.mockReset();
  mockReactivate.mockReset();
  mockUseSubscription.mockReset();
  mockRecharge.mockReset();
  mockRechargeStatus.mockReset();
  mockOpenExternal.mockClear();
});

test('S0: local mode renders nothing', () => {
  mockMode = 'local';
  const { container } = render(<NetmindAccountPanel />);
  expect(container.firstChild).toBeNull();
});

test('S1: free plan (subscription null)', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Free \(not subscribed\)/)).toBeTruthy();
});

test('S2: pro active (auto_renew on)', async () => {
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: {
      subscription: { status: 'ACTIVE', auto_renew: true, current_period_end: 1790000000 },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Pro · active/)).toBeTruthy();
});

test('S3: pro cancelled but in-period (auto_renew off)', async () => {
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: {
      subscription: { status: 'ACTIVE', auto_renew: false, current_period_end: 1790000000 },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/downgrades to Free/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Resume auto-renew/ })).toBeTruthy();
});

test('error: fetch rejects -> error copy, no crash', async () => {
  mockGetSubscription.mockRejectedValue(new Error('401'));
  render(<NetmindAccountPanel />);
  await waitFor(() =>
    expect(screen.getByText(/Could not load your NetMind.AI Power account/)).toBeTruthy(),
  );
});

test('footer: scope note + sandbox note always shown in cloud mode', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  // scope: LLM-API-only clarification (not compute/GPU)
  expect(await screen.findByText(/cover LLM API usage/)).toBeTruthy();
  expect(screen.getByText(/sandbox itself is free for now/)).toBeTruthy();
});

test('header: branded as NetMind.AI Power', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
});

// --- Phase 3 actions --------------------------------------------------------

test('S1: subscribe button → api.subscribe + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://pay/x' } });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Subscribe to Pro/ });
  fireEvent.click(btn);
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalled());
  await waitFor(() => expect(mockOpenExternal).toHaveBeenCalledWith('https://pay/x'));
});

test('S2: cancel button → confirm true → api.cancelSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: { subscription: { status: 'ACTIVE', auto_renew: true, current_period_end: 1790000000 } },
  });
  mockCancel.mockResolvedValue({ success: true, data: { status: 'auto_renew_off' } });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Cancel subscription/ });
  fireEvent.click(btn);
  await waitFor(() => expect(mockCancel).toHaveBeenCalled());
});

test('S2: cancel confirm dismissed → no api call', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: { subscription: { status: 'ACTIVE', auto_renew: true, current_period_end: 1790000000 } },
  });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Cancel subscription/ });
  fireEvent.click(btn);
  expect(mockCancel).not.toHaveBeenCalled();
});

// --- Phase 2 enhancement: recent activity -----------------------------------

test('activity: collapsed by default, expands on click', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'r1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-03T03:48:35+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  // toggle present, but the list (amount) is hidden until expanded
  const toggle = await screen.findByRole('button', { name: /Recent activity/ });
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
  fireEvent.click(toggle);
  expect(await screen.findByText(/\+\$10\.00 USD/)).toBeTruthy();
});

test('activity: pending records are hidden (abandoned checkouts)', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
      { record_id: 's1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '5.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-05T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Recent activity/ }));
  // the settled $5 shows; the pending $10 does not
  expect(await screen.findByText(/\+\$5\.00 USD/)).toBeTruthy();
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
});

test('activity: hidden entirely when every record is pending', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  await screen.findByText(/Free \(not subscribed\)/);
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

test('activity: hidden when records fetch fails', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetRecords.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  await screen.findByText(/Free \(not subscribed\)/);
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

// --- Phase 5: use subscription ----------------------------------------------

test('use subscription: success → shows connected message', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockUseSubscription.mockResolvedValue({ success: true, provider_ids: ['p1', 'p2'] });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Use this account/ });
  fireEvent.click(btn);
  await waitFor(() => expect(mockUseSubscription).toHaveBeenCalled());
  expect(await screen.findByText(/Connected/)).toBeTruthy();
});

test('use subscription: failure → shows error, no crash', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockUseSubscription.mockRejectedValue(new Error('not enabled yet'));
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Use this account/ });
  fireEvent.click(btn);
  await waitFor(() => expect(screen.getByText(/not enabled yet/)).toBeTruthy());
});

// --- Phase 2 balance ---------------------------------------------------------

test('balance: renders free_credit + deduction order when fee-info available', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetFeeInfo.mockResolvedValue({
    success: true,
    data: {
      eligible: true,
      checks: { has_arrears: false, card_within_limit: true, has_bound_card: false },
      metrics: { balance: { usd: '0', nmt: '0', cny: '0' }, free_credit: '12.50', monthly_free_credit: '2.00', arrears: {}, card_month: {} },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/\$12\.50/)).toBeTruthy(); // balance hero
  expect(screen.getByText(/How usage is charged/)).toBeTruthy(); // footer charging order
});

test('balance: partial fee payload (no metrics/checks) renders without crashing', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  // malformed-but-200: missing metrics + checks entirely
  mockGetFeeInfo.mockResolvedValue({ success: true, data: { eligible: false } });
  render(<NetmindAccountPanel />);
  // no TypeError; header + footer always render, balance falls back to —
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
  expect(screen.getByText(/How usage is charged/)).toBeTruthy();
});

test('balance hero: hidden when fee-info fails, subscription still shows', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockGetFeeInfo.mockRejectedValue(new Error('403'));
  render(<NetmindAccountPanel />);
  // subscription status still renders
  expect(await screen.findByText(/Free \(not subscribed\)/)).toBeTruthy();
  // the balance hero (Current balance label) is gated on fee → absent
  expect(screen.queryByText(/Current balance/)).toBeNull();
});

test('S3: resume button → confirm true → api.reactivateSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: { subscription: { status: 'ACTIVE', auto_renew: false, current_period_end: 1790000000 } },
  });
  mockReactivate.mockResolvedValue({ success: true, data: { status: 'auto_renew_on' } });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Resume auto-renew/ });
  fireEvent.click(btn);
  await waitFor(() => expect(mockReactivate).toHaveBeenCalled());
});

// --- Phase 4: recharge / top-up ---------------------------------------------

test('recharge: default tier → api.recharge(10) + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  // never resolve status → stays in processing; we only assert the kickoff
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Recharge/ });
  fireEvent.click(btn);
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10));
  await waitFor(() =>
    expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/x'),
  );
});

test('recharge: custom amount overrides the preset tier', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  await screen.findByText(/Free \(not subscribed\)/);
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '25' } });
  fireEvent.click(screen.getByRole('button', { name: /Recharge/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(25));
});

test('recharge: non-positive amount → validation error, no api call', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  await screen.findByText(/Free \(not subscribed\)/);
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: /Recharge/ }));
  expect(await screen.findByText(/greater than 0/)).toBeTruthy();
  expect(mockRecharge).not.toHaveBeenCalled();
});

test('recharge: api.recharge rejects → failed state error shown', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockRecharge.mockRejectedValue(new Error('checkout unavailable'));
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Recharge/ });
  fireEvent.click(btn);
  expect(await screen.findByText(/checkout unavailable/)).toBeTruthy();
});

test('recharge: "Stop waiting" leaves processing and allows immediate retry', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {})); // never resolves → stuck pending
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Recharge/ }));
  // enters the waiting state with an escape hatch
  const stop = await screen.findByRole('button', { name: /Stop waiting/ });
  fireEvent.click(stop);
  // back to idle: waiting message gone
  await waitFor(() => expect(screen.queryByText(/Waiting for payment/)).toBeNull());
  // and a fresh top-up is not blocked by the abandoned attempt
  fireEvent.click(screen.getByRole('button', { name: /Recharge/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledTimes(2));
});
