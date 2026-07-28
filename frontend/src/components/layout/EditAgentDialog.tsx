/**
 * @file_name: EditAgentDialog.tsx
 * @description: Edit an agent's name + description from the row ⋮ menu.
 *
 * The only UI surface for agent_description (previously the field was carried
 * by the API and fed into the agent's own context/Agent Card but had no editor,
 * so a value could only arrive via bundle import — see #71). Both fields are
 * capped at AGENT_TEXT_MAX_LENGTH: a live "n/255" counter turns red and Save is
 * disabled with an error hint once either exceeds it, so the user is stopped
 * here instead of by the server's 422. Controlled by AgentList (owns busy /
 * target state); mounted only while open so the fields reset on each open.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter, Button, Input, Textarea } from '@/components/ui';
import { cn } from '@/lib/utils';
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';

export interface EditAgentDialogProps {
  initialName: string;
  initialDescription: string;
  busy?: boolean;
  onCancel: () => void;
  onSave: (name: string, description: string) => void;
}

function Counter({ length }: { length: number }) {
  const over = length > AGENT_TEXT_MAX_LENGTH;
  return (
    <span
      className={cn('text-[11px] tabular-nums', over ? 'text-[var(--color-error)]' : 'text-[var(--nm-ink50)]')}
    >
      {length}/{AGENT_TEXT_MAX_LENGTH}
    </span>
  );
}

export function EditAgentDialog({
  initialName,
  initialDescription,
  busy = false,
  onCancel,
  onSave,
}: EditAgentDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);

  const nameOver = name.length > AGENT_TEXT_MAX_LENGTH;
  const descOver = description.length > AGENT_TEXT_MAX_LENGTH;
  const nameEmpty = !name.trim();
  const canSave = !busy && !nameEmpty && !nameOver && !descOver;

  return (
    <Dialog isOpen onClose={onCancel} title={t('layout.editAgentDialog.title')} size="md">
      <DialogContent>
        <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-[var(--nm-ink70)]">
                {t('layout.editAgentDialog.nameLabel')}
              </label>
              <Counter length={name.length} />
            </div>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              error={nameOver}
              autoFocus
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-[var(--nm-ink70)]">
                {t('layout.editAgentDialog.descriptionLabel')}
              </label>
              <Counter length={description.length} />
            </div>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              error={descOver}
              placeholder={t('layout.editAgentDialog.descriptionPlaceholder')}
            />
          </div>

          {(nameOver || descOver) && (
            <p className="text-[11px] text-[var(--color-error)]">
              {t('layout.editAgentDialog.tooLong', { max: AGENT_TEXT_MAX_LENGTH })}
            </p>
          )}
        </div>
      </DialogContent>

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t('layout.editAgentDialog.cancel')}
        </Button>
        <Button onClick={() => onSave(name.trim(), description)} disabled={!canSave}>
          {t('layout.editAgentDialog.save')}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
