/**
 * `refreshAgents` is the only thing that corrects the agent list after a
 * rename, and the list is persisted to localStorage with no `partialize` — so
 * whatever it leaves in the store is what a later page load shows.
 *
 * Keeping the previous list when the server refuses is deliberate (a backend
 * hiccup must not blank the sidebar). Doing it SILENTLY is the defect: the
 * endpoint answers 200 + {success:false} for any unhandled handler exception,
 * and the UI then goes on confidently rendering names that may already be
 * stale — which is indistinguishable, to the user, from the rename not having
 * been saved (Shenzhen round 2, P1: "改名后前端显示回退旧名").
 */
import { beforeEach, afterEach, describe, expect, test, vi } from 'vitest';

const getAgents = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    get getAgents() {
      return getAgents;
    },
  },
}));

import { useConfigStore } from '../configStore';
import type { AgentInfo } from '@/types/api';

function agent(name: string): AgentInfo {
  return { agent_id: 'agent_1', name } as AgentInfo;
}

beforeEach(() => {
  useConfigStore.getState().logout();
  useConfigStore.getState().login('alice');
  getAgents.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('refreshAgents', () => {
  test('replaces the list with server truth on success', async () => {
    useConfigStore.setState({ agents: [agent('美食家')] });
    getAgents.mockResolvedValue({ success: true, agents: [agent('小绿')], count: 1 });

    await useConfigStore.getState().refreshAgents();

    expect(useConfigStore.getState().agents[0].name).toBe('小绿');
  });

  test('a server refusal keeps the list but is reported, never silent', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    useConfigStore.setState({ agents: [agent('美食家')] });
    getAgents.mockResolvedValue({ success: false, agents: [], count: 0, error: 'boom' });

    await useConfigStore.getState().refreshAgents();

    // Not blanked — a hiccup must not empty the sidebar.
    expect(useConfigStore.getState().agents[0].name).toBe('美食家');
    // But the operator hears about it: this is the branch where the UI is
    // knowingly showing possibly-stale names.
    expect(spy).toHaveBeenCalled();
    expect(String(spy.mock.calls[0]?.[0] ?? '')).toMatch(/refresh/i);
  });

  test('a network failure keeps the list and is reported', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    useConfigStore.setState({ agents: [agent('美食家')] });
    getAgents.mockRejectedValue(new Error('offline'));

    await useConfigStore.getState().refreshAgents();

    expect(useConfigStore.getState().agents[0].name).toBe('美食家');
    expect(spy).toHaveBeenCalled();
  });

  test('does nothing without an identity', async () => {
    useConfigStore.getState().logout();
    await useConfigStore.getState().refreshAgents();
    expect(getAgents).not.toHaveBeenCalled();
  });
});
