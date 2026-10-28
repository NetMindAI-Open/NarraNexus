/**
 * @file_name: AgentLlmConfigPanel.defaultFramework.test.tsx
 * @author:
 * @date: 2026-09-11
 * @description: The per-agent editor's framework when nothing is bound yet
 * comes from the backend's resolved framework (GET /api/providers/
 * agent-framework), never from a frontend literal. The backend picks an
 * INSTALLED framework for the distribution; a hardcoded id here could name
 * one whose plugin is not installed (Owner bug 2026-09-11).
 */
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

let ownerDefault: Record<string, unknown> | null = null;
let resolvedFramework = 'codex_cli';
let frameworkLoadOk = true;

vi.mock('@/lib/api', () => ({
  api: {
    getProviderCatalog: () => Promise.resolve({ success: true, onboard_defaults: {} }),
    getAgentLlmConfig: () =>
      Promise.resolve({
        success: true,
        data: {
          agent_id: 'agent_1',
          slots: {
            agent: { owner_default: ownerDefault, effective: null },
            helper_llm: { owner_default: null, effective: null },
          },
        },
      }),
    getProviders: () =>
      Promise.resolve({
        success: true,
        data: {
          providers: {
            prov_o: {
              provider_id: 'prov_o', name: 'OpenAI key', source: 'user',
              protocol: 'openai', auth_type: 'api_key', is_active: true, models: ['gpt-x'],
            },
            prov_a: {
              provider_id: 'prov_a', name: 'Anthropic key', source: 'user',
              protocol: 'anthropic', auth_type: 'api_key', is_active: true, models: ['claude-x'],
            },
          },
        },
      }),
    getAgentFramework: () =>
      Promise.resolve(frameworkLoadOk ? {
        success: true,
        data: {
          framework: resolvedFramework,
          supported: ['nexus_power', 'claude_code', 'codex_cli'],
          frameworks: [
            { name: 'claude_code', available: false, protocol: 'anthropic', oauth_source: 'claude_oauth' },
            { name: 'codex_cli', available: true, protocol: 'openai', oauth_source: 'codex_oauth' },
            { name: 'nexus_power', available: true, protocol: 'any', oauth_source: null },
          ],
          probe: { ok: true, detail: '' },
        },
      } : { success: false }),
    setAgentLlmConfig: vi.fn(),
  },
}));

vi.mock('@/stores/configStore', () => ({
  useConfigStore: (sel: (s: { role: string | undefined }) => unknown) => sel({ role: 'owner' }),
}));

import { AgentLlmConfigPanel } from '../AgentLlmConfigPanel';

function frameworkSelect(): HTMLSelectElement {
  return screen
    .getAllByRole('combobox')
    .find((el) => el.querySelector('option[value="nexus_power"]')) as HTMLSelectElement;
}

async function renderPanel() {
  render(
    <MemoryRouter>
      <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
    </MemoryRouter>,
  );
  await waitFor(() => expect(frameworkSelect()).toBeTruthy());
}

beforeEach(() => {
  ownerDefault = null;
  resolvedFramework = 'codex_cli';
  frameworkLoadOk = true;
});

describe('AgentLlmConfigPanel default framework', () => {
  test('no owner slot → the backend-resolved framework, not a hardcoded id', async () => {
    await renderPanel();
    await waitFor(() => expect(frameworkSelect().value).toBe('codex_cli'));
  });

  test('an owner default framework still wins over the user-level answer', async () => {
    ownerDefault = { provider_id: 'prov_a', model: 'claude-x', agent_framework: 'nexus_power' };
    await renderPanel();
    await waitFor(() => expect(frameworkSelect().value).toBe('nexus_power'));
  });

  test('a soft-failed framework load says so instead of an empty form', async () => {
    frameworkLoadOk = false;
    render(
      <MemoryRouter>
        <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Failed to load configuration')).toBeTruthy();
  });

  test('a successful load shows no load error', async () => {
    await renderPanel();
    expect(screen.queryByText('Failed to load configuration')).toBeNull();
  });
});
