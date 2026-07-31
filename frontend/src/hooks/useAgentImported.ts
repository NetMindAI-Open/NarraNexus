/**
 * @file_name: useAgentImported.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Shared "an agent was imported" side effect — refresh the agent
 * list, select the new agent, and navigate to its chat.
 *
 * Every migration entry point (the sidebar "+" Import, the guided-flow welcome
 * modal) needs the exact same post-apply wiring; extracted so they can't drift.
 */

import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfigStore, useChatStore } from '@/stores';
import type { MigrationApplyResult } from '@/types';

export function useAgentImported() {
  const navigate = useNavigate();
  return useCallback(
    async (result: MigrationApplyResult) => {
      await useConfigStore.getState().refreshAgents().catch(() => { /* best-effort */ });
      useConfigStore.getState().setAgentId(result.agent_id);
      useChatStore.getState().setActiveAgent(result.agent_id);
      navigate('/app/chat');
    },
    [navigate],
  );
}
