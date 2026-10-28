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
    'pages.settings.modelDefaults.helperTitle': 'Helper model',
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
const mockGetProviderCatalog = vi.fn();
const mockSetAgentFramework = vi.fn();
const mockGetMyQuota = vi.fn();
const mockSetProviderSlot = vi.fn();
const mockGetSlotOverrideStats = vi.fn();
const mockApplySlotsToAgents = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    getProviderCatalog: (...a: unknown[]) => mockGetProviderCatalog(...a),
    getAgentFramework: (...a: unknown[]) => mockGetAgentFramework(...a),
    setAgentFramework: (...a: unknown[]) => mockSetAgentFramework(...a),
    getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a),
    setProviderSlot: (...a: unknown[]) => mockSetProviderSlot(...a),
    getSlotOverrideStats: (...a: unknown[]) => mockGetSlotOverrideStats(...a),
    applySlotsToAgents: (...a: unknown[]) => mockApplySlotsToAgents(...a),
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

// The live `frameworks[]` list (B6, 2026-09-07 final cut) — `providerBacksFramework`
// now reads `protocol`/`oauth_source` straight off this instead of any hardcoded
// table, and fails closed with no list at all, so every test needs one.
const LIVE_FRAMEWORKS = [
  { name: 'claude_code', available: true, protocol: 'anthropic', oauth_source: 'claude_oauth' },
  { name: 'codex_cli', available: true, protocol: 'openai', oauth_source: 'codex_oauth' },
  { name: 'nexus_power', available: true, protocol: 'any', oauth_source: null },
];

beforeEach(() => {
  mockGetProviderCatalog.mockReset().mockResolvedValue({ success: true, onboard_defaults: {} });
  mockRole = 'user';
  mockForcedCloud = false;
  mockGetProviders.mockReset().mockResolvedValue({
    success: true,
    data: { providers: PROVIDERS, slots: {} },
  });
  mockGetAgentFramework.mockReset().mockResolvedValue({
    success: true,
    data: { framework: 'claude_code', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  mockSetAgentFramework.mockReset().mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null },
  });
  // Default: free tier not active (local/exhausted) — panel behaves as before.
  mockGetMyQuota.mockReset().mockResolvedValue({ enabled: false });
  mockSetProviderSlot.mockReset().mockResolvedValue({ success: true });
  mockGetSlotOverrideStats.mockReset().mockResolvedValue({
    success: true,
    data: { agent: 3, helper_llm: 0, total_agents: 5 },
  });
  mockApplySlotsToAgents.mockReset().mockResolvedValue({
    success: true,
    data: { cleared: { agent: 3 } },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// Every select is located by accessible name (block title + its own label),
// so inserting another select or renaming an option value cannot silently
// retarget a helper. The framework label may carry the auth-probe badge, hence
// the prefix match.
function frameworkSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: /^Agent \(main dialogue\) pages\.settings\.modelDefaults\.framework/,
  }) as HTMLSelectElement;
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
  expect(select.value).toBe('nexus_power');
  expect(screen.getByRole('button', { name: 'pages.settings.modelDefaults.saveDefaults' })).not.toBeDisabled();
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
  expect(screen.queryByText('Staff only in cloud')).toBeNull();
  expect(frameworkSelect().value).toBe('codex_cli');
});

test('local stays fully open and shows no note', async () => {
  await renderLoaded();

  expect(screen.getAllByRole('option', { name: 'My Anthropic Key' })).toHaveLength(2);
  expect(screen.getAllByRole('option', { name: 'NetMind (Anthropic)' })).toHaveLength(2);
  expect(
    screen.queryByText(/models from your own API keys are not available here/),
  ).toBeNull();
});

test('a wallet holding only a Claude Code Login offers only that framework', async () => {
  // The reported bug: with just a `claude auth login` card, NexusPower was
  // still offered — and picking it saved a binding that only failed mid-run
  // (nexus_power refuses subscription credentials). Nothing else can run on
  // that card, so nothing else is listed.
  mockGetProviders.mockResolvedValue({
    success: true,
    data: {
      providers: {
        p_login: {
          provider_id: 'p_login',
          name: 'Claude Code (OAuth)',
          source: 'claude_oauth',
          protocol: 'anthropic',
          auth_type: 'oauth',
          is_active: true,
          models: ['opus', 'haiku'],
        },
      },
      slots: {},
    },
  });
  await renderLoaded();

  const select = frameworkSelect();
  expect(
    [...select.querySelectorAll('option')].map((o) => o.value),
  ).toEqual(['claude_code']);
  expect(
    screen.getByText(/Only the frameworks your connected providers can actually run/),
  ).toBeInTheDocument();
  // ...and the login card is still selectable for the agent slot itself.
  expect(screen.getAllByRole('option', { name: 'Claude Code (OAuth)' }).length)
    .toBeGreaterThan(0);
});

test('a plugin-gated framework renders disabled but visible, and picking it pops a notice instead of saving', async () => {
  // Codex CLI is a local plugin the user hasn't installed yet: the option
  // must still be LISTED (never hidden — the user needs to see what to
  // install) but disabled, and a programmatic change event (which a native
  // `disabled` attribute doesn't stop) must still be rejected client-side.
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: {
      framework: 'claude_code',
      probe: { ok: true, detail: '' },
      frameworks: [
        { name: 'claude_code', available: true, protocol: 'anthropic', oauth_source: 'claude_oauth' },
        { name: 'codex_cli', available: false, protocol: 'openai', oauth_source: 'codex_oauth' },
        { name: 'nexus_power', available: true, protocol: 'any', oauth_source: null },
      ],
    },
  });
  await renderLoaded();

  const select = frameworkSelect();
  const codexOption = [...select.querySelectorAll('option')].find(
    (o) => o.value === 'codex_cli',
  ) as HTMLOptionElement;
  expect(codexOption).toBeDisabled();

  fireEvent.change(select, { target: { value: 'codex_cli' } });
  expect(screen.getByText('Plugin required')).toBeInTheDocument();
  expect(select.value).toBe('claude_code');
  expect(mockSetAgentFramework).not.toHaveBeenCalled();
});

test('a mixed wallet keeps every framework listed and shows no note', async () => {
  await renderLoaded();

  expect(
    [...frameworkSelect().querySelectorAll('option')].map((o) => o.value),
  ).toEqual(['claude_code', 'codex_cli', 'nexus_power']);
  expect(
    screen.queryByText(/Only the frameworks your connected providers can actually run/),
  ).toBeNull();
});

const SAVE_NAME = 'pages.settings.modelDefaults.saveDefaults';

function withBoundAgentSlot() {
  mockGetProviders.mockResolvedValue({
    success: true,
    data: {
      providers: PROVIDERS,
      slots: { agent: { config: { provider_id: 'p_own', model: 'claude-opus-4-8' } } },
    },
  });
}

test('changing only the framework makes the form dirty and Save persists it', async () => {
  // Owner bug 2026-09-11: picking another default framework left Save greyed
  // out — the framework was written behind the user's back on change and was
  // never part of the dirty state, so "Save" had nothing to save and the page
  // never said the choice had landed. The framework is now a draft like every
  // other field: nothing is written until Save.
  withBoundAgentSlot();
  mockSetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, install: null, slot_cleared: false },
  });
  await renderLoaded();

  const save = screen.getByRole('button', { name: SAVE_NAME });
  expect(save).toBeDisabled();
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  expect(frameworkSelect().value).toBe('nexus_power');
  expect(mockSetAgentFramework).not.toHaveBeenCalled();
  expect(save).not.toBeDisabled();

  // The post-save reload reads the now-stored framework back.
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  fireEvent.click(save);
  await waitFor(() => expect(mockSetAgentFramework).toHaveBeenCalledWith('nexus_power'));
  // nexus_power drives the bound anthropic key — the slot itself is untouched.
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
  // Reloaded state reflects the saved framework and the form is clean again.
  await waitFor(() => expect(screen.getByText(/pages\.settings\.modelDefaults\.saved/)).toBeInTheDocument());
});

test('picking the saved framework back makes the form clean again', async () => {
  withBoundAgentSlot();
  await renderLoaded();
  const save = screen.getByRole('button', { name: SAVE_NAME });
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  expect(save).not.toBeDisabled();
  fireEvent.change(frameworkSelect(), { target: { value: 'claude_code' } });
  expect(save).toBeDisabled();
});

test('a framework the bound provider cannot drive drops the provider from the draft; Save writes framework before the slot', async () => {
  withBoundAgentSlot();
  mockSetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null, slot_cleared: true },
  });
  await renderLoaded();

  const providerSelect = agentProviderSelect();
  expect(providerSelect.value).toBe('p_own');
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  // Codex only drives openai cards — the anthropic key cannot stay selected.
  expect(providerSelect.value).toBe('');

  // Pick an openai card → framework first (set_slot validates the provider
  // against the STORED framework), then the slot.
  fireEvent.change(providerSelect, { target: { value: 'p_free_o' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));
  await waitFor(() => expect(mockSetProviderSlot).toHaveBeenCalledWith('agent', expect.objectContaining({ provider_id: 'p_free_o' })));
  expect(mockSetAgentFramework).toHaveBeenCalledWith('codex_cli');
  expect(mockSetAgentFramework.mock.invocationCallOrder[0])
    .toBeLessThan(mockSetProviderSlot.mock.invocationCallOrder[0]);
});

test('switching to a framework the bound card cannot run and saving with no card: the framework lands, then the page asks for a card', async () => {
  // Review 399b I2: this path used to be refused before anything was written
  // (generic pickAgentModel), so the `slotClearedPickModel` branch was dead.
  withBoundAgentSlot();
  mockSetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null, slot_cleared: true },
  });
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  expect(agentProviderSelect().value).toBe('');
  // What the backend holds after the switch: codex_cli, agent slot unbound.
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: PROVIDERS, slots: {} } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.slotClearedPickModel')).toBeInTheDocument();
  expect(mockSetAgentFramework).toHaveBeenCalledWith('codex_cli');
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
  expect(screen.queryByText('pages.settings.modelDefaults.pickAgentModel')).toBeNull();
  expect(screen.queryByText(/pages\.settings\.modelDefaults\.saved/)).toBeNull();
  // Empty slot — nothing to push onto agents, so no apply dialog.
  expect(mockGetSlotOverrideStats).not.toHaveBeenCalled();
  expect(frameworkSelect().value).toBe('codex_cli');
});

test('a half-filled agent draft is still refused before anything is written, even with a framework change', async () => {
  withBoundAgentSlot();
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  // A provider with no model yet: an explicit, incomplete agent edit.
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_free_o' } });
  const modelInput = agentModelSelect();
  fireEvent.change(modelInput, { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));
  expect(await screen.findByText('pages.settings.modelDefaults.pickAgentModel')).toBeInTheDocument();
  expect(mockSetAgentFramework).not.toHaveBeenCalled();
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
});

test('a provider the user cleared by hand is refused, not skipped behind a fake "Saved", even with a framework change', async () => {
  // Review 399c M1: an agent draft emptied by the USER (not by the framework
  // switch) used to count as framework-only — the framework landed, the
  // clear was silently dropped and the page flashed "Saved".
  withBoundAgentSlot();
  await renderLoaded();
  // nexus_power drives any protocol, so the switch keeps the bound card...
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  expect(agentProviderSelect().value).toBe('p_own');
  // ...and the user picks the blank provider option themselves.
  fireEvent.change(agentProviderSelect(), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));
  expect(await screen.findByText('pages.settings.modelDefaults.pickAgentModel')).toBeInTheDocument();
  expect(mockSetAgentFramework).not.toHaveBeenCalled();
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
  expect(screen.queryByText(/pages\.settings\.modelDefaults\.saved/)).toBeNull();
});

test('thinking / reasoning effort are disabled on an unbound agent slot and enabled once a provider is picked', async () => {
  // Review 399d M1: these knobs live on the slot row, so on an unbound slot an
  // edit had nowhere to land — it was refused with a provider/model message
  // that did not match the field the user touched.
  await renderLoaded();
  expect(agentProviderSelect().value).toBe('');
  expect(agentThinkingSelect()).toBeDisabled();
  expect(agentEffortSelect()).toBeDisabled();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_own' } });
  expect(agentThinkingSelect()).not.toBeDisabled();
  expect(agentEffortSelect()).not.toBeDisabled();
});

test('every select label is bound to its select, so clicking the label focuses the control', async () => {
  // Review 399d M3: aria-labelledby names the select for assistive tech, but
  // only htmlFor makes a click on the label text reach the control.
  withBoundAgentSlot();
  await renderLoaded();
  const selects = [
    frameworkSelect(),
    agentProviderSelect(),
    agentModelSelect(),
    agentThinkingSelect(),
    agentEffortSelect(),
    helperProviderSelect(),
    helperModelSelect(),
  ];
  for (const select of selects) {
    // The last aria-labelledby id is the select's own label.
    const ids = (select.getAttribute('aria-labelledby') ?? '').split(' ');
    const label = document.getElementById(ids[ids.length - 1]) as HTMLLabelElement;
    expect(label.tagName).toBe('LABEL');
    expect(label.control).toBe(select);
  }
});

test('a framework switch that drops the bound card also locks thinking / reasoning effort', async () => {
  // Review 399d M1 (b): after the switch emptied the draft, a thinking edit
  // used to be skipped silently while the framework alone was saved.
  withBoundAgentSlot();
  await renderLoaded();
  expect(agentThinkingSelect()).not.toBeDisabled();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  expect(agentProviderSelect().value).toBe('');
  expect(agentThinkingSelect()).toBeDisabled();
  expect(agentEffortSelect()).toBeDisabled();
});

test('load: a soft failure of the framework endpoint says the load failed instead of rendering empty selects silently', async () => {
  withBoundAgentSlot();
  mockGetAgentFramework.mockResolvedValue({ success: false, data: null });
  await renderLoaded();
  expect(screen.getByText('pages.settings.modelDefaults.loadFailed')).toBeInTheDocument();
  // No framework is invented on the frontend, so Save stays disabled.
  expect(screen.getByRole('button', { name: SAVE_NAME })).toBeDisabled();
});

test('picking a NetMind provider selects the server-owned default, not the omni list head', async () => {
  const FLASH = 'deepseek-ai/DeepSeek-V4.1-Flash';
  mockGetProviders.mockResolvedValue({
    success: true,
    data: {
      providers: {
        p_nm2: {
          provider_id: 'p_nm2',
          name: 'NetMind (Anthropic)',
          source: 'netmind',
          protocol: 'anthropic',
          auth_type: 'bearer_token',
          is_active: true,
          models: ['Qwen/Qwen3.5-Omni-Flash', 'Qwen/Qwen3-32B', FLASH],
        },
      },
      slots: {},
    },
  });
  mockGetProviderCatalog.mockResolvedValue({
    success: true,
    onboard_defaults: { netmind: { agent: FLASH, helper: FLASH } },
  });
  await renderLoaded();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_nm2' } });
  expect(agentModelSelect().value).toBe(FLASH);
});

test('load: a failed catalog fetch says the load failed instead of silently picking the list head', async () => {
  // Without the server's onboarding defaults a NetMind card would auto-select
  // its omni list head; the failure has to be visible, not a silent fallback.
  withBoundAgentSlot();
  mockGetProviderCatalog.mockRejectedValue(new Error('catalog down'));
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  await renderLoaded();
  expect(screen.getByText('pages.settings.modelDefaults.loadFailed')).toBeInTheDocument();
  expect(warn).toHaveBeenCalledWith('onboard defaults unavailable', expect.any(Error));
  warn.mockRestore();
});

test('load: a successful framework endpoint shows no load error', async () => {
  withBoundAgentSlot();
  await renderLoaded();
  expect(screen.queryByText('pages.settings.modelDefaults.loadFailed')).toBeNull();
});

test('a failed framework save keeps the draft dirty and shows the error', async () => {
  withBoundAgentSlot();
  mockSetAgentFramework.mockRejectedValue(new Error("Framework 'nexus_power' plugin is not installed"));
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));
  expect(await screen.findByText(/plugin is not installed/)).toBeInTheDocument();
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
  expect(frameworkSelect().value).toBe('nexus_power');
  expect(screen.getByRole('button', { name: SAVE_NAME })).not.toBeDisabled();
});

function agentProviderSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Agent (main dialogue) pages.settings.modelDefaults.provider',
  }) as HTMLSelectElement;
}
function agentModelSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Agent (main dialogue) pages.settings.modelDefaults.model',
  }) as HTMLSelectElement;
}
function agentThinkingSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Agent (main dialogue) pages.settings.modelDefaults.thinking',
  }) as HTMLSelectElement;
}
function agentEffortSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Agent (main dialogue) pages.settings.modelDefaults.reasoningEffort',
  }) as HTMLSelectElement;
}
function helperProviderSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Helper model pages.settings.modelDefaults.provider',
  }) as HTMLSelectElement;
}
function helperModelSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', {
    name: 'Helper model pages.settings.modelDefaults.model',
  }) as HTMLSelectElement;
}

test('an unbound agent slot does not block saving a framework-only change', async () => {
  // Review I2 (2026-09-11): the provider/model check ran on any framework
  // change, so a user whose agent slot was never bound could not save a new
  // framework at all — the Owner's bug in a narrower shape. The backend keeps
  // the framework on a stub slot row until a card is wired.
  mockSetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, install: null, slot_cleared: false },
  });
  await renderLoaded();
  expect(agentProviderSelect().value).toBe('');
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));
  await waitFor(() => expect(mockSetAgentFramework).toHaveBeenCalledWith('nexus_power'));
  await waitFor(() => expect(screen.getByText(/pages\.settings\.modelDefaults\.saved/)).toBeInTheDocument());
  expect(screen.queryByText('pages.settings.modelDefaults.pickAgentModel')).toBeNull();
  expect(mockSetProviderSlot).not.toHaveBeenCalled();
});

test('switching to a framework that drops the provider and back restores the provider and model', async () => {
  withBoundAgentSlot();
  await renderLoaded();
  const save = screen.getByRole('button', { name: SAVE_NAME });
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  expect(agentProviderSelect().value).toBe('');
  fireEvent.change(frameworkSelect(), { target: { value: 'claude_code' } });
  expect(agentProviderSelect().value).toBe('p_own');
  expect(save).toBeDisabled();
});

test('a provider picked after the drop is not overwritten by the remembered one', async () => {
  withBoundAgentSlot();
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_free_o' } });
  // nexus_power drives both cards: the user's new pick stays.
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  expect(agentProviderSelect().value).toBe('p_free_o');
});

test('slot write fails after the framework landed → the framework and the cleared binding are rolled back', async () => {
  // Review I3 (2026-09-11): the framework used to stay switched (and the
  // binding cleared) while the page only said "save failed".
  withBoundAgentSlot();
  mockSetAgentFramework
    .mockResolvedValueOnce({
      success: true,
      data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null, slot_cleared: true },
    })
    .mockResolvedValueOnce({
      success: true,
      data: { framework: 'claude_code', probe: { ok: true, detail: '' }, install: null, slot_cleared: false },
    });
  mockSetProviderSlot
    .mockResolvedValueOnce({ success: false, detail: 'model rejected' })
    .mockResolvedValueOnce({ success: true });
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_free_o' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.slotSaveRolledBack')).toBeInTheDocument();
  expect(mockSetAgentFramework.mock.calls.map((c) => c[0])).toEqual(['codex_cli', 'claude_code']);
  // The binding the switch cleared is written back.
  expect(mockSetProviderSlot).toHaveBeenLastCalledWith('agent', expect.objectContaining({ provider_id: 'p_own', model: 'claude-opus-4-8' }));
  // Nothing is stored, so the draft stays as the user left it.
  expect(frameworkSelect().value).toBe('codex_cli');
  expect(screen.getByRole('button', { name: SAVE_NAME })).not.toBeDisabled();
});

test('slot write fails and the rollback fails too → the stored state is reloaded and the half-save is named', async () => {
  withBoundAgentSlot();
  mockSetAgentFramework
    .mockResolvedValueOnce({
      success: true,
      data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null, slot_cleared: true },
    })
    .mockRejectedValueOnce(new Error('network down'));
  mockSetProviderSlot.mockResolvedValueOnce({ success: false, detail: 'model rejected' });
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_free_o' } });
  // What the backend really holds now: codex_cli with an unbound slot.
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: PROVIDERS, slots: {} } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.frameworkSavedSlotFailed')).toBeInTheDocument();
  expect(frameworkSelect().value).toBe('codex_cli');
  // Review 399b m4: the agent pick survives the reload (the stored slot is
  // empty), so Save stays live for a retry.
  expect(agentProviderSelect().value).toBe('p_free_o');
  expect(screen.getByRole('button', { name: SAVE_NAME })).not.toBeDisabled();
});

test('framework rolled back but the cleared binding could not be rebound → says the framework is back and the slot is unbound', async () => {
  // Review 399b m3(b): this sub-case used to say "the framework was switched".
  withBoundAgentSlot();
  mockSetAgentFramework
    .mockResolvedValueOnce({
      success: true,
      data: { framework: 'codex_cli', probe: { ok: true, detail: '' }, install: null, slot_cleared: true },
    })
    .mockResolvedValueOnce({
      success: true,
      data: { framework: 'claude_code', probe: { ok: true, detail: '' }, install: null, slot_cleared: false },
    });
  mockSetProviderSlot
    .mockResolvedValueOnce({ success: false, detail: 'model rejected' })
    .mockResolvedValueOnce({ success: false, detail: 'rebind rejected' });
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'codex_cli' } });
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_free_o' } });
  // Stored now: the original framework, slot unbound.
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: PROVIDERS, slots: {} } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.frameworkRestoredBindingLost')).toBeInTheDocument();
  expect(screen.queryByText('pages.settings.modelDefaults.frameworkSavedSlotFailed')).toBeNull();
  // The user's edits survive the reload so Save can retry them.
  expect(frameworkSelect().value).toBe('codex_cli');
  expect(agentProviderSelect().value).toBe('p_free_o');
  expect(screen.getByRole('button', { name: SAVE_NAME })).not.toBeDisabled();
});

test('helper write fails after only the framework landed → the message names the framework, not the agent model', async () => {
  // Review 399b m3(a): no agent-slot edit here, so "the agent model was saved"
  // would be wrong.
  mockSetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, install: null, slot_cleared: false },
  });
  mockSetProviderSlot.mockResolvedValue({ success: false, detail: 'helper rejected' });
  await renderLoaded();
  fireEvent.change(frameworkSelect(), { target: { value: 'nexus_power' } });
  const helper = helperProviderSelect();
  fireEvent.change(helper, { target: { value: 'p_free_o' } });
  mockGetAgentFramework.mockResolvedValue({
    success: true,
    data: { framework: 'nexus_power', probe: { ok: true, detail: '' }, frameworks: LIVE_FRAMEWORKS },
  });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.frameworkSavedHelperFailed')).toBeInTheDocument();
  expect(screen.queryByText('pages.settings.modelDefaults.agentSavedHelperFailed')).toBeNull();
  expect(mockSetProviderSlot).toHaveBeenCalledWith('helper_llm', expect.objectContaining({ provider_id: 'p_free_o' }));
  const helperAfter = helperProviderSelect();
  expect(helperAfter.value).toBe('p_free_o');
  // The kept helper edit still carries its model, so it can be re-saved as is.
  expect(helperModelSelect().value).not.toBe('');
  expect(frameworkSelect().value).toBe('nexus_power');
  // Only the helper edit is still unsaved.
  expect(screen.getByRole('button', { name: SAVE_NAME })).not.toBeDisabled();
});

test('helper write fails after the agent slot landed → the message names the agent model', async () => {
  mockSetProviderSlot
    .mockResolvedValueOnce({ success: true })
    .mockResolvedValueOnce({ success: false, detail: 'helper rejected' });
  await renderLoaded();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_own' } });
  fireEvent.change(helperProviderSelect(), { target: { value: 'p_free_o' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE_NAME }));

  expect(await screen.findByText('pages.settings.modelDefaults.agentSavedHelperFailed')).toBeInTheDocument();
  expect(screen.queryByText('pages.settings.modelDefaults.frameworkSavedHelperFailed')).toBeNull();
  expect(mockSetProviderSlot.mock.calls.map((c) => c[0])).toEqual(['agent', 'helper_llm']);
  expect(helperProviderSelect().value).toBe('p_free_o');
});

test('after saving a changed default, the apply-to-agents dialog appears when overrides exist', async () => {
  await renderLoaded();
  // Pick a provider for the agent slot — this also auto-fills the model, so the
  // slot becomes valid + dirty in one change (see ModelDefaultsSettings agent
  // provider onChange).
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_nm' } });
  fireEvent.click(
    screen.getByRole('button', { name: 'pages.settings.modelDefaults.saveDefaults' }),
  );
  await waitFor(() => expect(mockSetProviderSlot).toHaveBeenCalledWith('agent', expect.any(Object)));
  await waitFor(() => expect(mockGetSlotOverrideStats).toHaveBeenCalled());
  expect(await screen.findByTestId('apply-confirm-btn')).toBeInTheDocument();
});

test('no apply dialog when there are zero overrides', async () => {
  mockGetSlotOverrideStats.mockResolvedValue({
    success: true,
    data: { agent: 0, helper_llm: 0, total_agents: 5 },
  });
  await renderLoaded();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_nm' } });
  fireEvent.click(
    screen.getByRole('button', { name: 'pages.settings.modelDefaults.saveDefaults' }),
  );
  await waitFor(() => expect(mockGetSlotOverrideStats).toHaveBeenCalled());
  expect(screen.queryByTestId('apply-confirm-btn')).not.toBeInTheDocument();
});

test('a failing override-stats fetch does not turn a successful save into an error', async () => {
  // The stats GET is a pure preview; the default is already saved. A flaky GET
  // must not render a "save failed" state (it lived in the same try before).
  mockGetSlotOverrideStats.mockRejectedValue(new Error('boom'));
  await renderLoaded();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_nm' } });
  fireEvent.click(
    screen.getByRole('button', { name: 'pages.settings.modelDefaults.saveDefaults' }),
  );
  await waitFor(() => expect(mockSetProviderSlot).toHaveBeenCalled());
  await waitFor(() => expect(mockGetSlotOverrideStats).toHaveBeenCalled());
  expect(screen.queryByText('pages.settings.modelDefaults.saveFailed')).not.toBeInTheDocument();
  expect(screen.queryByTestId('apply-confirm-btn')).not.toBeInTheDocument();
});

test('dialog is gated to CHANGED slots — overrides on an untouched slot do not trigger it', async () => {
  // The user changes only the agent slot; the agent slot has 0 overrides but
  // the (untouched) helper slot has 5. The dialog must NOT appear — it is
  // gated on "a slot the user changed has overrides", not "any slot does".
  mockGetSlotOverrideStats.mockResolvedValue({
    success: true,
    data: { agent: 0, helper_llm: 5, total_agents: 8 },
  });
  await renderLoaded();
  fireEvent.change(agentProviderSelect(), { target: { value: 'p_nm' } }); // agent slot dirty
  fireEvent.click(
    screen.getByRole('button', { name: 'pages.settings.modelDefaults.saveDefaults' }),
  );
  await waitFor(() => expect(mockGetSlotOverrideStats).toHaveBeenCalled());
  expect(screen.queryByTestId('apply-confirm-btn')).not.toBeInTheDocument();
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
