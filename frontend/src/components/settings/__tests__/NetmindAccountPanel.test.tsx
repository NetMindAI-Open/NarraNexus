/**
 * @file NetmindAccountPanel.test.tsx
 * @description Covers the plan × runway state machine of the merged Account &
 * Subscription card: S0 local hidden; plan states (free / pro_active /
 * pro_cancelled) drive the badge + top status + management action; runway
 * health (healthy / low) decides whether spend controls stay behind "Manage"
 * or ONE contextual action is promoted (free→upsell Pro, pro→top-up). Also
 * covers the runway view (free-tier bar / grant / balance / prefer toggle),
 * the read-only module-F status, recharge flows, and the activity ledger.
 * api + i18n + runtimeStore are mocked — no network.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { NetmindAccountPanel } from '../NetmindAccountPanel';

// i18n: return the inline default string (2nd arg) with {{var}} interpolation
// applied, so assertions can read real, fully-resolved copy ("62% left").
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: unknown, o?: Record<string, unknown>) => {
      const s = typeof d === 'string' ? d : _k;
      const opts = (d && typeof d === 'object' ? (d as Record<string, unknown>) : o) ?? {};
      return s.replace(/\{\{(\w+)\}\}/g, (m, v) => (v in opts ? String(opts[v]) : m));
    },
  }),
}));

let mockEmail = '';
let mockDisplayName = '';
// The panel now gates on whether THIS session is a Power account (holds a
// NetMind loginToken), not on the deployment mode. Default truthy so the
// behavior tests render; the S0 test clears it.
let mockNetmindToken = 'tok';
vi.mock('@/stores/configStore', () => ({
  useConfigStore: (
    sel: (s: { email: string; displayName: string; netmindToken: string }) => unknown,
  ) => sel({ email: mockEmail, displayName: mockDisplayName, netmindToken: mockNetmindToken }),
}));

const mockGetSubscription = vi.fn();
const mockGetFeeInfo = vi.fn();
const mockGetRecords = vi.fn();
const mockGetMyQuota = vi.fn();
const mockGetPlans = vi.fn();
const mockSetQuotaPreference = vi.fn();
const mockSubscribe = vi.fn();
const mockCancel = vi.fn();
const mockReactivate = vi.fn();
const mockRecharge = vi.fn();
const mockRechargeStatus = vi.fn();
const mockGetProviders = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
    getFeeInfo: (...a: unknown[]) => mockGetFeeInfo(...a),
    getRecords: (...a: unknown[]) => mockGetRecords(...a),
    getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a),
    getPlans: (...a: unknown[]) => mockGetPlans(...a),
    setQuotaPreference: (...a: unknown[]) => mockSetQuotaPreference(...a),
    subscribe: (...a: unknown[]) => mockSubscribe(...a),
    cancelSubscription: (...a: unknown[]) => mockCancel(...a),
    reactivateSubscription: (...a: unknown[]) => mockReactivate(...a),
    recharge: (...a: unknown[]) => mockRecharge(...a),
    rechargeStatus: (...a: unknown[]) => mockRechargeStatus(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
  },
}));

const mockOpenExternal = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/platform', () => ({
  platform: { openExternal: (...a: unknown[]) => mockOpenExternal(...a) },
}));

// ── Fixtures ────────────────────────────────────────────────────────────────
// Shapes mirror the verified backend contracts: quota (backend/routes/quota.py,
// NO envelope), plans (billing.py verbatim proxy, {success,data:{plans}}
// envelope), fee-info (all-optional NetMind passthrough).

// Default: NetMind is registered AND the agent slot points at it → 'driving'.
const NETMIND_CONNECTED = {
  success: true,
  data: {
    providers: { p1: { source: 'netmind' } },
    slots: { agent: { config: { provider_id: 'p1' } } },
  },
};
// Registered but the agent slot is the user's OWN provider → 'available'.
const NETMIND_IDLE = {
  success: true,
  data: {
    providers: { p1: { source: 'netmind' }, own: { source: 'user' } },
    slots: { agent: { config: { provider_id: 'own' } } },
  },
};

const PRO_PLAN = {
  plan_id: 'pro',
  name: 'NetMind Pro',
  quota_limits: { rpm: 600 },
  features: { support: true, member_price: true },
  monthly_grant_usd: 19,
  prices: [{ period: 'month', currency: 'USD', stripe_price_id: 'price_x' }],
};

// input 62% left / output ~79% left → bar shows the more depleted: 62%.
const QUOTA_ACTIVE = {
  enabled: true,
  status: 'active',
  remaining_input_tokens: 124_000,
  remaining_output_tokens: 119_000,
  initial_input_tokens: 200_000,
  initial_output_tokens: 150_000,
  granted_input_tokens: 0,
  granted_output_tokens: 0,
  used_input_tokens: 76_000,
  used_output_tokens: 31_000,
  prefer_system_override: true,
};
const QUOTA_EXHAUSTED = {
  ...QUOTA_ACTIVE,
  status: 'exhausted',
  remaining_input_tokens: 0,
  remaining_output_tokens: 0,
};

const FEE_RICH = {
  success: true,
  data: {
    eligible: true,
    checks: { has_arrears: false },
    metrics: { free_credit: '12.50', monthly_free_credit: '19.00' },
  },
};
const FEE_POOR = {
  success: true,
  data: {
    eligible: true,
    checks: { has_arrears: false },
    metrics: { free_credit: '0.40', monthly_free_credit: '0.00' },
  },
};

const FREE_SUB = { success: true, data: { subscription: null } };
const PRO_SUB = (autoRenew: boolean) => ({
  success: true,
  data: {
    subscription: { status: 'ACTIVE', auto_renew: autoRenew, current_period_end: 1790000000 },
  },
});

// Restore window.confirm (and any other) spies so a later test never inherits
// a stale stub. The vi.fn() api mocks are re-primed in beforeEach, and
// mockOpenExternal is fully reset there too, so restoreAllMocks is safe.
afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  mockNetmindToken = 'tok';
  mockEmail = '';
  mockDisplayName = '';
  mockGetSubscription.mockReset();
  mockGetFeeInfo.mockReset();
  mockGetFeeInfo.mockRejectedValue(new Error('no fee')); // default: balance hidden unless a test opts in
  mockGetRecords.mockReset();
  mockGetRecords.mockRejectedValue(new Error('no records')); // default: activity hidden
  mockGetMyQuota.mockReset();
  mockGetMyQuota.mockResolvedValue({ enabled: false }); // default: no free-tier bar
  mockGetPlans.mockReset();
  mockGetPlans.mockResolvedValue({ success: true, data: { plans: [PRO_PLAN] } });
  mockSetQuotaPreference.mockReset();
  mockSubscribe.mockReset();
  mockCancel.mockReset();
  mockReactivate.mockReset();
  mockRecharge.mockReset();
  mockRechargeStatus.mockReset();
  mockGetProviders.mockReset();
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED); // default: already connected
  mockOpenExternal.mockReset();
  mockOpenExternal.mockResolvedValue(undefined); // re-prime after restoreAllMocks
});

// Default world for a Free user (fee reject + quota off) classifies as LOW, so
// the top-up controls sit behind the "one-time top-up" link. Helper expands it.
async function openTopUp() {
  fireEvent.click(await screen.findByRole('button', { name: /Just need a one-time top-up/ }));
}

// ── S0 / plan states / error ────────────────────────────────────────────────

test('S0: non-Power session (no NetMind token) renders nothing', () => {
  mockNetmindToken = '';
  const { container } = render(<NetmindAccountPanel />);
  expect(container.firstChild).toBeNull();
});

test('account row: hidden when no email; shown once when displayName equals email', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  // no email → account row absent entirely
  const { unmount } = render(<NetmindAccountPanel />);
  await screen.findByText('NetMind.AI Power');
  expect(screen.queryByText('Account')).toBeNull();
  unmount();
  // NetMind returns email AS displayName → must NOT print the email twice
  mockEmail = 'chen.tong@protagolabs.com';
  mockDisplayName = 'chen.tong@protagolabs.com';
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Account')).toBeTruthy();
  expect(screen.getAllByText('chen.tong@protagolabs.com')).toHaveLength(1);
});

test('account row: distinct nickname shows "name · email"', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockEmail = 'chen.tong@protagolabs.com';
  mockDisplayName = 'Tong Chen';
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Tong Chen')).toBeTruthy();
  expect(screen.getByText('chen.tong@protagolabs.com')).toBeTruthy();
});

test('S1: free plan (subscription null) → Free badge, no negative copy', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Free')).toBeTruthy(); // badge
  expect(screen.queryByText(/not subscribed/)).toBeNull(); // de-negativized
});

test('S2: pro active (auto_renew on) → Pro member status + valid-until', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  render(<NetmindAccountPanel />);
  // plan row: badge "Pro" + explanation with validity (the single ✓ is the
  // connection line, not the plan row)
  expect(await screen.findByText(/Member · valid until \d{4}-\d{2}-\d{2}/)).toBeTruthy();
});

test('S3: pro cancelled but in-period → downgrade copy + Resume button', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
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

test('footer: scope + sandbox notes shown; charging order moved OUT of footer', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/cover LLM API usage/)).toBeTruthy();
  expect(screen.getByText(/sandbox itself is free for now/)).toBeTruthy();
  expect(screen.queryByText(/How usage is charged/)).toBeNull();
});

test('header: branded as NetMind.AI Power', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
});

// ── plan × runway: free × healthy (progressive disclosure) ─────────────────

test('free × healthy: reassurance shown, ZERO spend buttons, manage link collapsed', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  // reassurance is the connection line (netStatus-driven), not a runway claim
  expect(await screen.findByText(/Running on your NetMind/)).toBeTruthy();
  // the core UX goal: no spend CTA anywhere until the user asks
  expect(screen.queryByRole('button', { name: /Subscribe to Pro/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /Upgrade to Pro/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Manage plan & credits/ })).toBeTruthy();
});

test('free × healthy: Manage opens a MODAL — Pro card leads, top-up demoted to a link (no peer choice)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  // closed by default — no Pro card, no top-up in the DOM yet
  expect(screen.queryByRole('button', { name: /Upgrade to Pro/ })).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: /Manage plan & credits/ }));
  // modal shows the Pro value card as the lead action…
  expect(screen.getByRole('button', { name: /Upgrade to Pro/ })).toBeTruthy();
  expect(screen.getByText(/Up to 50% off/)).toBeTruthy();
  // …with top-up NOT presented as a peer button — it's a demoted link first
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Just need a one-time top-up/ }));
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy();
});

// ── plan × runway: free × low (the decision moment) ────────────────────────

test('free × low: ONE promoted action — upsell card with value prop + dynamic price', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Free tier used up. To keep going:/)).toBeTruthy();
  // real plan value leads — the true differentiators vs a same-priced top-up
  expect(screen.getByText(/Up to 50% off on models like OpenAI/)).toBeTruthy();
  expect(screen.getByText(/No platform service fee/)).toBeTruthy();
  // price pulled from getPlans (monthly_grant_usd=19, period=month→mo)
  expect(screen.getAllByText(/\$19\.00 \/ mo/).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: /Upgrade to Pro/ })).toBeTruthy();
  // top-up demoted to a link, not a peer button
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Just need a one-time top-up/ })).toBeTruthy();
});

test('free × low: the one-time top-up link toggles — click again collapses', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  const link = await screen.findByRole('button', { name: /Just need a one-time top-up/ });
  fireEvent.click(link);
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy();
  fireEvent.click(link); // second click must collapse, not stick open
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
});

test('free × low: upsell button → api.subscribe + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://pay/x' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Upgrade to Pro/ }));
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalled());
  await waitFor(() => expect(mockOpenExternal).toHaveBeenCalledWith('https://pay/x'));
});

test('free × low: plans fetch fails → upsell card still renders, price line hidden', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetPlans.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Upgrade to Pro/ })).toBeTruthy();
  expect(screen.queryByText(/\$19\.00/)).toBeNull();
});

// ── plan × runway: pro states ───────────────────────────────────────────────

test('pro × healthy: member-pricing note, cancel hidden behind Manage', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH); // balance $12.50 ≥ buffer → healthy
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Member · valid until/)).toBeTruthy();
  expect(screen.getByText(/Member pricing active/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Cancel subscription/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Manage subscription & balance/ }));
  expect(screen.getByRole('button', { name: /Cancel subscription/ })).toBeTruthy();
});

test('pro × low: top-up promoted directly (no upsell — already Pro)', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_POOR); // $0.40 < buffer → low
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/grant and balance are running low/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy(); // no click needed
  expect(screen.queryByRole('button', { name: /Upgrade to Pro/ })).toBeNull();
});

test('S2: cancel via Manage → confirm true → api.cancelSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockCancel.mockResolvedValue({ success: true, data: { status: 'auto_renew_off' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Manage subscription & balance/ }));
  fireEvent.click(screen.getByRole('button', { name: /Cancel subscription/ }));
  await waitFor(() => expect(mockCancel).toHaveBeenCalled());
});

test('S2: cancel confirm dismissed → no api call', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Manage subscription & balance/ }));
  fireEvent.click(screen.getByRole('button', { name: /Cancel subscription/ }));
  expect(mockCancel).not.toHaveBeenCalled();
});

test('S3: resume button → confirm true → api.reactivateSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  mockReactivate.mockResolvedValue({ success: true, data: { status: 'auto_renew_on' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Resume auto-renew/ }));
  await waitFor(() => expect(mockReactivate).toHaveBeenCalled());
});

// ── runway view: free-tier bar / balance / grant / flow line / toggle ───────

test('runway: free-tier bar shows the more depleted side (62% left) + tiered flow line', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Free tier')).toBeTruthy();
  expect(screen.getByText('62% left')).toBeTruthy();
  // free-tier bar visible → the flow line may mention it
  expect(screen.getByText(/free tier first, then your balance\./)).toBeTruthy();
});

test('runway: single pool (only balance) → NO flow line at all (#3)', async () => {
  // quota off + free → the ONLY pool is the balance; the charging-order sentence
  // would be trivial, so it's hidden entirely.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Current balance')).toBeTruthy(); // balance hero
  expect(screen.getByText('$12.50')).toBeTruthy();
  expect(screen.queryByText(/Usage draws/)).toBeNull(); // no flow sentence
  expect(screen.queryByText(/free tier first/)).toBeNull();
});

test('runway: grant row is Pro-only; Pro flow line names three pools', async () => {
  // free + rich fee → no grant row
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  const { unmount } = render(<NetmindAccountPanel />);
  await screen.findByText('Current balance');
  expect(screen.queryByText('Monthly grant')).toBeNull();
  unmount();
  // pro + rich fee (no free-tier bar) → grant row + grant-first flow line
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Monthly grant')).toBeTruthy();
  expect(screen.getByText('$19.00 / mo')).toBeTruthy();
  expect(screen.getByText(/monthly grant first, then your balance\./)).toBeTruthy();
});

test('runway: partial fee payload (no metrics/checks) renders without crashing', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue({ success: true, data: { eligible: false } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
  // eligible=false → low → the action prompt carries the message; the
  // system-toned "cannot incur paid usage" line is suppressed (redundant).
  expect(await screen.findByText(/To keep going:/)).toBeTruthy();
  expect(screen.queryByText(/Cannot incur paid usage right now/)).toBeNull();
});

test('eligible=false warning still shows for pro_cancelled (no low prompt there)', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  mockGetFeeInfo.mockResolvedValue({ success: true, data: { eligible: false } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Cannot incur paid usage right now/)).toBeTruthy();
});

test('runway: hidden when fee fails and quota is off; plan status still shows', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ }); // load settled
  expect(screen.queryByText('Current balance')).toBeNull();
  expect(screen.queryByText('Free tier')).toBeNull();
});

test('runway: quota fetch failure never crashes the panel', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockRejectedValue(new Error('500'));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Current balance')).toBeTruthy();
  expect(screen.queryByText('Free tier')).toBeNull();
});

// ── prefer toggle (formerly QuotaPanel prefer_system) ──────────────────────

test('prefer toggle: click → setQuotaPreference(false), UI reflects the response', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE); // prefer ON
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockSetQuotaPreference.mockResolvedValue({ ...QUOTA_ACTIVE, prefer_system_override: false });
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect(sw.getAttribute('aria-checked')).toBe('true');
  fireEvent.click(sw);
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledWith(false));
  await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('false'));
});

test('prefer toggle: exhausted + OFF → locked (cannot turn ON without budget)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue({ ...QUOTA_EXHAUSTED, prefer_system_override: false });
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect((sw as HTMLButtonElement).disabled).toBe(true);
});

test('prefer toggle: rapid double-click fires only ONE api call (sync guard)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockSetQuotaPreference.mockReturnValue(new Promise(() => {})); // in flight forever
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  fireEvent.click(sw);
  fireEvent.click(sw); // second click lands before the first resolves
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledTimes(1));
});

test('free × low with UNKNOWN quota state → neutral copy, not "Free tier used up"', async () => {
  // quota feature off + poor balance → low, but we never observed exhaustion
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/You're low on credits. To keep going:/)).toBeTruthy();
  expect(screen.queryByText(/Free tier used up/)).toBeNull();
});

test('prefer toggle: exhausted + ON → still allowed to turn OFF', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED); // prefer ON
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  mockSetQuotaPreference.mockResolvedValue({ ...QUOTA_EXHAUSTED, prefer_system_override: false });
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect((sw as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(sw);
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledWith(false));
});

// ── module F: read-only connection status ──────────────────────────────────

test('status DRIVING: netmind is the active agent provider → green "running on NetMind" ✓', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  // default NETMIND_CONNECTED = agent slot points at the netmind provider
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Running on your NetMind/)).toBeTruthy();
  expect(screen.queryByText(/linked but idle/)).toBeNull();
  expect(screen.queryByRole('button', { name: /Use this account/ })).toBeNull();
});

test('status AVAILABLE: netmind registered but user is on their OWN provider → NO "running" claim', async () => {
  // the misleading case the redesign guards against: a netmind card exists
  // (auto-registered) but the agent slot is the user's own provider
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockGetProviders.mockResolvedValue(NETMIND_IDLE);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/linked but idle/)).toBeTruthy();
  expect(screen.getByText(/running on your own provider/)).toBeTruthy();
  // must NOT claim it's running on NetMind, and no green reassurance ✓
  expect(screen.queryByText(/Running on your NetMind/)).toBeNull();
});

test('status: no netmind provider → not-connected copy', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/isn.t linked as a provider yet/)).toBeTruthy();
});

test('status: connection line sits ABOVE the runway breakdown, both states', async () => {
  // order is account/plan → balance hero → connection → runway; assert the
  // connection line precedes the runway's "Free tier" row in both states
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: {}, slots: {} } });
  const { unmount } = render(<NetmindAccountPanel />);
  const warn = await screen.findByText(/isn.t linked as a provider yet/);
  const tier = screen.getByText('Free tier');
  expect(warn.compareDocumentPosition(tier) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  unmount();
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED);
  render(<NetmindAccountPanel />);
  const ok = await screen.findByText(/Running on your NetMind/);
  const tier2 = screen.getByText('Free tier');
  expect(ok.compareDocumentPosition(tier2) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test('status: getProviders FAILS → transient error copy (refresh), NOT re-login advice', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockRejectedValue(new Error('500'));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Couldn.t read your connection status/)).toBeTruthy();
  // must not mislead into re-logging for a network blip
  expect(screen.queryByText(/isn.t linked as a provider yet/)).toBeNull();
});

// ── recent activity (settled ledger, collapsed by default) ─────────────────

test('activity: collapsed by default, expands on click', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'r1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-03T03:48:35+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  const toggle = await screen.findByRole('button', { name: /Recent activity/ });
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
  fireEvent.click(toggle);
  expect(await screen.findByText(/\+\$10\.00 USD/)).toBeTruthy();
});

test('activity: pending records are hidden (abandoned checkouts)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
      { record_id: 's1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '5.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-05T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Recent activity/ }));
  expect(await screen.findByText(/\+\$5\.00 USD/)).toBeTruthy();
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
});

test('activity: hidden entirely when every record is pending', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ });
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

test('activity: hidden when records fetch fails', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ });
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

// ── recharge / top-up (free × low: expand via the demoted link first) ──────

test('recharge: default tier → api.recharge(10) + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {})); // stays processing
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10));
  await waitFor(() =>
    expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/x'),
  );
});

test('recharge: custom amount overrides the preset tier', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '25' } });
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(25));
});

test('recharge: non-positive amount → validation error, no api call', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  expect(await screen.findByText(/greater than 0/)).toBeTruthy();
  expect(mockRecharge).not.toHaveBeenCalled();
});

test('recharge: api.recharge rejects → failed state error shown', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockRejectedValue(new Error('checkout unavailable'));
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  expect(await screen.findByText(/checkout unavailable/)).toBeTruthy();
});

test('recharge: "Stop waiting" leaves processing and allows immediate retry', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {})); // never resolves → stuck pending
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  const stop = await screen.findByRole('button', { name: /Stop waiting/ });
  fireEvent.click(stop);
  await waitFor(() => expect(screen.queryByText(/Waiting for payment/)).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledTimes(2));
});
