/**
 * Nav-gating test for SettingsPage: the "Account & Subscription" entry is
 * powerOnly — present iff the session holds a NetMind loginToken. Heavy content
 * panels are stubbed so the test only exercises the left-nav filter.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }) }));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('@/components/settings/ProviderSettings', () => ({ ProviderSettings: () => <div /> }));
vi.mock('@/components/settings/ModelDefaultsSettings', () => ({ ModelDefaultsSettings: () => <div /> }));
vi.mock('@/components/settings/NetmindAccountPanel', () => ({ NetmindAccountPanel: () => <div /> }));
vi.mock('@/components/settings/ArtifactsSection', () => ({ default: () => <div /> }));
vi.mock('@/lib/tauri', () => ({ isTauri: () => false, kickUpdaterCheck: vi.fn(), restartForUpdate: vi.fn() }));
vi.mock('@/stores/updaterStore', () => ({ useUpdaterStore: (sel: (s: unknown) => unknown) => sel({ status: 'idle' }) }));

let mockNetmindToken = '';
vi.mock('@/stores/configStore', () => ({
  useConfigStore: (sel: (s: { netmindToken: string }) => unknown) => sel({ netmindToken: mockNetmindToken }),
}));

import SettingsPage from '../SettingsPage';

describe('SettingsPage nav — Account & Subscription is powerOnly', () => {
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
