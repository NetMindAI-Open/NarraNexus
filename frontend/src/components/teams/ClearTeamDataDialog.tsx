/**
 * @file_name: ClearTeamDataDialog.tsx
 * @description: Multi-select confirmation dialog for clearing a team's data.
 *
 * Team counterpart to ClearAgentDataDialog. Lets the owner tick "chat" (the
 * team group-chat history), "files" (the team's shared folder — which holds the
 * team's artifacts too, so ticking it removes those tabs as well) and/or
 * "bulletin" (the standing rules and the progress summary). Maps to
 * DELETE /api/teams/{id}/data?chat=&files=&bulletin= (api.clearTeamData).
 *
 * The bulletin is a SEPARATE tick, never folded into chat. It exists precisely
 * because it is not chat, so clearing a noisy transcript must not silently
 * destroy every rule the team was given — that would put the user straight back
 * into the repeat-yourself loop the bulletin was built to end. It defaults off
 * for the same reason.
 * The team, its members and the bus channel are always preserved; the confirm
 * button is danger-styled and disabled until at least one scope is selected.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter, Button } from '@/components/ui';
import { Checkbox } from '@/components/nm/form';

export interface ClearTeamDataDialogProps {
  teamName: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => void;
}

export function ClearTeamDataDialog({ teamName, busy = false, onCancel, onConfirm }: ClearTeamDataDialogProps) {
  const { t } = useTranslation();
  const [chat, setChat] = useState(true);
  const [files, setFiles] = useState(false);
  const [bulletin, setBulletin] = useState(false);
  const nothingSelected = !chat && !files && !bulletin;

  return (
    <Dialog isOpen onClose={onCancel} title={t('teams.clearData.title', { name: teamName })} size="md">
      <DialogContent>
        <p className="text-sm text-[var(--text-secondary)] mb-4">{t('teams.clearData.subtitle')}</p>
        <div className="space-y-3">
          <label className="flex flex-col gap-1 cursor-pointer">
            <Checkbox checked={chat} onChange={setChat} disabled={busy} label={t('teams.clearData.optChat')} />
            <span className="pl-6 text-xs text-[var(--text-tertiary)]">{t('teams.clearData.optChatDesc')}</span>
          </label>
          <label className="flex flex-col gap-1 cursor-pointer">
            <Checkbox checked={files} onChange={setFiles} disabled={busy} label={t('teams.clearData.optFiles')} />
            <span className="pl-6 text-xs text-[var(--text-tertiary)]">{t('teams.clearData.optFilesDesc')}</span>
          </label>
          <label className="flex flex-col gap-1 cursor-pointer">
            <Checkbox checked={bulletin} onChange={setBulletin} disabled={busy} label={t('teams.clearData.optBulletin')} />
            <span className="pl-6 text-xs text-[var(--text-tertiary)]">{t('teams.clearData.optBulletinDesc')}</span>
          </label>
        </div>
        <p className="mt-4 text-xs text-[var(--text-tertiary)]">{t('teams.clearData.keepNote')}</p>
      </DialogContent>
      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          {t('teams.clearData.cancel')}
        </Button>
        <Button variant="danger" onClick={() => onConfirm({ chat, files, bulletin })} disabled={nothingSelected || busy}>
          {t('teams.clearData.confirm')}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
