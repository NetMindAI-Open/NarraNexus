/**
 * Nav-gating test for SettingsPage: the "Account & Subscription" entry is
 * powerOnly — present iff the session holds a NetMind loginToken. Heavy content
 * panels are stubbed so the test only exercises the left-nav filter.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const { mockT } = vi.hoisted(() => {
  const copy: Record<string, string> = {
    'pages.settings.nav.account': 'Account & Subscription',
    'pages.settings.nav.providers': 'LLM Providers',
    'pages.settings.nav.artifacts': 'Artifacts',
  };
  return { mockT: (key: string) => copy[key] ?? key };
});

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: mockT }) }));
let mockSearch = '';
vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams(mockSearch), vi.fn()] as const,
}));
vi.mock('@/components/settings/ProviderSettings', () => ({
  ProviderSettings: () => <div data-testid="providers-pane" />,
}));
vi.mock('@/components/settings/ModelDefaultsSettings', () => ({ ModelDefaultsSettings: () => <div /> }));
vi.mock('@/components/settings/NetmindAccountPanel', () => ({
  NetmindAccountPanel: () => <div data-testid="account-pane" />,
}));
vi.mock('@/components/settings/ArtifactsSection', () => ({
  default: () => <div data-testid="artifacts-pane" />,
}));
vi.mock('@/lib/tauri', () => ({ isTauri: () => false, kickUpdaterCheck: vi.fn(), restartForUpdate: vi.fn() }));
vi.mock('@/stores/updaterStore', () => ({ useUpdaterStore: (sel: (s: unknown) => unknown) => sel({ status: 'idle' }) }));

let mockNetmindToken = '';
vi.mock('@/stores/configStore', () => ({
  useConfigStore: (sel: (s: { netmindToken: string }) => unknown) => sel({ netmindToken: mockNetmindToken }),
}));

import SettingsPage from '../SettingsPage';

describe('SettingsPage nav — Account & Subscription is powerOnly', () => {
  beforeEach(() => {
    mockSearch = '';
  });

  test('hidden for a pure-local session (no NetMind token)', () => {
    mockNetmindToken = '';
    render(<SettingsPage />);
    expect(screen.queryByRole('button', { name: /Account & Subscription/ })).toBeNull();
    expect(screen.getByRole('button', { name: /LLM Providers/ })).toBeTruthy();
  });

  test('shown for a Power session (holds a NetMind token)', () => {
    mockNetmindToken = 'tok';
    render(<SettingsPage />);
    expect(screen.getByRole('button', { name: /Account & Subscription/ })).toBeTruthy();
  });
});

// ── ?tab= deep link (post-payment return target, 2026-07-30) ───────────────
// Stripe drops the payer on /app/settings?tab=account&status=…, so the URL — not
// just a click — has to be able to open a pane. Without this the payer lands on
// whatever pane happens to be first and reads it as "my payment went nowhere".
describe('SettingsPage ?tab= deep link', () => {
  test('opens the pane named in the URL, not the default first item', () => {
    mockNetmindToken = 'tok'; // → 'account' is items[0], so artifacts proves the URL won
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    expect(screen.getByTestId('artifacts-pane')).toBeTruthy();
    expect(screen.queryByTestId('account-pane')).toBeNull();
  });

  test('unknown tab falls back to the first visible item', () => {
    mockNetmindToken = 'tok';
    mockSearch = 'tab=not-a-pane';
    render(<SettingsPage />);
    expect(screen.getByTestId('account-pane')).toBeTruthy();
  });

  test('a tab the session cannot see falls back instead of opening a blank pane', () => {
    // powerOnly item requested by a non-Power session: 'account' is filtered
    // out of the nav, so honoring it would render an empty content area.
    mockNetmindToken = '';
    mockSearch = 'tab=account';
    render(<SettingsPage />);
    expect(screen.queryByTestId('account-pane')).toBeNull();
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
  });

  test('the URL seeds the pane but does not lock it — nav clicks still work', () => {
    mockNetmindToken = 'tok';
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /Account & Subscription/ }));
    expect(screen.getByTestId('account-pane')).toBeTruthy();
    expect(screen.queryByTestId('artifacts-pane')).toBeNull();
  });
});
