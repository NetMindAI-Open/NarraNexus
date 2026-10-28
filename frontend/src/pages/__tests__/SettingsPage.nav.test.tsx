/**
 * Nav test for SettingsPage. Settings is the single configuration front
 * door: Personalization (theme/language) and Account (billing /
 * subscription — inline pane, the left nav stays visible) live here;
 * bundle entries live in the sidebar. ?tab=account (Stripe's post-payment
 * return target) must open the Account pane in place. Heavy content panels
 * are stubbed so the test only exercises the nav.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const { mockT } = vi.hoisted(() => {
  const copy: Record<string, string> = {
    'pages.settings.nav.providers': 'LLM Providers',
    'pages.settings.nav.artifacts': 'Artifacts',
    'pages.settings.nav.modelDefaults': 'Model Defaults',
  };
  return { mockT: (key: string) => copy[key] ?? key };
});

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: mockT }) }));
let mockSearch = '';
vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(mockSearch), vi.fn()] as const,
}));
const { authState, cloudState } = vi.hoisted(() => ({
  authState: { netmindToken: null as string | null },
  cloudState: { forced: false },
}));
vi.mock('@/stores', () => ({
  useConfigStore: (sel: (s: { netmindToken: string | null }) => unknown) =>
    sel({ netmindToken: authState.netmindToken }),
}));
vi.mock('@/lib/runtimeConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/runtimeConfig')>();
  return { ...actual, isForcedCloud: () => cloudState.forced };
});
vi.mock('@/components/settings/PersonalizationSettings', () => ({
  PersonalizationSettings: () => <div data-testid="personalization-pane" />,
}));
vi.mock('@/components/settings/NetmindAccountPanel', () => ({
  NetmindAccountPanel: () => <div data-testid="account-pane" />,
}));
vi.mock('@/components/settings/ProviderSettings', () => ({
  ProviderSettings: () => <div data-testid="providers-pane" />,
}));
vi.mock('@/components/settings/ModelDefaultsSettings', () => ({ ModelDefaultsSettings: () => <div /> }));
vi.mock('@/components/settings/PrivacySettings', () => ({
  PrivacySettings: () => <div data-testid="privacy-pane" />,
}));
vi.mock('@/components/settings/BrowserSettings', () => ({ default: () => <div data-testid="browser-pane" /> }));
vi.mock('@/components/settings/ArtifactsSection', () => ({
  default: () => <div data-testid="artifacts-pane" />,
}));
vi.mock('@/components/settings/PluginsSettings', () => ({
  PluginsSettings: () => <div data-testid="plugins-pane" />,
}));
vi.mock('@/lib/tauri', () => ({ isTauri: () => false, kickUpdaterCheck: vi.fn(), restartForUpdate: vi.fn() }));
vi.mock('@/stores/updaterStore', () => ({ useUpdaterStore: (sel: (s: unknown) => unknown) => sel({ status: 'idle' }) }));

import SettingsPage from '../SettingsPage';
import { PANELS, SETTINGS_SECTIONS, enableOwner } from '@/platform/registries';
import { disableBuiltinUi } from '@/platform/loader';

const browserPanel = PANELS.get('browser')!;
const browserSection = SETTINGS_SECTIONS.get('browser')!;
const panelOwner = PANELS.ownerOf('browser');
const sectionOwner = SETTINGS_SECTIONS.ownerOf('browser');
afterEach(() => {
  cleanup();
  enableOwner('builtin.browser');
  PANELS.register('browser', browserPanel, { owner: panelOwner, replace: true });
  SETTINGS_SECTIONS.register('browser', browserSection, { owner: sectionOwner, replace: true });
});
import {
  MASTER_DETAIL_ROW_CLASS,
  MASTER_NAV_CLASS,
  MASTER_NAV_ITEM_CLASS,
} from '@/lib/masterDetailNav';

describe('SettingsPage nav', () => {
  beforeEach(() => {
    mockSearch = '';
    authState.netmindToken = null;
    cloudState.forced = false;
  });

  test('plugins nav is present in local mode', () => {
    render(<SettingsPage />);
    expect(screen.getByRole('button', { name: /pages.settings.nav.plugins/ })).toBeTruthy();
  });

  test('plugins nav is hidden in cloud mode (frameworks pre-installed there)', () => {
    cloudState.forced = true;
    render(<SettingsPage />);
    expect(screen.queryByRole('button', { name: /pages.settings.nav.plugins/ })).toBeNull();
  });

  test('cloud + ?tab=plugins falls back to the first visible pane, not an empty one', () => {
    cloudState.forced = true;
    mockSearch = 'tab=plugins';
    render(<SettingsPage />);
    // The deep link targeted a pane the cloud session cannot see → fall back
    // to the first item (providers), NOT the empty plugins pane.
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
    expect(screen.queryByTestId('plugins-pane')).toBeNull();
  });

  test('bundle entries are gone; account shows a sign-in hint without a NetMind session', () => {
    render(<SettingsPage />);
    expect(screen.queryByRole('button', { name: /bundle/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /pages.settings.nav.account/ }));
    expect(screen.getByText('pages.account.powerOnlyHint')).toBeTruthy();
    expect(screen.queryByTestId('account-pane')).toBeNull();
  });

  test('account is a PANE for NetMind users — the left nav must survive opening it', () => {
    authState.netmindToken = 'tok';
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /pages.settings.nav.account/ }));
    expect(screen.getByTestId('account-pane')).toBeTruthy();
    expect(screen.queryByTestId('providers-pane')).toBeNull();
    // The tab list is still there: switching back works without leaving.
    fireEvent.click(screen.getByRole('button', { name: /LLM Providers/ }));
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
  });

  test('the nav column collapses to a horizontal strip below md (GitHub #130)', () => {
    // The rail's responsive classes live in one shared definition
    // (lib/masterDetailNav) so this page and its sibling cannot drift; the
    // breakpoint pin itself is asserted in masterDetailNav.test.ts.
    render(<SettingsPage />);
    const nav = screen.getByRole('navigation');
    expect(nav.className).toBe(MASTER_NAV_CLASS);
    expect(nav.parentElement?.className).toBe(MASTER_DETAIL_ROW_CLASS);
    const item = nav.querySelector('button');
    expect(item?.className.split(/\s+/)).toEqual(
      expect.arrayContaining(MASTER_NAV_ITEM_CLASS.split(' ')),
    );
  });

  test('personalization pane opens from the nav', () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /pages.settings.nav.personalization/ }));
    expect(screen.getByTestId('personalization-pane')).toBeTruthy();
    expect(screen.queryByTestId('providers-pane')).toBeNull();
  });
});

// ── ?tab= deep link ─────────────────────────────────────────────────────────
// Stripe drops the payer on /app/settings?tab=account&status=… (backend
// billing.py::_return_urls). Account lives on /app/account now, so that URL
// must forward there with the whole query preserved — landing the payer on a
// random Settings pane would read as "my payment went nowhere".
describe('SettingsPage ?tab= deep link', () => {
  test('a disabled browser deep link falls back to providers without a Browser entry', () => {
    disableBuiltinUi('builtin.browser');
    mockSearch = 'tab=browser';
    render(<SettingsPage />);
    expect(screen.queryByRole('button', { name: 'pages.settings.nav.browser' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('browser-pane')).not.toBeInTheDocument();
    expect(screen.getByTestId('providers-pane')).toBeInTheDocument();
  });

  test('tab=account opens the account pane in place — Stripe returns land with the nav intact', () => {
    authState.netmindToken = 'tok';
    mockSearch = 'tab=account&status=success';
    render(<SettingsPage />);
    expect(screen.getByTestId('account-pane')).toBeTruthy();
  });

  test('opens the pane named in the URL, not the default first item', () => {
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    expect(screen.getByTestId('artifacts-pane')).toBeTruthy();
    expect(screen.queryByTestId('providers-pane')).toBeNull();
  });

  test('unknown tab falls back to the first visible item', () => {
    mockSearch = 'tab=not-a-pane';
    render(<SettingsPage />);
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
  });

  test('tab=privacy opens the privacy pane — the telemetry notice deep-links here', () => {
    // TelemetryNotice navigates to /app/settings?tab=privacy; if this
    // ever falls back to the first pane, "turn it off in settings"
    // becomes a dead promise.
    mockSearch = 'tab=privacy';
    render(<SettingsPage />);
    expect(screen.getByTestId('privacy-pane')).toBeTruthy();
  });

  test('the URL seeds the pane but does not lock it — nav clicks still work', () => {
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /LLM Providers/ }));
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
    expect(screen.queryByTestId('artifacts-pane')).toBeNull();
  });
});
