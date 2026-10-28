/**
 * @file_name: useAgentActions.ts
 * @author:
 * @date: 2026-09-11
 * @description: The shared rename / delete actions for one agent.
 *
 * Two surfaces act on an agent: its profile page and the sidebar agent row's
 * ⋯ menu (Owner-required, reinstated 2026-09-11). Both go through here so
 * they cannot drift — the same confirm dialog, the same store cleanup after a
 * delete, the same "renamed, with something to know" report. The last time
 * these lived in two places (AgentList + EditAgentDialog vs. the profile
 * page), the sidebar copy silently diverged.
 *
 * The caller owns the dialog host: it passes the `confirm` / `alert` of its
 * own `useConfirm()` (and renders that hook's `dialog`), so a page that
 * already has a confirm dialog does not grow a second one. Navigation after a
 * delete is the caller's decision too — the hook only reports success.
 */

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import type { ConfirmOptions, AlertOptions } from '@/components/ui/ConfirmDialog';
import type { UpdateAgentResponse } from '@/types';

export interface AgentActionDialogs {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  alert: (opts: AlertOptions) => Promise<void>;
}

export interface DeleteAgentOutcome {
  deleted: boolean;
  /** The deleted agent was the globally active one — the view showing it is
   *  gone, so callers move off it (the active agent was already re-pointed). */
  wasActive: boolean;
}

export function useAgentActions({ confirm, alert }: AgentActionDialogs) {
  const { t } = useTranslation();
  const { agents, agentId: activeAgentId, setAgentId, refreshAgents } = useConfigStore();
  const { setActiveAgent, clearAgent } = useChatStore();

  /**
   * Report what a successful update still wants the user to know. Both cases
   * come back on a `success: true` response, so neither reaches an error
   * branch, and reporting neither is what once made a rename path where both
   * happened silently:
   *
   * - `name_clash_with` — another of this owner's agents already answers to
   *   the name. Deliberate often enough that blocking it would be wrong;
   *   silent is how two agents came to share one name (Shenzhen P1).
   * - `identity_record_updated === false` — the name IS stored but the agent's
   *   identity memory was not corrected, so it may keep introducing itself by
   *   the old name. That state IS the incident.
   */
  const warnAboutUpdateSideEffects = useCallback(
    async (res: UpdateAgentResponse) => {
      const notes: string[] = [];
      if (res.name_clash_with) {
        notes.push(t('layout.agentRename.clashWarn', { agentId: res.name_clash_with }));
      }
      if (res.identity_record_updated === false) {
        notes.push(t('layout.agentRename.memoryWarn'));
      }
      if (!notes.length) return;
      await alert({
        title: t('layout.agentRename.warnTitle'),
        message: notes.join('\n\n'),
      });
    },
    [alert, t],
  );

  /** Rename only (the description keeps its value). Refreshes the server list
   *  — `agents` is persisted, so a hand-patched row is what a reload would
   *  show — then reports side effects. Returns whether the rename landed. */
  const renameAgent = useCallback(
    async (agentId: string, name: string): Promise<boolean> => {
      const next = name.trim();
      if (!next) return false;
      try {
        const res = await api.updateAgent(agentId, next);
        if (!res.success) {
          await alert({
            title: t('pages.agentProfile.saveFailed'),
            message: res.error || t('pages.agentProfile.saveFailed'),
            danger: true,
          });
          return false;
        }
        await refreshAgents();
        await warnAboutUpdateSideEffects(res);
        return true;
      } catch (err) {
        await alert({
          title: t('pages.agentProfile.saveFailed'),
          message: err instanceof Error ? err.message : String(err),
          danger: true,
        });
        return false;
      }
    },
    [alert, t, refreshAgents, warnAboutUpdateSideEffects],
  );

  /**
   * Confirm, delete, and leave the stores clean: server list first (it is the
   * truth and `agents` is persisted to localStorage — a stale row would ghost
   * in the sidebar until the next refresh), then drop the cached session, then
   * — only when the deleted agent was the active one — re-point the active
   * agent so /app/chat cannot open on an agent that no longer exists.
   */
  const deleteAgent = useCallback(
    async (agentId: string, name: string): Promise<DeleteAgentOutcome> => {
      const ok = await confirm({
        title: t('layout.agentList.deleteAgentTitle'),
        message: t('layout.agentList.deleteAgentMessage', { name }),
        confirmText: t('layout.agentList.deleteAction'),
        danger: true,
      });
      if (!ok) return { deleted: false, wasActive: false };
      try {
        const res = await api.deleteAgent(agentId);
        if (!res.success) {
          await alert({
            title: t('layout.agentList.deleteFailedTitle'),
            message: t('layout.agentList.deleteAgentFailedMessage', { error: res.error }),
            danger: true,
          });
          return { deleted: false, wasActive: false };
        }
        const wasActive = activeAgentId === agentId;
        const remaining = agents.filter((item) => item.agent_id !== agentId);
        await refreshAgents();
        clearAgent(agentId);
        if (wasActive) {
          const next = remaining[0]?.agent_id ?? '';
          setAgentId(next);
          if (next) setActiveAgent(next);
        }
        return { deleted: true, wasActive };
      } catch (err) {
        await alert({
          title: t('layout.agentList.deleteFailedTitle'),
          message: t('layout.agentList.deleteAgentFailedMessage', {
            error: err instanceof Error ? err.message : String(err),
          }),
          danger: true,
        });
        return { deleted: false, wasActive: false };
      }
    },
    [activeAgentId, agents, alert, clearAgent, confirm, refreshAgents, setActiveAgent, setAgentId, t],
  );

  return { renameAgent, deleteAgent, warnAboutUpdateSideEffects };
}
