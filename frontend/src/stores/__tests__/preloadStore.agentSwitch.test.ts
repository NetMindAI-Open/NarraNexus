/**
 * @file_name: preloadStore.agentSwitch.test.ts
 * @description: Behavior contract for per-agent data isolation in preloadStore.
 *
 * Bug (tracker: "新建 Agent 的 Awareness 默认带上前一个 Agent 的内容"):
 * the store holds ONE global copy of every per-agent domain. Switching to a
 * freshly created agent kept showing the PREVIOUS agent's awareness until
 * the new agent's fetch resolved — and a brand-new agent's awareness fetch
 * can fail (its module instance is created asynchronously), so the stale
 * persona stayed forever. Opening Edit then Save persisted the pollution.
 *
 * Invariants:
 *   - preloadAll for a DIFFERENT agent clears every per-agent domain
 *     synchronously, before any fetch resolves
 *   - a failed awareness fetch for the new agent leaves awareness null
 *     (never the previous agent's text)
 *   - preloadAll for the SAME agent keeps cached data (stale-while-revalidate)
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  api: {
    getAgentInbox: vi.fn(),
    getJobs: vi.fn(),
    getAwareness: vi.fn(),
    getSocialNetworkList: vi.fn(),
    getChatHistory: vi.fn(),
    getCosts: vi.fn(),
  },
}));

import { api } from '@/lib/api';
import { usePreloadStore } from '../preloadStore';

function mockAllApisPending(): void {
  const never = new Promise(() => undefined);
  vi.mocked(api.getAgentInbox).mockReturnValue(never as never);
  vi.mocked(api.getJobs).mockReturnValue(never as never);
  vi.mocked(api.getAwareness).mockReturnValue(never as never);
  vi.mocked(api.getSocialNetworkList).mockReturnValue(never as never);
  vi.mocked(api.getChatHistory).mockReturnValue(never as never);
  vi.mocked(api.getCosts).mockReturnValue(never as never);
}

function seedAgentAState(): void {
  usePreloadStore.setState({
    lastUserId: 'user_1',
    lastAgentId: 'agent_A',
    awareness: 'I am agent A, a pirate captain',
    awarenessCreateTime: '2026-07-01',
    awarenessUpdateTime: '2026-07-02',
    jobs: [{ job_id: 'job_A' } as never],
    socialNetworkList: [{ entity_id: 'ent_A' } as never],
    chatHistoryEvents: [{ event_id: 'evt_A' } as never],
    agentInboxRooms: [{ room_id: 'room_A' } as never],
    costSummary: { total: 1 } as never,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  usePreloadStore.getState().clearAll();
});

describe('preloadAll agent switch', () => {
  it('clears the previous agent data synchronously before fetches resolve', () => {
    seedAgentAState();
    mockAllApisPending();

    // Fire and DON'T await — the previous agent's data must be gone
    // immediately, not after the network answers.
    void usePreloadStore.getState().preloadAll('agent_B', 'user_1');

    const s = usePreloadStore.getState();
    expect(s.awareness).toBeNull();
    expect(s.jobs).toEqual([]);
    expect(s.socialNetworkList).toEqual([]);
    expect(s.chatHistoryEvents).toEqual([]);
    expect(s.agentInboxRooms).toEqual([]);
    expect(s.costSummary).toBeNull();
  });

  it('a failed awareness fetch never resurrects the previous agent text', async () => {
    seedAgentAState();
    const never = new Promise(() => undefined);
    vi.mocked(api.getAgentInbox).mockReturnValue(never as never);
    vi.mocked(api.getJobs).mockReturnValue(never as never);
    vi.mocked(api.getAwareness).mockRejectedValue(new Error('instance not ready'));
    vi.mocked(api.getSocialNetworkList).mockReturnValue(never as never);
    vi.mocked(api.getChatHistory).mockReturnValue(never as never);
    vi.mocked(api.getCosts).mockReturnValue(never as never);

    void usePreloadStore.getState().preloadAll('agent_B', 'user_1');
    await vi.waitFor(() => {
      expect(usePreloadStore.getState().awarenessError).not.toBeNull();
    });

    expect(usePreloadStore.getState().awareness).toBeNull();
  });

  it('same agent keeps cached data (stale-while-revalidate)', () => {
    seedAgentAState();
    usePreloadStore.setState({ jobs: [{ job_id: 'job_A' } as never] });
    mockAllApisPending();

    void usePreloadStore.getState().preloadAll('agent_A', 'user_1');

    expect(usePreloadStore.getState().awareness).toBe('I am agent A, a pirate captain');
  });
});
