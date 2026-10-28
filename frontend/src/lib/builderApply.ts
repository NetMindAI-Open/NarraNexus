/**
 * @file_name: builderApply.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: Turns a parsed `<agent_draft>` into real changes on the agent.
 *
 * The split that matters, and it is deliberate:
 *
 *   name / description / instructions  → written LIVE, no confirmation
 *   skills / channels                  → RECOMMENDED, the user installs/binds
 *
 * Text fields are safe to write because they are cheap to undo (the panel
 * shows them, the user edits them in place) and because writing them is the
 * whole point — the user asked for a conversation that fills in the panel.
 *
 * Skills and channels are not. Installing a skill copies files into the
 * agent's workspace, and a model that changes its mind mid-conversation would
 * install-then-uninstall in front of the user. Binding a channel needs a
 * credential, which is the user's to supply and must never reach the model.
 * So the draft only ever RECOMMENDS those two, and a human click applies them.
 *
 * Every write is diffed first: an unchanged field issues no request. Without
 * that, every single reply would PUT the same instructions back, and the
 * agent's update timestamp would churn on turns that changed nothing.
 */
import { api } from '@/lib/api';
import type { AgentDraft } from '@/lib/builderProtocol';

export interface ApplyOutcome {
  /** Fields actually written this turn. Empty means the reply changed nothing. */
  changed: Array<'identity' | 'awareness'>;
  /** Per-field failure text. A failure never blocks the conversation. */
  errors: string[];
}

/**
 * Read the agent's current configuration.
 *
 * Identity comes from the caller (the agent list already holds it, and this
 * runs on every turn — one avoidable round trip per message adds up).
 * Instructions come from the API because nothing else holds them.
 */
export async function readCurrentConfig(
  agentId: string,
  identity: { name: string; description: string },
  recommendations: { skill_ids: string[]; channels: string[] },
): Promise<AgentDraft> {
  let awareness = '';
  try {
    const res = await api.getAwareness(agentId);
    awareness = typeof res.awareness === 'string' ? res.awareness : '';
  } catch {
    // A failed read must not stop the turn: an empty value simply tells the
    // model there are no instructions yet, which is recoverable. Blocking the
    // send is not.
  }
  return {
    name: identity.name,
    description: identity.description,
    awareness,
    skill_ids: recommendations.skill_ids,
    channels: recommendations.channels as AgentDraft['channels'],
  };
}

/**
 * Write the live fields, skipping anything unchanged.
 *
 * Errors are collected, not thrown. A failed write is surfaced in the panel;
 * it must not interrupt the conversation the user is having (binding rule
 * #15 — the platform is not allowed to become the interruption).
 */
export async function applyLiveFields(
  agentId: string,
  prev: AgentDraft,
  next: AgentDraft,
): Promise<ApplyOutcome> {
  const changed: ApplyOutcome['changed'] = [];
  const errors: string[] = [];

  const identityChanged = next.name !== prev.name || next.description !== prev.description;
  if (identityChanged) {
    try {
      const res = await api.updateAgent(agentId, next.name, next.description);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      changed.push('identity');
    } catch (e) {
      errors.push(String(e));
    }
  }

  if (next.awareness !== prev.awareness) {
    try {
      const res = await api.updateAwareness(agentId, next.awareness);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      changed.push('awareness');
    } catch (e) {
      errors.push(String(e));
    }
  }

  return { changed, errors };
}
