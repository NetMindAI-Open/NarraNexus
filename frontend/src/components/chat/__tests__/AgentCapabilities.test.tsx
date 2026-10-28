/**
 * @file_name: AgentCapabilities.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The capability switches: locked rows cannot be toggled, a toggle writes through the API, an explicit row can be restored to the default rule, and the budget line turns amber past the ratio.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AgentCapabilities } from '@/components/chat/AgentCapabilities';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  api: {
    getAgentCapabilities: vi.fn(),
    setAgentCapability: vi.fn(),
    resetAgentCapability: vi.fn(),
  },
}));

const item = (over: Partial<Record<string, unknown>>) => ({
  module_class: 'JobModule', name: 'Jobs', icon: '🗓', description: 'Scheduled work', owner: 'builtin.job',
  builtin: true, enabled: true, default_enabled: true, explicit: false, locked: false, always_load: false,
  context_cost_hint: 800, priority: 10, ...over,
});
const view = (caps: unknown[], over_budget = false) => ({
  success: true,
  data: { agent_id: 'a1', capabilities: caps, budget: { baseline_tokens: 1000, enabled_tokens: over_budget ? 3000 : 1000, ratio: over_budget ? 3 : 1, over_budget } },
});

afterEach(() => vi.clearAllMocks());

describe('AgentCapabilities', () => {
  it('renders the switches; a locked row is disabled and has no restore control', async () => {
    vi.mocked(api.getAgentCapabilities).mockResolvedValue(view([
      item({ module_class: 'ChatModule', name: 'Chat', locked: true }),
      item({}),
    ]) as never);
    render(<AgentCapabilities agentId="a1" />);
    const chat = await screen.findByRole('switch', { name: 'Chat' });
    expect(chat).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Jobs' })).toBeEnabled();
    expect(screen.queryByTestId('capability-restore-ChatModule')).toBeNull();
  });

  it('a toggle writes through setAgentCapability and reloads', async () => {
    vi.mocked(api.getAgentCapabilities).mockResolvedValue(view([item({})]) as never);
    vi.mocked(api.setAgentCapability).mockResolvedValue({ success: true } as never);
    render(<AgentCapabilities agentId="a1" />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Jobs' }));
    await waitFor(() => expect(api.setAgentCapability).toHaveBeenCalledWith('a1', 'JobModule', false));
    expect(api.getAgentCapabilities).toHaveBeenCalledTimes(2);
  });

  it('an explicitly switched row offers restore-default, which calls the DELETE route', async () => {
    vi.mocked(api.getAgentCapabilities).mockResolvedValue(view([item({ explicit: true, enabled: false })]) as never);
    vi.mocked(api.resetAgentCapability).mockResolvedValue({ success: true } as never);
    render(<AgentCapabilities agentId="a1" />);
    const restore = await screen.findByTestId('capability-restore-JobModule');
    await act(async () => {
      fireEvent.click(restore);
    });
    expect(api.resetAgentCapability).toHaveBeenCalledWith('a1', 'JobModule');
  });

  it('the budget line reports over-budget with the amber copy', async () => {
    vi.mocked(api.getAgentCapabilities).mockResolvedValue(view([item({})], true) as never);
    render(<AgentCapabilities agentId="a1" />);
    const line = await screen.findByTestId('capability-budget');
    expect(line.textContent).toMatch(/Over budget/);
    expect(line.className).toMatch(/warning/);
  });
});
