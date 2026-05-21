/**
 * @file_name: useCreateAgent.ts
 * @author: NexusAgent
 * @date: 2026-05-21
 * @description: Shared "create a blank agent" action.
 *
 * Extracted so both the sidebar AgentList button and the onboarding
 * checklist card create agents through one path — same store wiring, same
 * onboarding-progress side effect. Without this, the two call sites would
 * drift (one updates configStore, the other forgets setActiveAgent, etc.).
 *
 * Side effect: on success it fires `markOnboardingStep('first_agent_created')`
 * (fire-and-forget). The backend endpoint is mode-agnostic, so this is a
 * no-op-ish cheap call in local mode — only the checklist *card* is
 * cloud-gated, the progress flag is harmless to record anywhere.
 */
import { useState, useCallback } from 'react';
import { useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';

export function useCreateAgent() {
  const [creating, setCreating] = useState(false);

  /** Create a fresh agent, wire it into the stores, select it. Returns the
   *  new agent_id on success, or null on failure. */
  const createAgent = useCallback(async (): Promise<string | null> => {
    const { userId, agents, setAgents, setAgentId } = useConfigStore.getState();
    const { setActiveAgent } = useChatStore.getState();
    setCreating(true);
    try {
      const res = await api.createAgent(userId);
      if (res.success && res.agent) {
        const newAgent = {
          agent_id: res.agent.agent_id,
          name: res.agent.name,
          description: res.agent.description,
          status: res.agent.status,
          created_at: res.agent.created_at,
          created_by: userId,
          bootstrap_active: res.agent.bootstrap_active,
        };
        setAgents([newAgent, ...agents]);
        setAgentId(res.agent.agent_id);
        setActiveAgent(res.agent.agent_id);
        if (userId) {
          api.markOnboardingStep(userId, 'first_agent_created').catch(() => {
            /* onboarding progress is best-effort — never block agent create */
          });
        }
        return res.agent.agent_id;
      }
      console.error('Failed to create agent:', res.error);
      return null;
    } catch (err) {
      console.error('Error creating agent:', err);
      return null;
    } finally {
      setCreating(false);
    }
  }, []);

  return { createAgent, creating };
}
