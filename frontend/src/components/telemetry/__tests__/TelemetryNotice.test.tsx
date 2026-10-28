/**
 * TelemetryNotice — one-time disclosure gating. localStorage records
 * "shown", never consent; the notice appears only when telemetry is
 * actually active for this install.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const { mockApi, mockNavigate } = vi.hoisted(() => ({
  mockApi: { getTelemetryConsent: vi.fn() },
  mockNavigate: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ api: mockApi }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));

import { TelemetryNotice } from '../TelemetryNotice';

const KEY = 'telemetry_disclosure_seen_v1';

describe('TelemetryNotice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  test('shows once when telemetry is active, dismiss persists the flag', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
    });
    render(<TelemetryNotice />);
    await screen.findByText('telemetryNotice.body');
    fireEvent.click(screen.getByText('telemetryNotice.dismiss'));
    expect(localStorage.getItem(KEY)).toBe('1');
    expect(screen.queryByText('telemetryNotice.body')).toBeNull();
  });

  test('never renders again once seen', async () => {
    localStorage.setItem(KEY, '1');
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
    });
    const { container } = render(<TelemetryNotice />);
    await waitFor(() => expect(container.firstChild).toBeNull());
    expect(mockApi.getTelemetryConsent).not.toHaveBeenCalled();
  });

  test('telemetry off (env or opt-out): no notice due, flag NOT burned', async () => {
    // If the deployment ships off today and flips on later, the user
    // still deserves the disclosure then — so "seen" must not be set.
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'off', source: 'env', opted_out: false, controllable: false, managed_by: 'env',
    });
    const { container } = render(<TelemetryNotice />);
    await waitFor(() =>
      expect(mockApi.getTelemetryConsent).toHaveBeenCalled(),
    );
    expect(container.firstChild).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  test('managed install gets its own wording and NO settings button', async () => {
    // Promising "turn it off in settings" to a user whose switch is
    // disabled would be false — managed installs get bodyManaged.
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'env', opted_out: false, controllable: false, managed_by: 'env',
    });
    render(<TelemetryNotice />);
    await screen.findByText('telemetryNotice.bodyManaged');
    expect(screen.queryByText('telemetryNotice.settings')).toBeNull();
    fireEvent.click(screen.getByText('telemetryNotice.dismiss'));
    expect(localStorage.getItem(KEY)).toBe('1');
  });

  test('settings button deep-links to the privacy pane and dismisses', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'default', opted_out: false, controllable: true, managed_by: null,
    });
    render(<TelemetryNotice />);
    await screen.findByText('telemetryNotice.body');
    fireEvent.click(screen.getByText('telemetryNotice.settings'));
    expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=privacy');
    expect(localStorage.getItem(KEY)).toBe('1');
  });
});
