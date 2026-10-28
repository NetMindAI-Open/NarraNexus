/**
 * @file_name: guideAgent.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Which agent in a list is the auto-provisioned guide agent —
 * the "first agent" a new user did not create themselves.
 *
 * A pure helper rather than a field on the page, because three places need the
 * same answer: [[welcomeSteps]] composition (is there an agent step at all?),
 * [[StepAgent]] (which agent to introduce) and [[onboardingGate]] (which agent
 * NOT to count as the user's own). It also has to stay right after an import:
 * agents brought in from Claude Code / Codex are never bootstrap-active, so
 * the guide is still the one that gets introduced.
 *
 * Only a bootstrap-active agent qualifies. There is no "first agent" fallback:
 * when provisioning is off, or the guide was deleted, guessing `agents[0]`
 * turned a user's OWN agent into "the one login made for you" — the gate then
 * discounted it and sent a veteran through the newcomer flow, and StepAgent
 * introduced their agent as a guide it never was.
 */

import type { AgentInfo } from '@/types';

export function pickGuideAgent(agents: AgentInfo[]): AgentInfo | null {
  return agents.find((a) => a.bootstrap_active) ?? null;
}
