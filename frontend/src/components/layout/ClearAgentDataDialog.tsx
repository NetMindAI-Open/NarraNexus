/**
 * @file_name: ClearAgentDataDialog.tsx
 * @description: Multi-select confirmation dialog for wiping an agent's data.
 *
 * Opened from the agent row ⋮ menu ("Clear data…"). Lets the owner tick
 * "conversations" and/or "memory" (either or both) and confirm. Maps to
 * DELETE /api/agents/{id}/history?conversations=&memory= (api.clearHistory),
 * which — unlike the old Sidebar "clear history" button — also removes the
 * on-disk narrative markdown + trajectory files, the real memory surface.
 *
 * The persona, channel credentials and account are always preserved; the
 * confirm button is danger-styled and disabled until at least one scope is
 * selected. Controlled by AgentList (which owns the busy/target state).
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui';
import { Button } from '@/components/ui';
import { Checkbox } from '@/components/nm/form';

export interface ClearAgentDataDialogProps {
  agentName: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (scopes: { conversations: boolean; memory: boolean }) => void;
}

/**
 * Mounted only while open (the host `AgentList` renders it behind
 * `{clearTarget && ...}`), so the checkbox defaults reset naturally on each
 * open — no reset effect needed. Default: chat records checked, memory
 * unchecked (memory is the more destructive opt-in).
 */
export function ClearAgentDataDialog({
  agentName,
  busy = false,
  onCancel,
  onConfirm,
}: ClearAgentDataDialogProps) {
  const { t } = useTranslation();
  // Default: clear chat records only. Memory (learned facts/relationships) is
  // the more destructive option, so it starts unchecked — the user opts in.
  const [conversations, setConversations] = useState(true);
  const [memory, setMemory] = useState(false);

  const nothingSelected = !conversations && !memory;

  return (
    <Dialog
      isOpen
      onClose={onCancel}
      title={t('layout.clearAgentData.title', { name: agentName })}
      size="md"
    >
      <DialogContent>
        <p className="text-sm text-[var(--text-secondary)] mb-4">
          {t('layout.clearAgentData.subtitle')}
        </p>

        <div className="space-y-3">
          <label className="flex flex-col gap-1 cursor-pointer">
            <Checkbox
              checked={conversations}
              onChange={setConversations}
              disabled={busy}
              label={t('layout.clearAgentData.optConversations')}
            />
            <span className="pl-6 text-xs text-[var(--text-tertiary)]">
              {t('layout.clearAgentData.optConversationsDesc')}
            </span>
          </label>

          <label className="flex flex-col gap-1 cursor-pointer">
            <Checkbox
              checked={memory}
              onChange={setMemory}
              disabled={busy}
              label={t('layout.clearAgentData.optMemory')}
            />
            <span className="pl-6 text-xs text-[var(--text-tertiary)]">
              {t('layout.clearAgentData.optMemoryDesc')}
            </span>
          </label>
        </div>

        <p className="mt-4 text-xs text-[var(--text-tertiary)]">
          {t('layout.clearAgentData.keepNote')}
        </p>
      </DialogContent>

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t('layout.clearAgentData.cancel')}
        </Button>
        <Button
          variant="danger"
          onClick={() => onConfirm({ conversations, memory })}
          disabled={nothingSelected || busy}
        >
          {t('layout.clearAgentData.confirm')}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
