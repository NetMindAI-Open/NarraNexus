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

const { mockT } = vi.hoisted(() => {
  const copy: Record<string, string> = {
    'pages.settings.modelDefaults.agentMain': 'Agent (main dialogue)',
  };
  return {
    mockT: (key: string, fallback?: unknown) =>
      copy[key] ?? (typeof fallback === 'string' ? fallback : key),
  };
});

// i18n: stable translator identity prevents effect dependencies from changing
// on every render; selected locale keys resolve to their English test copy.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: mockT }),
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
const mockGetMyQuota = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    getAgentFramework: (...a: unknown[]) => mockGetAgentFramework(...a),
    setAgentFramework: (...a: unknown[]) => mockSetAgentFramework(...a),
    getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a),
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
  // The platform-funded card. Same NetMind capacity, different source — which
  // is exactly what the inlined `!== 'netmind'` filters used to exclude.
  p_free_a: {
    provider_id: 'p_free_a',
    name: 'Free Tier (Anthropic)',
    source: 'netmind_free',
    protocol: 'anthropic',
    auth_type: 'bearer_token',
    is_active: true,
    models: ['deepseek-ai/DeepSeek-V4-Pro'],
  },
  p_free_o: {
    provider_id: 'p_free_o',
    name: 'Free Tier (OpenAI)',
    source: 'netmind_free',
    protocol: 'openai',
    auth_type: 'api_key',
    is_active: true,
    models: ['deepseek-ai/DeepSeek-V4-Flash'],
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
  // Default: free tier not active (local/exhausted) — panel behaves as before.
  mockGetMyQuota.mockReset().mockResolvedValue({ enabled: false });
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

test('free tier never preempts these defaults — no lock banner, controls live', async () => {
  // The free tier is an ordinary provider card now: what is set here is what
  // runs, on the free wallet just as on a user's own key. The old "your choice
  // is ignored until the free tier runs out" banner would be a lie.
  mockGetMyQuota.mockResolvedValue({
    enabled: true,
    status: 'active',
    currency: 'USD',
    max_budget: 10,
    spend: 1,
    remaining: 9,
  });
  await renderLoaded();
  expect(screen.queryByText('chat.model.freeTierBanner')).toBeNull();
  expect(frameworkSelect()).not.toBeDisabled();
});

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

  // A CLI-backed framework is staff-only on cloud (backend 403s it: it
  // would sign in through the image's shared CLI login). The select stays
  // interactive, but the pick pops the styled notice dialog (useConfirm
  // alert), snaps back, and never calls the API.
  const select = frameworkSelect();
  expect(select).not.toBeDisabled();
  fireEvent.change(select, { target: { value: 'codex_cli' } });
  expect(screen.getByText('Staff only in cloud')).toBeInTheDocument();
  expect(
    screen.getByText(/signs in through a shared CLI login/),
  ).toBeInTheDocument();
  expect(select.value).toBe('claude_code');
  expect(mockSetAgentFramework).not.toHaveBeenCalled();

  // OK dismisses the notice.
  fireEvent.click(screen.getByRole('button', { name: 'OK' }));
  expect(screen.queryByText('Staff only in cloud')).toBeNull();
});

test('cloud non-staff CAN select NexusPower — it runs on their own key', async () => {
  // The gate is about credential riding, not framework variety: NexusPower
  // drives the provider API with the key of the card bound to the agent
  // slot and refuses subscription OAuth, so cloud is free to offer it.
  // This case is why the rule became a shared predicate — the old inlined
  // `!== 'claude_code'` rejected it here and in AgentLlmConfigPanel.
  mockForcedCloud = true;
  await renderLoaded();

  const select = frameworkSelect();
  fireEvent.change(select, { target: { value: 'nexus_power' } });

  expect(screen.queryByText('Staff only in cloud')).toBeNull();
  expect(mockSetAgentFramework).toHaveBeenCalledWith('nexus_power');
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

test('cloud non-staff can select the free-tier card in both slots', async () => {
  // The bug this pins: `p.source !== 'netmind'` was inlined in four filters,
  // so when the free tier gained its own source the card was registered,
  // bound and working — yet invisible in every provider dropdown.
  mockForcedCloud = true;
  await renderLoaded();

  expect(screen.getAllByRole('option', { name: 'Free Tier (Anthropic)' }).length)
    .toBeGreaterThan(0);
  expect(screen.getAllByRole('option', { name: 'Free Tier (OpenAI)' }).length)
    .toBeGreaterThan(0);
  // ...and the cloud policy still holds: a bring-your-own key stays hidden.
  expect(screen.queryByRole('option', { name: 'My Anthropic Key' })).toBeNull();
});
