/**
 * @file_name: ComposerModelBadge.test.tsx
 * @description: Behavior contract for the composer model chip.
 *
 * This file used to guard a free-tier LOCK: while the platform tier had budget
 * the runtime pinned every run to a fixed system model, so the chip had to
 * render read-only rather than offer a switch that silently no-ops. That whole
 * mechanism is gone (2026-07-28) — the free tier is an ordinary provider card,
 * and the user's model choice is honoured on it like on any other key. What is
 * guarded now is the inverse: the chip must NEVER go read-only, because there
 * is no longer any state in which a switch would be a false promise.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { ComposerModelBadge } from '../ComposerModelBadge';
import { useConfigStore } from '@/stores/configStore';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

const mockGetAgentLlmConfig = vi.fn();
const mockGetProviders = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getAgentLlmConfig: (...a: unknown[]) => mockGetAgentLlmConfig(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    getProviderCatalog: () => Promise.resolve({ success: true, onboard_defaults: {} }),
  },
}));

const EFF = {
  provider_id: 'p_nm',
  model: 'my-own-model',
  thinking: '',
  reasoning_effort: '',
  agent_framework: 'claude_code',
};

function wireConfig() {
  mockGetAgentLlmConfig.mockResolvedValue({
    success: true,
    data: {
      agent_id: 'agent_x',
      slots: { agent: { inheriting: true, effective: EFF } },
    },
  });
  mockGetProviders.mockResolvedValue({
    success: true,
    data: {
      providers: {
        p_nm: {
          provider_id: 'p_nm',
          name: 'NetMind',
          source: 'netmind',
          protocol: 'anthropic',
          is_active: true,
          models: ['my-own-model', 'other-model'],
        },
      },
    },
  });
}

beforeEach(() => {
  mockGetAgentLlmConfig.mockReset();
  mockGetProviders.mockReset();
});

describe('ComposerModelBadge — the model chip is always live', () => {
  test('a free-tier user gets the same switchable control as anyone else', async () => {
    // The free tier is an ordinary provider card now: nothing preempts the
    // agent's own slot, so there is no read-only "locked" chip to render.
    wireConfig();
    render(<ComposerModelBadge agentId="agent_x" />);

    const btn = await screen.findByRole('button');
    expect(btn).toBeInTheDocument();
    expect(screen.getByText('my-own-model')).toBeInTheDocument();
    expect(screen.queryByText('chat.model.freeTierTag')).toBeNull();
  });
});

describe('ComposerModelBadge — follows model changes made through other doors', () => {
  test('re-reads when the agent list reports a new model for THIS agent, not for others', async () => {
    // The sidebar ⋯ menu and the profile page save through their own
    // AgentLlmConfigPanel and then refresh the agent list; the chip must pick
    // that up instead of showing the replaced model until a remount.
    wireConfig();
    useConfigStore.setState({
      agents: [
        { agent_id: 'agent_x', name: 'X', model: 'my-own-model', agent_framework: 'nexus_power' } as never,
        { agent_id: 'agent_y', name: 'Y', model: 'm', agent_framework: 'nexus_power' } as never,
      ],
    });
    render(<ComposerModelBadge agentId="agent_x" />);
    await waitFor(() => expect(mockGetAgentLlmConfig).toHaveBeenCalledTimes(1));

    // Another agent's change: no re-read.
    act(() => {
      useConfigStore.setState({
        agents: [
          { agent_id: 'agent_x', name: 'X', model: 'my-own-model', agent_framework: 'nexus_power' } as never,
          { agent_id: 'agent_y', name: 'Y', model: 'changed', agent_framework: 'nexus_power' } as never,
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(mockGetAgentLlmConfig).toHaveBeenCalledTimes(1);

    // This agent's model changed behind the chip: re-read.
    act(() => {
      useConfigStore.setState({
        agents: [
          { agent_id: 'agent_x', name: 'X', model: 'other-model', agent_framework: 'nexus_power' } as never,
          { agent_id: 'agent_y', name: 'Y', model: 'changed', agent_framework: 'nexus_power' } as never,
        ],
      });
    });
    await waitFor(() => expect(mockGetAgentLlmConfig).toHaveBeenCalledTimes(2));
  });
});
