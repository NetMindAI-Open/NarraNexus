/**
 * @file_name: useMCP.ts
 * @author: NexusAgent
 * @date: 2026-08-25
 * @description: Read-only TanStack Query hook for the current agent's MCP
 * list. Mirrors useSkillsList's shape. MCPManager.tsx keeps its own local
 * fetch/mutate/validate logic — this is a second, deliberately separate read
 * path for summary surfaces that only need the list, not the mutations.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';
import type { MCPInfo, MCPListResponse } from '@/types';

const MCP_KEY = 'mcp-list';

/** Fetch the MCP list for the current agent with automatic caching. */
export function useMCPList() {
  const { agentId, userId } = useConfigStore();

  return useQuery({
    queryKey: [MCP_KEY, agentId, userId] as const,
    queryFn: () => api.listMCPs(agentId!),
    enabled: !!agentId && !!userId,
    select: (data: MCPListResponse): MCPInfo[] => data.mcps,
  });
}
