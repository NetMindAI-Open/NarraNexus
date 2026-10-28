/**
 * PrivacySettings — the two consent toggles' load/flip/guard behavior.
 * The telemetry half's contract: `controllable=false` renders a DISABLED
 * switch with the managed note (never hides it — an invisible switch
 * reads as "there is no telemetry", which would be false).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';

const { mockApi, mockWebAnalytics } = vi.hoisted(() => ({
  mockApi: {
    getAnalyticsOptOut: vi.fn(),
    setAnalyticsOptOut: vi.fn(),
    getTelemetryConsent: vi.fn(),
    setTelemetryOptOut: vi.fn(),
  },
  mockWebAnalytics: {
    isWebAnalyticsLoaded: vi.fn(() => false),
    markWebAnalyticsConsentRevoked: vi.fn(),
  },
}));
vi.mock('@/lib/api', () => ({ api: mockApi }));
vi.mock('@/lib/analytics/webAnalytics', () => mockWebAnalytics);

// Restore the real jsdom Location after any test that swaps in a { reload } stub.
let realLocation: PropertyDescriptor | undefined;
beforeAll(() => {
  realLocation = Object.getOwnPropertyDescriptor(window, 'location');
});
afterAll(() => {
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
});
afterEach(() => {
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { PrivacySettings } from '../PrivacySettings';

describe('PrivacySettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getAnalyticsOptOut.mockResolvedValue(false);
    mockApi.setAnalyticsOptOut.mockResolvedValue(undefined);
    mockApi.setTelemetryOptOut.mockResolvedValue(undefined);
    mockWebAnalytics.isWebAnalyticsLoaded.mockReturnValue(false);
  });

  test('telemetry on + controllable: toggle writes, then RECONCILES from the server', async () => {
    mockApi.getTelemetryConsent
      .mockResolvedValueOnce({
        mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
      })
      .mockResolvedValueOnce({
        mode: 'off', source: 'optout', opted_out: true, controllable: true, managed_by: null,
      });
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setTelemetryOptOut).toHaveBeenCalledWith(true),
    );
    // The landed state comes from the second GET, never a client guess.
    await waitFor(() =>
      expect(mockApi.getTelemetryConsent).toHaveBeenCalledTimes(2),
    );
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'));
  });

  test('failed write says so and lands on the server truth', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
    });
    mockApi.setTelemetryOptOut.mockRejectedValue(new Error('boom'));
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    // A silently-failed opt-out is the worst privacy failure: the
    // error note must appear, and the switch stays on the true state.
    await waitFor(() =>
      expect(
        screen.getByText('pages.settings.privacy.telemetryError'),
      ).toBeTruthy(),
    );
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  test('consent fetch failure never renders a definite "off"', async () => {
    mockApi.getTelemetryConsent.mockRejectedValue(new Error('401'));
    render(<PrivacySettings />);
    // The unavailable note replaces the row — an unchecked switch here
    // would read "telemetry is off" while it may be on and shipping.
    await waitFor(() =>
      expect(
        screen.getByText('pages.settings.privacy.telemetryUnavailable'),
      ).toBeTruthy(),
    );
    expect(
      screen.queryByRole('switch', {
        name: 'pages.settings.privacy.telemetryTitle',
      }),
    ).toBeNull();
  });

  test('cloud-managed install names the administrator, not an env var', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: false, managed_by: 'cloud',
    });
    render(<PrivacySettings />);
    await waitFor(() =>
      expect(
        screen.getByText('pages.settings.privacy.telemetryManagedCloud'),
      ).toBeTruthy(),
    );
  });

  test('env-managed install: switch disabled, managed note shown, no writes', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'env', opted_out: false, controllable: false, managed_by: 'env',
    });
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() =>
      expect(
        screen.getByText('pages.settings.privacy.telemetryManaged'),
      ).toBeTruthy(),
    );
    expect((toggle as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(toggle);
    expect(mockApi.setTelemetryOptOut).not.toHaveBeenCalled();
  });

  test('analytics toggle round-trips through the per-user endpoint', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
    });
    mockApi.getAnalyticsOptOut.mockResolvedValue(true); // opted out
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.analyticsTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setAnalyticsOptOut).toHaveBeenCalledWith(false),
    );
  });

  const TELEMETRY_META = {
    mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
  } as const;

  async function turnAnalyticsOff() {
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.analyticsTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setAnalyticsOptOut).toHaveBeenCalledWith(true),
    );
  }

  test('turning analytics OFF reloads to shed GTM when it actually loaded', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue(TELEMETRY_META);
    mockApi.getAnalyticsOptOut.mockResolvedValue(false); // opted in → toggle ON
    mockWebAnalytics.isWebAnalyticsLoaded.mockReturnValue(true);
    const reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { reload } });
    await turnAnalyticsOff();
    expect(mockWebAnalytics.markWebAnalyticsConsentRevoked).toHaveBeenCalled();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('turning analytics OFF does NOT reload when GTM never loaded', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue(TELEMETRY_META);
    mockApi.getAnalyticsOptOut.mockResolvedValue(false);
    mockWebAnalytics.isWebAnalyticsLoaded.mockReturnValue(false);
    const reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { reload } });
    await turnAnalyticsOff();
    expect(reload).not.toHaveBeenCalled();
  });

  test('a failed opt-out does NOT reload (revert path)', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue(TELEMETRY_META);
    mockApi.getAnalyticsOptOut.mockResolvedValue(false);
    mockApi.setAnalyticsOptOut.mockRejectedValue(new Error('nope'));
    mockWebAnalytics.isWebAnalyticsLoaded.mockReturnValue(true);
    const reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { reload } });
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.analyticsTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setAnalyticsOptOut).toHaveBeenCalledWith(true),
    );
    expect(reload).not.toHaveBeenCalled();
  });
});
