/**
 * @file_name: useAgentImported.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Shared "agents were imported" side effect — refresh the agent
 * list, and optionally select the first new agent and navigate to its chat.
 *
 * Every migration entry point (the sidebar "+" Import, the guided-flow welcome)
 * needs the exact same post-apply wiring; extracted so they can't drift.
 *
 * Takes a LIST because one import run can create several agents (the one-page
 * modal imports every checked row in a batch). `open: false` is the "Close"
 * path: the sidebar must still refresh — otherwise agents that just landed stay
 * invisible until a reload — but the user asked not to be moved anywhere.
 */

import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfigStore, useChatStore } from '@/stores';
import type { MigrationApplyResult } from '@/types';

export function useAgentImported() {
  const navigate = useNavigate();
  return useCallback(
    async (results: MigrationApplyResult[], opts?: { open?: boolean }) => {
      await useConfigStore.getState().refreshAgents().catch(() => { /* best-effort */ });
      const first = results[0];
      if (!first || opts?.open === false) return;
      useConfigStore.getState().setAgentId(first.agent_id);
      useChatStore.getState().setActiveAgent(first.agent_id);
      navigate('/app/chat');
    },
    [navigate],
  );
}
