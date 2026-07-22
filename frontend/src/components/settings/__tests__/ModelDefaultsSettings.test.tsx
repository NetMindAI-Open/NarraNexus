/**
 * @file ModelDefaultsSettings.test.tsx
 * @description Cloud netmind-only policy on the Model Defaults editor: a
 * non-staff cloud user only sees NetMind-source providers in both slot
 * dropdowns plus the "own keys are local-version only" note; staff and
 * local keep the full provider list and no note. api + i18n + configStore +
 * runtimeConfig are mocked — no network.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ModelDefaultsSettings } from '../ModelDefaultsSettings';
import { DESKTOP_RELEASES_URL } from '@/lib/agentFramework';

// i18n: return the inline default string (2nd arg) so assertions read real copy.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: unknown) => (typeof d === 'string' ? d : _k),
  }),
}));

let mockRole = 'user';
vi.mock('@/stores/configStore', () => ({
  useConfigStore: (sel: (s: { role: string }) => unknown) => sel({ role: mockRole }),
}));

let mockForcedCloud = false;
vi.mock('@/lib/runtimeConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/runtimeConfig')>();
  return { ...actual, isForcedCloud: () => mockForcedCloud };
});

const mockGetProviders = vi.fn();
const mockGetAgentFramework = vi.fn();
const mockSetAgentFramework = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    getAgentFramework: (...a: unknown[]) => mockGetAgentFramework(...a),
    setAgentFramework: (...a: unknown[]) => mockSetAgentFramework(...a),
  },
}));

const PROVIDERS = {
  p_nm: {
    provider_id: 'p_nm',
    name: 'NetMind (Anthropic)',
    source: 'netmind',
    protocol: 'anthropic',
    auth_type: 'bearer_token',
    is_active: true,
    models: ['claude-opus-4-8'],
  },
  p_own: {
    provider_id: 'p_own',
    name: 'My Anthropic Key',
    source: 'user',
    protocol: 'anthropic',
    auth_type: 'api_key',
    is_active: true,
    models: ['claude-opus-4-8'],
  },
};

beforeEach(() => {
  mockRole = 'user';
  mockForcedCloud = false;
  mockGetProviders.mockReset().mockResolvedValue({
    success: true,
    data: { providers: PROVIDERS, slots: {} },
  });
  mockGetAgentFramework.mockReset().mockResolvedValue({
    success: true,
    data: { framework: 'claude_code', probe: { ok: true, detail: '' } },
  });
  mockSetAgentFramework.mockReset().mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function frameworkSelect(): HTMLSelectElement {
  return screen
    .getAllByRole('combobox')
    .find((el) => el.querySelector('option[value="claude_code"]')) as HTMLSelectElement;
}

async function renderLoaded() {
  render(<ModelDefaultsSettings />);
  await waitFor(() =>
    expect(screen.getByText('Agent (main dialogue)')).toBeInTheDocument(),
  );
}

test('cloud non-staff: only NetMind providers are offered + local-version note', async () => {
  mockForcedCloud = true;
  await renderLoaded();

  // Both slot dropdowns list the netmind card only.
  expect(screen.getAllByRole('option', { name: 'NetMind (Anthropic)' })).toHaveLength(2);
  expect(screen.queryByRole('option', { name: 'My Anthropic Key' })).toBeNull();

  // Bottom note + download link.
  expect(
    screen.getByText(/models from your own API keys are not available here/),
  ).toBeInTheDocument();
  const link = screen.getByRole('link', {
    name: /Download the local desktop version/,
  });
  expect(link).toHaveAttribute('href', DESKTOP_RELEASES_URL);

  // Framework switching is staff-only on cloud (backend 403s it) — the
  // select stays interactive, but picking a different framework pops the
  // styled notice dialog (useConfirm alert), snaps back, and never calls
  // the API.
  const select = frameworkSelect();
  expect(select).not.toBeDisabled();
  fireEvent.change(select, { target: { value: 'codex_cli' } });
  expect(screen.getByText('Desktop version only')).toBeInTheDocument();
  expect(
    screen.getByText(/Switching the agent framework is available/),
  ).toBeInTheDocument();
  expect(select.value).toBe('claude_code');
  expect(mockSetAgentFramework).not.toHaveBeenCalled();

  // OK dismisses the notice.
  fireEvent.click(screen.getByRole('button', { name: 'OK' }));
  expect(screen.queryByText('Desktop version only')).toBeNull();
});

test('cloud staff keeps the full provider list and no note', async () => {
  mockForcedCloud = true;
  mockRole = 'staff';
  await renderLoaded();

  expect(screen.getAllByRole('option', { name: 'My Anthropic Key' })).toHaveLength(2);
  expect(
    screen.queryByText(/models from your own API keys are not available here/),
  ).toBeNull();
  // Staff switches frameworks freely — no notice dialog, API called.
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  expect(screen.queryByText('Desktop version only')).toBeNull();
  expect(mockSetAgentFramework).toHaveBeenCalledWith('codex_cli');
});

test('local stays fully open and shows no note', async () => {
  await renderLoaded();

  expect(screen.getAllByRole('option', { name: 'My Anthropic Key' })).toHaveLength(2);
  expect(screen.getAllByRole('option', { name: 'NetMind (Anthropic)' })).toHaveLength(2);
  expect(
    screen.queryByText(/models from your own API keys are not available here/),
  ).toBeNull();
});
