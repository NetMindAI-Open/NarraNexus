/**
 * @file QuotaPanel.test.tsx
 * @description #48 toggle-lock regression. The "Use free quota" checkbox must
 * only be locked when the free tier is exhausted AND already off (turning it
 * ON needs budget). Turning it OFF — to route through your own provider — must
 * ALWAYS be allowed, otherwise an opted-in user is trapped when they run out
 * (exhausted → greyed → cannot uncheck → 402 loop). Before the fix this was
 * `disabled={exhausted}`, which locked BOTH directions.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { QuotaPanel } from '../QuotaPanel';

vi.mock('react-i18next', () => ({
  // Return the key for interpolation calls (2nd arg is an options object, not
  // a string) so nothing tries to render an object as a React child.
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

vi.mock('@/stores/runtimeStore', () => ({
  useRuntimeStore: (sel: (s: { mode: string }) => unknown) =>
    sel({ mode: 'cloud-web' }),
}));

const mockGetMyQuota = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a) },
}));

function quota(overrides: Record<string, unknown>) {
  return {
    enabled: true,
    status: 'active',
    prefer_system_override: true,
    remaining_input_tokens: 1000,
    remaining_output_tokens: 1000,
    initial_input_tokens: 1000,
    initial_output_tokens: 1000,
    granted_input_tokens: 0,
    granted_output_tokens: 0,
    used_input_tokens: 0,
    used_output_tokens: 0,
    ...overrides,
  };
}

beforeEach(() => {
  mockGetMyQuota.mockReset();
});

test('exhausted + opted-in: toggle can still be turned OFF (not disabled)', async () => {
  mockGetMyQuota.mockResolvedValue(
    quota({ status: 'exhausted', prefer_system_override: true, used_input_tokens: 2000 }),
  );
  render(<QuotaPanel />);
  const cb = await screen.findByRole('checkbox');
  expect(cb).toBeChecked();
  expect(cb).not.toBeDisabled(); // the fix: escape hatch stays clickable
});

test('exhausted + opted-out: toggle is disabled (cannot re-enable without budget)', async () => {
  mockGetMyQuota.mockResolvedValue(
    quota({ status: 'exhausted', prefer_system_override: false, used_input_tokens: 2000 }),
  );
  render(<QuotaPanel />);
  const cb = await screen.findByRole('checkbox');
  expect(cb).not.toBeChecked();
  expect(cb).toBeDisabled();
});

test('healthy quota: toggle always interactive', async () => {
  mockGetMyQuota.mockResolvedValue(
    quota({ status: 'active', prefer_system_override: true }),
  );
  render(<QuotaPanel />);
  const cb = await screen.findByRole('checkbox');
  expect(cb).not.toBeDisabled();
});
