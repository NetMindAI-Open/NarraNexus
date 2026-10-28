/**
 * @file_name: whenContext.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Build the `WhenContext` slot predicates are evaluated against, from the host's stores.
 *
 * `agentHas:<module>` reads the agent's module instances (the agents list
 * carries `modules` when the backend reports them); `setting:<key>` reads
 * the config store's flat keys (e.g. `narrationTier`, `fastMode`) — the
 * host decides what a setting key means, a plugin only names it.
 */
import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';

import { useConfigStore } from '@/stores';

import type { WhenContext } from './registries/when';

/**
 * Explicit whitelist of `useConfigStore` fields a `setting:<key>` clause may read (M-5). This is
 * deliberately curated, not "the whole store": `useWhenContext` used to select the ENTIRE config
 * store object as `settings`, so ANY `set()` on the store — even one touching a field no
 * `setting:` clause reads (auth tokens, the agent list, ...) — produced a new `settings`
 * reference every render, defeating the `useMemo` below and re-rendering every
 * `useWhenContext` consumer (ChatHeader, Composer, MessageBubble, Sidebar, TopBar, every
 * AgentRow) on every store change, regardless of relevance.
 *
 * No `ConfigState` field is a genuine plugin-facing "setting" today (it currently only holds
 * auth/session/agent-list state) — this list is intentionally empty until one is added, which
 * keeps `settings` referentially stable across every current store update while leaving the
 * mechanism (`setting:<key>` clauses, `SETTING_KEYS`, `useShallow`) ready for the first real one.
 */
const SETTING_KEYS: readonly string[] = [];

export function useWhenContext(input: { conversationKind?: string; agentId?: string | null }): WhenContext {
  const agents = useConfigStore((s) => s.agents);
  const settings = useConfigStore(
    useShallow((s) => {
      const picked: Record<string, unknown> = {};
      for (const key of SETTING_KEYS) picked[key] = (s as unknown as Record<string, unknown>)[key];
      return picked;
    }),
  );
  return useMemo(() => {
    const agent = input.agentId ? agents.find((a) => a.agent_id === input.agentId) : undefined;
    const modules = (agent as { modules?: readonly string[] } | undefined)?.modules ?? [];
    return { conversationKind: input.conversationKind, agentModules: modules, settings };
  }, [agents, settings, input.agentId, input.conversationKind]);
}
