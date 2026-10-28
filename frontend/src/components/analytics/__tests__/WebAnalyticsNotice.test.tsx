/**
 * WebAnalyticsNotice — the four gates that keep the disclosure truthful:
 * it must appear ONLY where GTM actually runs (official host, not Tauri, an id
 * configured, user not opted out). The whole value of the component is not
 * showing "we send your page data to Google" where that is false.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const { mockApi, mockCfg, mockTauri } = vi.hoisted(() => ({
  mockApi: { getAnalyticsOptOut: vi.fn() },
  mockCfg: { getWebAnalyticsConfig: vi.fn() },
  mockTauri: { isTauri: vi.fn(() => false) },
}));
vi.mock('@/lib/api', () => ({ api: mockApi }));
vi.mock('@/lib/runtimeConfig', () => mockCfg);
vi.mock('@/lib/tauri', () => mockTauri);
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

import { WebAnalyticsNotice } from '../WebAnalyticsNotice';

const SEEN_KEY = 'web_analytics_disclosure_seen_v1';

describe('WebAnalyticsNotice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear(); // else case 5 writes '1' and later cases silently pass
    mockCfg.getWebAnalyticsConfig.mockReturnValue({ gtmId: 'GTM-TEST' });
    mockApi.getAnalyticsOptOut.mockResolvedValue(false);
    mockTauri.isTauri.mockReturnValue(false);
  });
  afterEach(() => localStorage.clear());

  test('not shown — and consent NOT queried — when GTM is unconfigured', async () => {
    mockCfg.getWebAnalyticsConfig.mockReturnValue({ gtmId: '' });
    const { container } = render(<WebAnalyticsNotice />);
    await Promise.resolve();
    expect(container.querySelector('[role="status"]')).toBeNull();
    // The gate ORDER is the thing under test: opt-out must not even be fetched.
    expect(mockApi.getAnalyticsOptOut).not.toHaveBeenCalled();
  });

  test('not shown inside the Tauri desktop build', async () => {
    mockTauri.isTauri.mockReturnValue(true);
    const { container } = render(<WebAnalyticsNotice />);
    await Promise.resolve();
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(mockApi.getAnalyticsOptOut).not.toHaveBeenCalled();
  });

  test('not shown when the user has opted out', async () => {
    mockApi.getAnalyticsOptOut.mockResolvedValue(true);
    const { container } = render(<WebAnalyticsNotice />);
    await waitFor(() => expect(mockApi.getAnalyticsOptOut).toHaveBeenCalled());
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  test('fail-closed: not shown when the opt-out lookup throws', async () => {
    mockApi.getAnalyticsOptOut.mockRejectedValue(new Error('nope'));
    const { container } = render(<WebAnalyticsNotice />);
    await waitFor(() => expect(mockApi.getAnalyticsOptOut).toHaveBeenCalled());
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  test('shown for an opted-in cloud user; dismiss records the seen key and hides it', async () => {
    render(<WebAnalyticsNotice />);
    await screen.findByText('webAnalyticsNotice.body');
    fireEvent.click(screen.getByText('webAnalyticsNotice.dismiss'));
    await waitFor(() =>
      expect(screen.queryByText('webAnalyticsNotice.body')).toBeNull(),
    );
    expect(localStorage.getItem(SEEN_KEY)).toBe('1');
  });

  test('stays hidden once the seen key is set (no re-nag)', async () => {
    localStorage.setItem(SEEN_KEY, '1');
    const { container } = render(<WebAnalyticsNotice />);
    await Promise.resolve();
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(mockApi.getAnalyticsOptOut).not.toHaveBeenCalled();
  });
});
