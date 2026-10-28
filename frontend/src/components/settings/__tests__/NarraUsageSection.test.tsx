/**
 * @file NarraUsageSection.test.tsx
 * @description The section exists to answer ONE question the NetMind balance
 * cannot: "how much of that did NarraNexus spend?". So the tests pin the
 * separation itself — the platform-scoped total, the "estimate, not the
 * invoice" caveat, and the rule that this section can never take the billing
 * card down with it (fetch failure / empty ledger → renders nothing).
 * api + i18n are mocked; no network.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { NarraUsageSection } from '../NarraUsageSection';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: unknown, o?: Record<string, unknown>) => {
      const s = typeof d === 'string' ? d : _k;
      const opts = (d && typeof d === 'object' ? (d as Record<string, unknown>) : o) ?? {};
      return s.replace(/\{\{(\w+)\}\}/g, (m, v) => (v in opts ? String(opts[v]) : m));
    },
  }),
}));

const mockGetCosts = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getCosts: (...a: unknown[]) => mockGetCosts(...a) },
}));

const SUMMARY = {
  success: true,
  summary: {
    total_cost_usd: 1.2345,
    total_input_tokens: 400_000,
    total_output_tokens: 100_000,
    total_cache_read_tokens: 500_000,
    total_cache_creation_tokens: 0,
    // The REAL contract: /api/agents/{id}/costs buckets by call_type into two
    // synthetic keys, it never returns model ids (backend cost.py). The first
    // version of this fixture used plausible model ids, which is precisely how
    // a raw `__main_model__` reached the live account page.
    by_model: {
      __main_model__: {
        cost: 1.0,
        input_tokens: 300_000,
        output_tokens: 90_000,
        cache_read_tokens: 500_000,
        cache_creation_tokens: 0,
        call_count: 42,
      },
      __helper_model__: {
        cost: 0.2345,
        input_tokens: 100_000,
        output_tokens: 10_000,
        cache_read_tokens: 0,
        cache_creation_tokens: 0,
        call_count: 7,
      },
    },
    daily: [],
  },
  records: [],
  total_count: 49,
};

const EMPTY = {
  success: true,
  summary: {
    total_cost_usd: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    by_model: {},
    daily: [],
  },
  records: [],
  total_count: 0,
};

beforeEach(() => {
  mockGetCosts.mockReset();
  mockGetCosts.mockResolvedValue(SUMMARY);
});

test('reads the viewer-wide ledger, not one agent', async () => {
  render(<NarraUsageSection />);
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalled());
  expect(mockGetCosts).toHaveBeenCalledWith('_all', 30);
});

test('shows the platform-scoped token total', async () => {
  render(<NarraUsageSection />);
  // 400k full-rate + 500k cache read + 0 cache write + 100k output = 1.00M.
  // Cache buckets MUST be in the input side or a cache-warm month under-reports.
  expect(await screen.findByText('1.00M')).toBeInTheDocument();
});

test('names NarraNexus as the scope, so the number cannot be read as the account total', async () => {
  render(<NarraUsageSection />);
  expect(
    await screen.findByText(/Used by NarraNexus · last 30 days/i),
  ).toBeInTheDocument();
});

test('labels the money as an estimate and says why it can differ from the bill', async () => {
  render(<NarraUsageSection />);
  expect(await screen.findByText(/≈ \$1\.23/)).toBeInTheDocument();
  // The caveat is the entire point of showing a dollar figure at all: list
  // price ≠ what an aggregator invoices (utils/model_pricing.py).
  expect(
    screen.getByText(/estimated from public list prices/i),
  ).toBeInTheDocument();
});

test('breaks down by bucket, busiest first, with no raw sentinel on screen', async () => {
  render(<NarraUsageSection />);
  const rows = await screen.findAllByTestId('narra-usage-model');
  expect(rows.map((r) => r.textContent)).toEqual([
    expect.stringContaining('Model usage'),
    expect.stringContaining('Helper Model Usage'),
  ]);
  // The bug this pins: rendering the by_model key verbatim.
  expect(screen.queryByText(/__main_model__|__helper_model__/)).toBeNull();
});

test('a real model id still renders (date suffix stripped) if the contract widens', async () => {
  mockGetCosts.mockResolvedValue({
    ...SUMMARY,
    summary: {
      ...SUMMARY.summary,
      by_model: {
        'claude-opus-5-2026-05-01': {
          cost: 1, input_tokens: 10, output_tokens: 10, call_count: 1,
        },
      },
    },
  });
  render(<NarraUsageSection />);
  expect(await screen.findByText('claude-opus-5')).toBeInTheDocument();
});

test('refreshes on window focus — the rest of the card does, and usage accrues elsewhere', async () => {
  render(<NarraUsageSection />);
  expect(await screen.findByText('1.00M')).toBeInTheDocument();
  mockGetCosts.mockResolvedValue({
    ...SUMMARY,
    summary: { ...SUMMARY.summary, total_output_tokens: 200_000 },
  });
  fireEvent(window, new Event('focus'));
  expect(await screen.findByText('1.10M')).toBeInTheDocument();
});

test('a failed refresh keeps the last good value instead of blanking', async () => {
  render(<NarraUsageSection />);
  expect(await screen.findByText('1.00M')).toBeInTheDocument();
  mockGetCosts.mockRejectedValue(new Error('502'));
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalledTimes(2));
  expect(screen.getByText('1.00M')).toBeInTheDocument();
});

test('concurrent focus events collapse to one read — this endpoint scans the whole ledger', async () => {
  let resolveFirst: (v: unknown) => void = () => {};
  mockGetCosts.mockReturnValueOnce(new Promise((r) => { resolveFirst = r; }));
  render(<NarraUsageSection />);
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalledTimes(1));
  // Two alt-tabs while the first read is still in the air.
  fireEvent(window, new Event('focus'));
  fireEvent(window, new Event('focus'));
  expect(mockGetCosts).toHaveBeenCalledTimes(1);
  // Once it lands, the next focus is served normally — this is a guard, not a
  // one-shot latch.
  resolveFirst(SUMMARY);
  expect(await screen.findByText('1.00M')).toBeInTheDocument();
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalledTimes(2));
});

test('renders nothing when the ledger is empty — no "$0.00, so it must be free"', async () => {
  mockGetCosts.mockResolvedValue(EMPTY);
  const { container } = render(<NarraUsageSection />);
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test('a failed fetch is silent — this section must never blank the billing card', async () => {
  mockGetCosts.mockRejectedValue(new Error('boom'));
  const { container } = render(<NarraUsageSection />);
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test('survives a payload with no summary at all', async () => {
  mockGetCosts.mockResolvedValue({ success: true, records: [], total_count: 0 });
  const { container } = render(<NarraUsageSection />);
  await waitFor(() => expect(mockGetCosts).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test('hides the cost line when every model is unpriced, rather than claiming $0', async () => {
  mockGetCosts.mockResolvedValue({
    ...SUMMARY,
    summary: { ...SUMMARY.summary, total_cost_usd: 0 },
  });
  render(<NarraUsageSection />);
  // Tokens still render — they are measured, not priced.
  expect(await screen.findByText('1.00M')).toBeInTheDocument();
  expect(screen.queryByText(/≈ \$/)).not.toBeInTheDocument();
  expect(screen.queryByText(/estimated from public list prices/i)).not.toBeInTheDocument();
});
