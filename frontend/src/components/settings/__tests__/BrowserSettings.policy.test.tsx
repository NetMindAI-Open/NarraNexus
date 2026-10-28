/**
 * @file_name: BrowserSettings.policy.test.tsx
 * @description: The production Browser settings host wires agent selection to real policy methods.
 */
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores/configStore';
import { useChatStore } from '@/stores/chatStore';
import BrowserSettings from '../BrowserSettings';

const ready = { state: 'ready' as const, reason: 'ready', version: 'test', executable: '/test', progress: null };
const policy = {
  agent_id: 'agent-a',
  defaults: { full_cdp_access: 'deny' as const },
  origins: [{ origin: 'https://accounts.example.test', full_cdp_access: 'deny' as const }],
};

beforeEach(() => {
  useConfigStore.setState({ userId: 'owner', agents: [
    { agent_id: 'agent-a', name: 'Agent A', created_by: 'owner', bound_channels: [] },
    { agent_id: 'agent-b', name: 'Agent B', created_by: 'owner', bound_channels: [] },
  ] });
  useChatStore.setState({ activeAgentId: 'agent-a' });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useConfigStore.setState({ agents: [], userId: '' });
  useChatStore.setState({ activeAgentId: null });
});

test('script settings switch only the managed agent', async () => {
  const get = vi.spyOn(api, 'getBrowserPolicy').mockImplementation(async (agentId) => ({ ...policy, agent_id: agentId }));
  render(<BrowserSettings fetchStatus={async () => ready} />);
  expect(await screen.findByText(policy.origins[0].origin)).toBeInTheDocument();
  expect(screen.queryByText(/site exceptions|website access|turn approvals|conversation approvals/i)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: /agent/i }), { target: { value: 'agent-b' } });
  await waitFor(() => expect(get).toHaveBeenCalledWith('agent-b'));
  expect(useChatStore.getState().activeAgentId).toBe('agent-a');
});

test('script saving reaches only the advanced-script policy API', async () => {
  vi.spyOn(api, 'getBrowserPolicy').mockResolvedValue(policy);
  const update = vi.spyOn(api, 'updateBrowserPolicy').mockResolvedValue(policy);

  render(<BrowserSettings fetchStatus={async () => ready} />);
  fireEvent.click(await screen.findByRole('checkbox', { name: /advanced scripts/i }));
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await waitFor(() => expect(update).toHaveBeenCalledWith('agent-a', {
    origin: policy.origins[0].origin, capability: 'full_cdp_access', verdict: 'allow',
  }));
  expect(screen.queryByRole('button', { name: /block site|allow site/i })).not.toBeInTheDocument();
});

test('a response for a different agent is never displayed as the selected agent permissions', async () => {
  vi.spyOn(api, 'getBrowserPolicy').mockResolvedValue({ ...policy, agent_id: 'someone-else' });
  render(<BrowserSettings fetchStatus={async () => ready} />);
  await screen.findByRole('alert');
  expect(screen.queryByText(policy.origins[0].origin)).not.toBeInTheDocument();
});

test('no website rules or exceptions appear for an empty policy', async () => {
  vi.spyOn(api, 'getBrowserPolicy').mockResolvedValue({ ...policy, origins: [] });
  render(<BrowserSettings fetchStatus={async () => ready} />);
  await screen.findByRole('heading', { name: 'Advanced scripts' });
  expect(screen.queryByText(/site exceptions|website access|blocked|requires approval/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /allow site|block site|revoke/i })).not.toBeInTheDocument();
});
