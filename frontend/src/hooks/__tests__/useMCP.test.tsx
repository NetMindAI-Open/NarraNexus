/**
 * @file_name: useMCP.test.tsx
 * @description: useMCPList resolves the current agent's MCP list, read-only —
 * mirrors useSkillsList's contract (global agentId, enabled gate).
 */
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const listMCPsMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { listMCPs: (...args: unknown[]) => listMCPsMock(...args) },
}));

const configState = { agentId: 'agent-1', userId: 'user-1' };
vi.mock('@/stores', () => ({
  useConfigStore: () => configState,
}));

import { useMCPList } from '../useMCP';

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  listMCPsMock.mockReset();
  configState.agentId = 'agent-1';
  configState.userId = 'user-1';
});

describe('useMCPList', () => {
  test('resolves the MCP list for the current agent', async () => {
    listMCPsMock.mockResolvedValue({
      success: true,
      count: 1,
      mcps: [
        {
          mcp_id: 'mcp-1',
          agent_id: 'agent-1',
          user_id: 'user-1',
          name: 'hubspot-mcp',
          url: 'https://example.com/mcp',
          is_enabled: true,
          connection_status: 'connected',
        },
      ],
    });

    const { result } = renderHook(() => useMCPList(), { wrapper });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].name).toBe('hubspot-mcp');
    expect(listMCPsMock).toHaveBeenCalledWith('agent-1');
  });

  test('does not fetch when there is no active agent', () => {
    configState.agentId = '';
    renderHook(() => useMCPList(), { wrapper });
    expect(listMCPsMock).not.toHaveBeenCalled();
  });
});
