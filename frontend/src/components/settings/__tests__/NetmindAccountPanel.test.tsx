/**
 * @file NetmindAccountPanel.test.tsx
 * @description Covers the plan × runway state machine of the merged Account &
 * Subscription card: S0 local hidden; plan states (free / pro_active /
 * pro_cancelled) drive the badge + top status + management action; runway
 * health (healthy / low) decides whether spend controls stay behind "Manage"
 * or ONE contextual action is promoted (free→upsell Pro, pro→top-up). Also
 * covers the runway view (free-tier bar / grant / balance — the prefer
 * toggle is gone since 2026-07-18: free-tier-first is platform behavior),
 * the read-only module-F status, recharge flows, and the activity ledger.
 * api + i18n + runtimeStore are mocked — no network.
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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
const mockSubscribe = vi.fn();
const mockCancel = vi.fn();
const mockReactivate = vi.fn();
const mockRecharge = vi.fn();
const mockRechargeStatus = vi.fn();
const mockFxRate = vi.fn();
const mockGetProviders = vi.fn();
const mockUseSubscription = vi.fn();
const mockGetCosts = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getCosts: (...a: unknown[]) => mockGetCosts(...a),
    getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
    getFeeInfo: (...a: unknown[]) => mockGetFeeInfo(...a),
    getRecords: (...a: unknown[]) => mockGetRecords(...a),
    getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a),
    getPlans: (...a: unknown[]) => mockGetPlans(...a),
    subscribe: (...a: unknown[]) => mockSubscribe(...a),
    cancelSubscription: (...a: unknown[]) => mockCancel(...a),
    reactivateSubscription: (...a: unknown[]) => mockReactivate(...a),
    recharge: (...a: unknown[]) => mockRecharge(...a),
    rechargeStatus: (...a: unknown[]) => mockRechargeStatus(...a),
    fxRate: (...a: unknown[]) => mockFxRate(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    useSubscription: (...a: unknown[]) => mockUseSubscription(...a),
  },
}));

// Post-payment return: Stripe lands the payer on /app/settings?status=…&flow=…
// The panel reads those, then strips them so a refresh doesn't re-announce a
// payment that already settled.
let mockSearch = '';
const mockSetSearchParams = vi.fn();
vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(mockSearch), mockSetSearchParams] as const,
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

// A $10 wallet with $6.20 left → the bar shows 62%.
const QUOTA_ACTIVE = {
  enabled: true,
  status: 'active',
  currency: 'USD',
  max_budget: 10,
  spend: 3.8,
  remaining: 6.2,
};
const QUOTA_EXHAUSTED = {
  ...QUOTA_ACTIVE,
  status: 'exhausted',
  spend: 10,
  remaining: 0,
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
  mockSearch = '';
  mockSetSearchParams.mockReset();
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
  mockSubscribe.mockReset();
  mockCancel.mockReset();
  mockReactivate.mockReset();
  mockRecharge.mockReset();
  mockRechargeStatus.mockReset();
  mockFxRate.mockReset();
  mockGetProviders.mockReset();
  mockUseSubscription.mockReset();
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED); // default: already connected
  mockGetCosts.mockReset();
  // Default: no platform ledger, so the NarraNexus-usage section stays hidden
  // and the pre-existing cases assert on exactly what they did before.
  mockGetCosts.mockRejectedValue(new Error('no costs'));
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
  expect(screen.queryByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Upgrade or top up/ })).toBeTruthy();
});

test('free × healthy: Manage opens a MODAL — Pro card leads, top-up demoted to a link (no peer choice)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  // closed by default — no Pro card, no top-up in the DOM yet
  expect(screen.queryByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: /Upgrade or top up/ }));
  // modal shows the Pro value card as the lead action…
  expect(screen.getByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeTruthy();
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
  expect(screen.getByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeTruthy();
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

test('free × low: upsell → card is preselected → api.subscribe()', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://pay/x' } });
  render(<NetmindAccountPanel />);
  await openBuyPro();
  fireEvent.click(await screen.findByRole('button', { name: /^Subscribe$/ }));
  // No arguments = the card subscription, unchanged from before the rails
  // existed: months do not apply to it and must not be sent.
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalledWith());
  await waitFor(() => expect(mockOpenExternal).toHaveBeenCalledWith('https://pay/x'));
});

test('free: a user with only Alipay can actually buy Pro', async () => {
  // The point of the whole branch. Before this, the upsell went straight to a
  // card checkout, so Alipay/WeChat users could top up credits but never
  // subscribe — and a one-time subscriber whose period lapsed fell back to
  // free and could never buy it back either.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockSubscribe.mockResolvedValue({
    success: true,
    data: { session_id: 'cs_new', checkout_url: 'https://checkout.stripe.com/c/pay/cs_new' },
  });
  render(<NetmindAccountPanel />);
  await openBuyPro();
  await pickMethod(/Alipay/);
  fireEvent.click(await screen.findByRole('radio', { name: /^3$/ }));
  fireEvent.click(await screen.findByRole('button', { name: /^Pay/ }));
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalledWith('alipay', 3));
  await waitFor(() =>
    expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay/cs_new'),
  );
});

test('free: the card rail is offered; a live one-time subscription withdraws it', async () => {
  // Not a preference — upstream refuses a card subscribe while a one-time
  // subscription is live, so offering it there would be offering a failure.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  const { unmount } = render(<NetmindAccountPanel />);
  await openBuyPro();
  const freeGroup = await screen.findByRole('radiogroup', { name: /How to pay for Pro/ });
  expect(within(freeGroup).getByRole('radio', { name: /Card/ })).toBeTruthy();
  unmount();

  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
  // Scoped to the Pro group: the TOP-UP chooser shares this dialog and does
  // legitimately offer card — which is exactly why the two groups needed
  // distinct accessible names.
  const proGroup = await screen.findByRole('radiogroup', { name: /How to pay for Pro/ });
  expect(within(proGroup).queryByRole('radio', { name: /Card/ })).toBeNull();
  // ...and the form must be the ONE-TIME one, not the card form with no card
  // option in sight: the default rail is 'stripe', so this only holds because
  // the panel normalises it before anything renders or is sent.
  expect(screen.getByRole('button', { name: /^Pay/ })).toBeTruthy();
});

test('free × low: plans fetch fails → upsell card still renders, price line hidden', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetPlans.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeTruthy();
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
  fireEvent.click(screen.getByRole('button', { name: /Top up or manage subscription/ }));
  expect(screen.getByRole('button', { name: /Cancel subscription/ })).toBeTruthy();
});

test('pro manage dialog: plan intro shown as Subscribed — perks visible, no upgrade CTA', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Top up or manage subscription/ }));
  // Plan card in subscribed state: name + badge + perks… ("Nexus Pro" appears
  // twice on purpose: the panel's plan badge and the card title share the name)
  expect(screen.getAllByText('Nexus Pro').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText(/Subscribed/)).toBeTruthy();
  expect(screen.getByText(/Up to 50% off on models like OpenAI/)).toBeTruthy();
  // …but no upgrade CTA (cancel is the only plan action here).
  expect(screen.queryByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeNull();
  // Pricing link present too (added 2026-07-18).
  expect(screen.getByRole('button', { name: /See all models & pricing/ })).toBeTruthy();
});

test('pro × low: top-up promoted directly (no upsell — already Pro)', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_POOR); // $0.40 < buffer → low
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/grant and balance are running low/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy(); // no click needed
  expect(screen.queryByRole('button', { name: /Upgrade to Nexus Pro/ })).toBeNull();
});

// Confirmation goes through the in-app dialog (useConfirm), never
// window.confirm: wry (the Tauri webview) does not render the native dialogs, so
// a native call resolves falsy and the handler bails out SILENTLY — the DMG user
// clicks "Cancel subscription" and nothing at all happens. The pre-2026-07-30
// tests here stubbed window.confirm, which is precisely why that was invisible.
// `nativeConfirm` therefore asserts absence, not a return value.
async function openManageDialog() {
  fireEvent.click(await screen.findByRole('button', { name: /Top up or manage subscription/ }));
  fireEvent.click(screen.getByRole('button', { name: /Cancel subscription/ }));
}

test('S2: cancel via Manage → in-app dialog, confirmed → api.cancelSubscription', async () => {
  const nativeConfirm = vi.spyOn(window, 'confirm');
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockCancel.mockResolvedValue({ success: true, data: { status: 'auto_renew_off' } });
  render(<NetmindAccountPanel />);
  await openManageDialog();

  // The dialog must be on screen and nothing may have been called yet.
  expect(await screen.findByText(/Turn off auto-renew\?/)).toBeTruthy();
  expect(mockCancel).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: /^Turn off auto-renew$/ }));
  await waitFor(() => expect(mockCancel).toHaveBeenCalled());
  expect(nativeConfirm).not.toHaveBeenCalled();
});

test('S2: cancel dialog dismissed → no api call', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  await openManageDialog();
  // "Keep subscription", not a bare "Cancel": next to a subscription-cancelling
  // action, a button labelled Cancel is genuinely ambiguous.
  fireEvent.click(await screen.findByRole('button', { name: /Keep subscription/ }));
  expect(mockCancel).not.toHaveBeenCalled();
});

test('S3: resume → in-app dialog, confirmed → api.reactivateSubscription', async () => {
  const nativeConfirm = vi.spyOn(window, 'confirm');
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  mockReactivate.mockResolvedValue({ success: true, data: { status: 'auto_renew_on' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Resume auto-renew/ }));

  expect(await screen.findByText(/Resume auto-renew\?/)).toBeTruthy();
  expect(mockReactivate).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: /^Resume$/ }));
  await waitFor(() => expect(mockReactivate).toHaveBeenCalled());
  expect(nativeConfirm).not.toHaveBeenCalled();
});

test('S3: resume dialog dismissed → no api call', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Resume auto-renew/ }));
  fireEvent.click(await screen.findByRole('button', { name: /Not now/ }));
  expect(mockReactivate).not.toHaveBeenCalled();
});

// ── runway view: free-tier bar / balance / grant / flow line / toggle ───────

test('runway: free-tier row shows the wallet in dollars, bar keeps the pct', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Free tier')).toBeTruthy();
  // Dollars, not tokens: the wallet's own unit, so the row and the balance
  // hero below it mean the same thing. Remaining only — the bar carries the
  // proportion (Owner: "/total" too dense). Six decimals, unlike the balance
  // hero: free-tier turns cost fractions of a cent, and at two the number
  // never appears to move (Owner, 2026-07-28).
  expect(screen.getByText('$6.200000 left')).toBeTruthy();
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('62');
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
  await screen.findByRole('button', { name: /Upgrade to Nexus Pro/ }); // load settled
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

// ── free tier is always drawn first (no toggle since 2026-07-18) ───────────

test('runway renders no prefer switch — free-tier-first is not a choice', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  // free-tier bar is there…
  expect(await screen.findByRole('progressbar')).toBeTruthy();
  // …but there is no switch and no "Free tier first" copy.
  expect(screen.queryByRole('switch')).toBeNull();
  expect(screen.queryByText(/Free tier first/)).toBeNull();
});

// ── Pro subscription-credit split (the "overflow tank" model) ───────────────
// Live dev numbers: free_credit 66.91, subscription_credit 56.98 (3 × $19
// cycles accumulated), recharge history $10. Split: this cycle's tank =
// min(56.98, 19) = 19 → 100% bar; overflow 37.98 + (66.91 − 56.98) = 47.91
// hero. Denominator is proPlan.monthly_grant_usd (19), NOT the unreliable
// metrics.monthly_free_credit.

const FEE_SUB_SPLIT = {
  success: true,
  data: {
    eligible: true,
    checks: { has_arrears: false },
    metrics: {
      free_credit: '66.9100',
      subscription_credit: '56.98000000',
      monthly_free_credit: '0.5000',
    },
  },
};

test('pro split: full-cycle bar + overflow folded into the hero', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE); // free tier present but REPLACED
  mockGetFeeInfo.mockResolvedValue(FEE_SUB_SPLIT);
  render(<NetmindAccountPanel />);

  // hero = (66.91 − 56.98) + (56.98 − 19) = 47.91, labelled as own money
  expect(await screen.findByText('$47.91')).toBeTruthy();
  expect(screen.getByText(/Your balance \(top-ups \+ carried-over plan credit\)/)).toBeTruthy();

  // plan-credit bar at 100%; the free-tier bar is replaced (single bar)
  const bars = screen.getAllByRole('progressbar');
  expect(bars).toHaveLength(1);
  expect(bars[0].getAttribute('aria-label')).toBe('Plan credit');
  expect(bars[0].getAttribute('aria-valuenow')).toBe('100');
  expect(screen.queryByText('Free tier')).toBeNull();

  // flow copy: plan credit → balance (no free-tier claim, no legacy grant row)
  expect(screen.getByText(/plan credit first, then your balance/)).toBeTruthy();
  expect(screen.queryByText(/Monthly grant/)).toBeNull();
});

test('pro split: mid-cycle drain → proportional bar', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue({
    ...FEE_SUB_SPLIT,
    data: {
      ...FEE_SUB_SPLIT.data,
      metrics: { free_credit: '14.43', subscription_credit: '9.50', monthly_free_credit: '0.5' },
    },
  });
  render(<NetmindAccountPanel />);
  // tank = min(9.50, 19) = 9.50 → floor(50%); hero = 14.43 − 9.50 = 4.93
  expect(await screen.findByText('$4.93')).toBeTruthy();
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50');
});

test('pro split: cycle used up → 0% bar stays + "refreshes next cycle" note', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue({
    ...FEE_SUB_SPLIT,
    data: {
      ...FEE_SUB_SPLIT.data,
      metrics: { free_credit: '4.93', subscription_credit: '0', monthly_free_credit: '0.5' },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('$4.93')).toBeTruthy();
  const bar = screen.getByRole('progressbar');
  expect(bar.getAttribute('aria-valuenow')).toBe('0');
  expect(screen.getByText(/refreshes next cycle/)).toBeTruthy();
});

test('pro WITHOUT subscription_credit (older API) → split off, legacy grant line + merged hero', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH); // no subscription_credit field
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('$12.50')).toBeTruthy(); // merged free_credit as-is
  expect(screen.getByText(/Monthly grant/)).toBeTruthy();
  expect(screen.queryByText('Plan credit')).toBeNull();
});

test('non-pro ignores subscription_credit even if present', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_SUB_SPLIT);
  render(<NetmindAccountPanel />);
  // merged hero, free-tier bar intact, no plan-credit bar
  expect(await screen.findByText('$66.91')).toBeTruthy();
  expect(screen.getByRole('progressbar').getAttribute('aria-label')).toBe('Free tier');
  expect(screen.queryByText('Plan credit')).toBeNull();
});

test('runway: exhausted free tier → bar collapses to one quiet note (one-time grant, no refresh)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/usage now draws from your balance/)).toBeTruthy();
  // no permanent 0% warning bar…
  expect(screen.queryByRole('progressbar')).toBeNull();
  // …and the flow line must not claim "free tier first" for a pool that's gone
  expect(screen.queryByText(/free tier first/)).toBeNull();
});

test('free × low with UNKNOWN quota state → neutral copy, not "Free tier used up"', async () => {
  // quota feature off + poor balance → low, but we never observed exhaustion
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/You're low on credits. To keep going:/)).toBeTruthy();
  expect(screen.queryByText(/Free tier used up/)).toBeNull();
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

test('status: no netmind provider → not-connected copy + Link it now button', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/isn.t linked as a provider yet/)).toBeTruthy();
  // Actionable in-session exit — no more "sign out and back in" copy.
  expect(screen.getByRole('button', { name: /Link it now/ })).toBeTruthy();
  expect(screen.queryByText(/[Ss]ign out and back in/)).toBeNull();
});

test('link now: click → POST use-subscription → status flips to driving', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  mockUseSubscription.mockResolvedValue({ success: true, provider_ids: ['p_nm'] });
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Link it now/ });
  // After the link succeeds, the providers re-read shows the netmind card.
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED);
  fireEvent.click(btn);
  await waitFor(() => expect(mockUseSubscription).toHaveBeenCalledTimes(1));
  expect(await screen.findByText(/Running on your NetMind/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Link it now/ })).toBeNull();
});

test('link now: 409 already-linked counts as success (status refreshes)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  mockUseSubscription.mockRejectedValue(new Error('API error: 409 Conflict'));
  render(<NetmindAccountPanel />);
  const btn = await screen.findByRole('button', { name: /Link it now/ });
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED);
  fireEvent.click(btn);
  expect(await screen.findByText(/Running on your NetMind/)).toBeTruthy();
});

test('link now: hard failure → error line, still not connected', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  mockUseSubscription.mockRejectedValue(new Error('API error: 502 Bad Gateway'));
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Link it now/ }));
  expect(await screen.findByText(/Linking failed:/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Link it now/ })).toBeTruthy();
});

test('subscribe payment lands → auto-link fires (no sign-out required)', async () => {
  // free × low promotes the upsell; complete the checkout and the poll's
  // ACTIVE result must trigger the best-effort use-subscription call.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://stripe/x' } });
  mockUseSubscription.mockResolvedValue({ success: true });
  render(<NetmindAccountPanel />);
  await openBuyPro();
  fireEvent.click(await screen.findByRole('button', { name: /^Subscribe$/ }));
  // First poll tick returns ACTIVE.
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  await waitFor(() => expect(mockUseSubscription).toHaveBeenCalledTimes(1), { timeout: 5000 });
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
  const toggle = await screen.findByRole('button', { name: /NetMind account activity/ });
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
  fireEvent.click(await screen.findByRole('button', { name: /NetMind account activity/ }));
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
  await screen.findByRole('button', { name: /Upgrade to Nexus Pro/ });
  expect(screen.queryByText(/NetMind account activity/)).toBeNull();
});

test('activity: hidden when records fetch fails', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Nexus Pro/ });
  expect(screen.queryByText(/NetMind account activity/)).toBeNull();
});

// ── usage attribution (2026-08-19: "usage shows model usage, not narra usage")
// The balance and the ledger are NetMind-account-wide; spending that account
// elsewhere moves the number in this card. Three places now say so, and the
// platform's own ledger renders beside them. ──────────────────────────────────

test('attribution: the card states that the balance is account-wide, not NarraNexus-only', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  // On the hero itself — this is the number users watch move.
  expect(
    await screen.findByText(/anything you run on this NetMind account draws it down/i),
  ).toBeInTheDocument();
  // And in the header, where the old copy said "used for your LLM API usage".
  expect(
    screen.getByText(/shared by every NetMind product, not only NarraNexus/i),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Power plan & credits · used for your LLM API usage/)).toBeNull();
});

test('attribution: the ledger is labelled as the NetMind account, all products', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'r1', kind: 'Payment', type: 'Payment', direction: 'expense', amount: '2.00', currency: 'USD', status: 'succeeded', created_at: '2026-08-18T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  expect(
    await screen.findByRole('button', { name: /NetMind account activity \(all products\)/ }),
  ).toBeInTheDocument();
});

test('attribution: the platform-scoped usage section renders with the account ledger', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetCosts.mockResolvedValue({
    success: true,
    summary: {
      total_cost_usd: 0.5,
      total_input_tokens: 200_000,
      total_output_tokens: 50_000,
      // Real contract: buckets by call_type, never a model id (backend cost.py).
      by_model: { __main_model__: { cost: 0.5, input_tokens: 200_000, output_tokens: 50_000, call_count: 3 } },
      daily: [],
    },
    records: [],
    total_count: 3,
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Used by NarraNexus · last 30 days/)).toBeInTheDocument();
  // Twice: the section total and the single model row that makes it up.
  expect(screen.getAllByText('250.0k')).toHaveLength(2);
});

test('attribution: a broken cost ledger cannot blank the billing card', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockGetCosts.mockRejectedValue(new Error('500'));
  render(<NetmindAccountPanel />);
  // Balance still renders; the usage section simply isn't there.
  expect(await screen.findByText('$12.50')).toBeInTheDocument();
  expect(screen.queryByText(/Used by NarraNexus/)).toBeNull();
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
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10, 'default'));
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
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(25, 'default'));
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

// ── post-payment return (2026-07-30 P0) ────────────────────────────────────
// Stripe now returns the payer to /app/settings?tab=account&status=…&flow=… on
// deployments with a configured public origin. That tab is a FRESH mount with no
// poll running, so everything the old in-tab poll did on success has to happen
// here too — otherwise the payer is told nothing and, for a subscription, the
// provider link that pollUntilActive fires never happens at all.

test('return with status=success&flow=topup: confirms the top-up', async () => {
  mockSearch = 'tab=account&status=success&flow=topup';
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Top-up received/i)).toBeTruthy();
  // Balance is read from the server, never inferred from the redirect.
  await waitFor(() => expect(mockGetFeeInfo).toHaveBeenCalled());
});

test('return does not duplicate the mount fetch, and re-reads once after a settle delay', async () => {
  // Re-reading immediately would only clone the five requests the mount effect
  // already fired in the same tick — no fresher, just doubled. What the money
  // display needs is a LATER read: Stripe redirects the moment IT is done, a
  // beat before NetMind has credited the account.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockSearch = 'tab=account&status=success&flow=topup';
    mockGetSubscription.mockResolvedValue(FREE_SUB);
    render(<NetmindAccountPanel />);
    await screen.findByText(/Top-up received/i);
    expect(mockGetFeeInfo.mock.calls.length).toBe(1); // mount only
    await vi.advanceTimersByTimeAsync(3500); // settle delay elapses
    expect(mockGetFeeInfo.mock.calls.length).toBe(2);
  } finally {
    vi.useRealTimers();
  }
});

test('a cancelled return never re-reads — nothing changed upstream', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockSearch = 'tab=account&status=cancelled&flow=topup';
    mockGetSubscription.mockResolvedValue(FREE_SUB);
    render(<NetmindAccountPanel />);
    await screen.findByText(/not been charged/i);
    await vi.advanceTimersByTimeAsync(3500);
    expect(mockGetFeeInfo.mock.calls.length).toBe(1);
  } finally {
    vi.useRealTimers();
  }
});

test('return with status=success&flow=subscription: starts the bounded poll on the returning tab', async () => {
  // The plan flips upstream a moment after payment, and the poll is also what
  // fires the provider auto-link. Before this, that poll only ever ran in the
  // tab that STARTED the payment — a fresh return tab had neither.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockSearch = 'tab=account&status=success&flow=subscription';
    mockGetSubscription.mockResolvedValue(FREE_SUB);
    mockUseSubscription.mockResolvedValue({ success: true });
    render(<NetmindAccountPanel />);
    expect(await screen.findByText(/Payment received/i)).toBeTruthy();
    const afterMount = mockGetSubscription.mock.calls.length;
    await vi.advanceTimersByTimeAsync(4500); // one poll interval
    expect(mockGetSubscription.mock.calls.length).toBeGreaterThan(afterMount);
  } finally {
    vi.useRealTimers();
  }
});

test('return with status=success&flow=topup: does NOT start the subscription poll', async () => {
  // A top-up never changes the plan, so polling /me would be pure noise.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockSearch = 'tab=account&status=success&flow=topup';
    mockGetSubscription.mockResolvedValue(FREE_SUB);
    render(<NetmindAccountPanel />);
    await screen.findByText(/Top-up received/i);
    expect(mockGetSubscription.mock.calls.length).toBe(1); // mount
    // Over 12s a running poll fires every 4s; the ONE settle refresh is the only
    // extra read allowed here, so the exact count separates the two.
    await vi.advanceTimersByTimeAsync(12000);
    expect(mockGetSubscription.mock.calls.length).toBe(2); // + settle only
  } finally {
    vi.useRealTimers();
  }
});

test('return with status=cancelled: says no charge was made, links nothing', async () => {
  mockSearch = 'tab=account&status=cancelled&flow=subscription';
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/not been charged/i)).toBeTruthy();
  expect(mockUseSubscription).not.toHaveBeenCalled();
});

test('return params are stripped, tab is kept — a refresh must not re-announce', async () => {
  mockSearch = 'tab=account&status=success&flow=topup';
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await screen.findByText(/Top-up received/i);
  await waitFor(() => expect(mockSetSearchParams).toHaveBeenCalled());
  const next = mockSetSearchParams.mock.calls[0][0] as URLSearchParams;
  expect(next.get('status')).toBeNull();
  expect(next.get('flow')).toBeNull();
  expect(next.get('tab')).toBe('account'); // dropping this would bounce the pane
});

test('no return params: no payment banner at all', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await screen.findByText('NetMind.AI Power');
  expect(screen.queryByText(/Payment received/i)).toBeNull();
  expect(screen.queryByText(/Top-up received/i)).toBeNull();
  expect(mockSetSearchParams).not.toHaveBeenCalled();
});

test('an unrecognised status value is ignored, not rendered blank', async () => {
  mockSearch = 'tab=account&status=weird';
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await screen.findByText('NetMind.AI Power');
  expect(screen.queryByText(/Payment received/i)).toBeNull();
  expect(screen.queryByText(/not been charged/i)).toBeNull();
});



// ── Alipay / WeChat top-up (nexus Stripe account) ──────────────────────────
// WeChat settles in CNY while the credit being bought is denominated in USD,
// so the panel has to quote the real charge before the payer commits. Card and
// Alipay are both USD and share the plain flow.

const FX_QUOTE = {
  success: true,
  data: {
    from: 'USD', to: 'CNY', rate: '6.753148',
    amount_usd: '10', charge_amount: '67.531480',
    min_amount_usd: '0.740396', min_charge: '5.0',
  },
};


/** free → "Upgrade to Nexus Pro" → the rail dialog. Card is preselected. */
async function openBuyPro() {
  fireEvent.click(await screen.findByRole('button', { name: /Upgrade to Nexus Pro/ }));
  await screen.findByRole('radiogroup', { name: /How to pay for Pro/ });
}

async function pickMethod(name: RegExp) {
  fireEvent.click(await screen.findByRole('radio', { name }));
}

test('top-up: WeChat quotes the CNY charge next to the USD amount', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockFxRate.mockResolvedValue(FX_QUOTE);
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/WeChat/);
  // The payer must see BOTH numbers IN ONE SENTENCE: they are buying $10 of
  // credit but their bank statement will say CNY. Asserting them separately
  // would pass even if the two ended up in unrelated places.
  expect(await screen.findByText(/\$10\.00 ≈ ¥67\.53/)).toBeTruthy();
  // ...and again on the button, so the last thing read before committing is
  // the amount that actually leaves their account.
  expect(screen.getByRole('button', { name: /Recharge ¥67\.53/ })).toBeTruthy();
});

test('top-up: the chosen payment method reaches the API', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/Alipay/);
  fireEvent.click(screen.getByRole('button', { name: /^Recharge/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10, 'alipay'));
});

test('top-up: under the WeChat minimum is stopped here, not by a 400', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockFxRate.mockResolvedValue(FX_QUOTE);
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/WeChat/);
  await screen.findByText(/\$10\.00 ≈ ¥67\.53/);
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '0.5' } });
  fireEvent.click(screen.getByRole('button', { name: /^Recharge/ }));
  expect(await screen.findByText(/minimum/i)).toBeTruthy();
  expect(mockRecharge).not.toHaveBeenCalled();
});

test('top-up: a failed quote must not block the payment', async () => {
  // The quote is informational. Refusing to let someone pay because a display
  // helper 502'd would turn a cosmetic outage into a revenue outage; upstream
  // does its own conversion and enforces its own minimum.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockFxRate.mockRejectedValue(new Error('fx unavailable'));
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/WeChat/);
  fireEvent.click(screen.getByRole('button', { name: /^Recharge/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10, 'wechat'));
});

test('top-up: switching back to card drops the CNY line', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockFxRate.mockResolvedValue(FX_QUOTE);
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/WeChat/);
  await screen.findByText(/\$10\.00 ≈ ¥67\.53/);
  await pickMethod(/Card/);
  await waitFor(() => expect(screen.queryByText(/≈ ¥67\.53/)).toBeNull());
});

test('top-up: card is the default method and asks for no quote', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await openTopUp();
  const card = await screen.findByRole('radio', { name: /Card/ });
  expect(card.getAttribute('aria-checked')).toBe('true');
  expect(mockFxRate).not.toHaveBeenCalled();
});


// ── one-time (Alipay / WeChat) subscriptions ────────────────────────────────
// A one-time purchase never renews, so auto_renew is false for its ENTIRE life
// — the same tuple a cancelled card sits in. `payment_method` (verified present
// on dev 2026-08-19) is the only thing separating them, and getting it wrong is
// not cosmetic: offering "Resume auto-renew" to a one-time holder calls an
// endpoint that does not apply to them, and offering "Renew" to a card holder
// is rejected upstream with "Already subscribed to Pro."

const ONETIME_SUB = (method: 'alipay' | 'wechat' = 'alipay') => ({
  success: true,
  data: {
    subscription: {
      status: 'ACTIVE',
      auto_renew: false,
      current_period_end: 1790000000,
      payment_method: method,
    },
  },
});

const CARD_CANCELLED_SUB = {
  success: true,
  data: {
    subscription: {
      status: 'ACTIVE',
      auto_renew: false,
      current_period_end: 1790000000,
      payment_method: 'stripe',
    },
  },
};

test('one-time: never offers "Resume auto-renew" — it does not apply', async () => {
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Renew/ });
  expect(screen.queryByRole('button', { name: /Resume auto-renew/ })).toBeNull();
});

test('one-time: offers Renew instead', async () => {
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Renew/ })).toBeTruthy();
});

test('one-time: never offers Cancel — upstream no-ops it and reports success', async () => {
  mockGetSubscription.mockResolvedValue(ONETIME_SUB('wechat'));
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Renew/ });
  expect(screen.queryByRole('button', { name: /Cancel subscription/ })).toBeNull();
});

test('cancelled CARD keeps Resume auto-renew (payment_method = stripe)', async () => {
  mockGetSubscription.mockResolvedValue(CARD_CANCELLED_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Resume auto-renew/ })).toBeTruthy();
});

test('subscription with NO payment_method is treated as a cancelled card', async () => {
  // Every subscription that predates the nexus account is a card one, so the
  // absent field must fall back to the old reading, not to the new state.
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Resume auto-renew/ })).toBeTruthy();
});

test('one-time renew: months reach api.subscribe and the checkout opens', async () => {
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  mockSubscribe.mockResolvedValue({
    success: true,
    data: { session_id: 'cs_sub1', checkout_url: 'https://checkout.stripe.com/c/pay/cs_sub1' },
  });
  mockRechargeStatus.mockResolvedValue({ success: true, data: { status: 'succeeded' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
  fireEvent.click(await screen.findByRole('radio', { name: /^3$/ }));
  fireEvent.click(await screen.findByRole('button', { name: /^Pay/ }));
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalledWith('alipay', 3));
  await waitFor(() =>
    expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay/cs_sub1'),
  );
});

test('one-time purchase polls the SESSION, not "am I Pro yet"', async () => {
  // The subscription is already ACTIVE when someone extends it, so a poll that
  // waits for ACTIVE would report success before the payment even happened.
  // Upstream models a one-time purchase as a recharge, so its session is
  // pollable by id — which is what actually settles.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockGetSubscription.mockResolvedValue(ONETIME_SUB());
    mockSubscribe.mockResolvedValue({
      success: true,
      data: { session_id: 'cs_sub2', checkout_url: 'https://checkout.stripe.com/c/pay/cs_sub2' },
    });
    mockRechargeStatus.mockResolvedValue({ success: true, data: { status: 'succeeded' } });
    render(<NetmindAccountPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^Pay/ }));
    await vi.advanceTimersByTimeAsync(4500); // one poll interval
    expect(mockRechargeStatus).toHaveBeenCalledWith('cs_sub2');
    // and never the "am I Pro yet" poll, which is already true here
    expect(mockGetSubscription.mock.calls.length).toBeLessThan(3);
  } finally {
    vi.useRealTimers();
  }
});

test('a finished renewal does not announce a top-up', async () => {
  // Both controls sit in the same dialog and share the submit guard on purpose
  // (two checkouts at once is not a state we want). Their FEEDBACK must not be
  // shared: without the flow marker a settled renewal announced "Top-up
  // complete — balance updated" right next to it.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockGetSubscription.mockResolvedValue(ONETIME_SUB());
    mockSubscribe.mockResolvedValue({
      success: true,
      data: { session_id: 'cs_sub3', checkout_url: 'https://checkout.stripe.com/c/pay/cs_sub3' },
    });
    mockRechargeStatus.mockResolvedValue({ success: true, data: { status: 'succeeded' } });
    render(<NetmindAccountPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^Pay/ }));
    await vi.advanceTimersByTimeAsync(4500);
    // the settle path awaits a full reload before flipping to success
    await waitFor(() => expect(screen.getByText(/Pro extended/i)).toBeTruthy());
    expect(screen.queryByText(/Top-up complete/i)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test('CNY figures are rounded for display, not echoed at upstream precision', async () => {
  // Upstream returns money as high-precision decimal strings ("67.531480",
  // "5.0" — measured on dev 2026-08-19). Every earlier fixture here was
  // 2-decimal because I wrote it that way, so the suite was green while the
  // real panel would have read "¥67.531480". The exchange RATE keeps its
  // precision on purpose: that is information, not noise.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockFxRate.mockResolvedValue(FX_QUOTE);
  render(<NetmindAccountPanel />);
  await openTopUp();
  await pickMethod(/WeChat/);
  // Two places on purpose — the conversion line and the button.
  expect((await screen.findAllByText(/¥67\.53(?!\d)/)).length).toBe(2);
  expect(document.body.textContent).not.toMatch(/67\.531480/);
  // These two sit as sibling text nodes inside one <p>, so match on the page
  // text rather than on an element.
  expect(document.body.textContent).toMatch(/Minimum ¥5\.00 per payment/);
  expect(document.body.textContent).toMatch(/1 USD = 6\.753148 CNY/);
});

test('one-time: the plan ROW says Pro, not Free', async () => {
  // The state machine drives three independent surfaces — badge, plan
  // explanation, action zone — and the first version of pro_onetime only
  // taught the third. Both of the others fell through to their Free branch, so
  // a paying subscriber read "Free — usage billed from your balance" directly
  // above "Pro is active". Assert the row, not just the button.
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Renew/ });
  expect(screen.getByText(/Nexus Pro/)).toBeTruthy();
  expect(screen.queryByText(/Free — usage billed from your balance/)).toBeNull();
  expect(screen.getByText(/one-time purchase, does not renew/i)).toBeTruthy();
});

test('one-time: the action zone does not restate the plan row', async () => {
  // The plan row already carries "Nexus Pro" + the end date + "does not
  // renew". The action zone's most prominent line went to repeating it, so it
  // now states the benefit instead — the same thing pro_active says in that
  // exact position.
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Renew/ });
  expect(screen.getByText(/Member pricing active/)).toBeTruthy();
  expect(screen.queryByText(/Pro is active/)).toBeNull();
  // ...and the fact itself is still on screen exactly once, in the plan row.
  expect(screen.getAllByText(/does not renew/i).length).toBe(1);
});

test('renew dialog never invents a future end date', async () => {
  // It used to render currentPeriodEnd + months*30d, which disagreed with the
  // plan row directly above (2026-09-23 vs 2026-08-24) because a month is not
  // 30 days — dev bills one as `2day`, a calendar month is 28-31, and upstream
  // owns the arithmetic either way. The dialog now anchors on the date the
  // server actually reports, in the same format the plan row uses.
  mockGetSubscription.mockResolvedValue(ONETIME_SUB()); // period_end 1790000000
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
  fireEvent.click(await screen.findByRole('radio', { name: /^3$/ }));
  expect(await screen.findByText(/current end date, 2026-09-21/)).toBeTruthy();
  // 3 months of invented 30-day arithmetic would have landed here:
  expect(document.body.textContent).not.toMatch(/2026-12-20|2026\/12\/20/);
});

test('a payment in flight disables the OTHER money button, not just its own', async () => {
  // The submit guard (rechargeRef) is shared between renew and top-up on
  // purpose, but a ref cannot reach the render. Routing `disabled` through
  // payFlow left the non-narrating button enabled: clicking it hit the guard's
  // early return and produced no loading, no error, no DOM change at all —
  // in a payment dialog, where "nothing happened" reads as "click it again".
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mockGetSubscription.mockResolvedValue(ONETIME_SUB());
    mockSubscribe.mockResolvedValue({
      success: true,
      data: { session_id: 'cs_x', checkout_url: 'https://checkout.stripe.com/c/pay/cs_x' },
    });
    mockRechargeStatus.mockReturnValue(new Promise(() => {})); // stays processing
    render(<NetmindAccountPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^Pay/ }));
    await vi.advanceTimersByTimeAsync(100);
    // The top-up button shares the dialog and the guard, so it must be disabled…
    const recharge = screen.getByRole('button', { name: /Recharge/ });
    expect(recharge).toHaveProperty('disabled', true);
    // …while only the flow that started it narrates.
    expect(screen.queryByText(/balance updates automatically/)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test('a /plans failure does not take the purchase down with it', async () => {
  // Creating the checkout needs the rail and the month count, never the price
  // — upstream prices it. Gating the button on the catalog turned a read-only
  // 502 into "nobody can buy Pro", with a dead button and no reason given.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetPlans.mockRejectedValue(new Error('plans 502'));
  mockSubscribe.mockResolvedValue({
    success: true,
    data: { session_id: 'cs_np', checkout_url: 'https://checkout.stripe.com/c/pay/cs_np' },
  });
  render(<NetmindAccountPanel />);
  await openBuyPro();
  const buy = screen.getByRole('button', { name: /^Subscribe$/ });
  expect(buy).toHaveProperty('disabled', false);
  fireEvent.click(buy);
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalled());
});

test('month choice is a radiogroup: one tab stop, arrow keys move it', async () => {
  mockGetSubscription.mockResolvedValue(ONETIME_SUB());
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Renew/ }));
  const group = await screen.findByRole('radiogroup', { name: /How many months/ });
  const one = within(group).getByRole('radio', { name: /^1$/ });
  expect(one.getAttribute('aria-checked')).toBe('true');
  expect(one.getAttribute('tabindex')).toBe('0');
  // ...and the unselected ones are OUT of the tab order, which is the half
  // that makes it one stop instead of six.
  expect(within(group).getByRole('radio', { name: /^6$/ }).getAttribute('tabindex')).toBe('-1');

  fireEvent.keyDown(group, { key: 'ArrowRight' });
  const two = within(group).getByRole('radio', { name: /^2$/ });
  expect(two.getAttribute('aria-checked')).toBe('true');
  expect(document.activeElement).toBe(two);
});
