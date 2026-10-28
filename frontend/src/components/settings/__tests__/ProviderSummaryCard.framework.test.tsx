/**
 * @file_name: ProviderSummaryCard.framework.test.tsx
 * @author:
 * @date: 2026-09-11
 * @description: The summary card names the framework the backend reports and
 * nothing else — when that answer is missing it shows "—", never a hardcoded
 * "Claude Code" (which on the lightweight build may not even be installed).
 */
import { beforeEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

let fwResponse: unknown = null;

vi.mock('@/lib/api', () => ({
  api: {
    getProviders: () =>
      Promise.resolve({
        success: true,
        data: {
          providers: {
            p1: { provider_id: 'p1', name: 'Key One', is_active: true },
          },
          slots: { agent: { config: { provider_id: 'p1', model: 'model-x' } } },
        },
      }),
    getAgentFramework: () => Promise.resolve(fwResponse),
  },
}));

import { ProviderSummaryCard } from '../ProviderSummaryCard';

beforeEach(() => {
  fwResponse = null;
});

test('no framework answer → "—", not a hardcoded Claude Code', async () => {
  fwResponse = { success: false };
  render(<ProviderSummaryCard />);
  expect(await screen.findByText('— · Key One')).toBeInTheDocument();
  expect(screen.queryByText(/Claude Code/)).toBeNull();
});

test('the backend-reported framework is shown by its live display name', async () => {
  fwResponse = {
    success: true,
    data: { framework: 'nexus_power', frameworks: [{ name: 'nexus_power', display_name: 'NexusPower-beta' }] },
  };
  render(<ProviderSummaryCard />);
  expect(await screen.findByText('NexusPower-beta · Key One')).toBeInTheDocument();
});
