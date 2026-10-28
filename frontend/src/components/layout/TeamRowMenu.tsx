/**
 * @file_name: TeamRowMenu.tsx
 * @author:
 * @date: 2026-06-23
 * @description: Kebab (⋮) context menu for the team group-chat row — Add
 * agent / Rename / Delete (a team has no profile page to carry them). Its
 * sibling for agent rows is AgentRowMenu (reinstated 2026-09-11). Both are item
 * lists on the shared RowKebabMenu dropdown shell.
 */

import { useTranslation } from 'react-i18next';
import { Pencil, Trash2, UserPlus } from 'lucide-react';
import { RowKebabMenu } from './RowKebabMenu';

export interface TeamRowMenuProps {
  /** Create a new agent already assigned to this team (#43). The old
   *  AgentGroupSection-header "+" no longer exists in the TEAMS-row layout,
   *  so this capability is re-homed into the row's ⋮ menu. */
  onAddAgent: (e: React.MouseEvent) => void;
  /** True while an agent create is in flight — disables the Add-agent item. */
  addingAgent?: boolean;
  onRename: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  /** Fired on open/close so the host row can lift its z-index above the rows
   *  below (each row is its own stacking context). */
  onOpenChange?: (open: boolean) => void;
}

export function TeamRowMenu({ onAddAgent, addingAgent, onRename, onDelete, onOpenChange }: TeamRowMenuProps) {
  const { t } = useTranslation();
  return (
    <RowKebabMenu
      ariaLabel={t('layout.teamRowMenu.options')}
      onOpenChange={onOpenChange}
      items={[
        {
          key: 'add-agent',
          icon: <UserPlus className="w-3 h-3" />,
          label: addingAgent ? t('layout.teamRowMenu.addingAgent') : t('layout.teamRowMenu.addAgent'),
          disabled: addingAgent,
          onSelect: onAddAgent,
        },
        { key: 'rename', icon: <Pencil className="w-3 h-3" />, label: t('layout.teamRowMenu.rename'), onSelect: onRename },
        { key: 'delete', icon: <Trash2 className="w-3 h-3" />, label: t('layout.teamRowMenu.delete'), danger: true, onSelect: onDelete },
      ]}
    />
  );
}
