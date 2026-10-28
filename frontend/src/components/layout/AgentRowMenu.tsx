/**
 * @file_name: AgentRowMenu.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Kebab (⋯) context menu for a single sidebar agent row —
 * Rename, Model & framework, Delete.
 *
 * OWNER-REQUIRED entry (2026-09-11): #383 deleted this menu as a duplicate of
 * the agent profile page, and the Owner asked for it back ("the three dots
 * behind each agent are gone, restore them: rename / delete / setting
 * model"). Do not remove it as redundant — the profile page is a second door,
 * not a replacement.
 *
 * The dropdown shell (trigger, panel, dismissal) is the shared RowKebabMenu,
 * same as TeamRowMenu. The host renders it only for agents the viewer owns
 * (every action is owner-only server-side).
 */

import { useTranslation } from 'react-i18next';
import { Pencil, SlidersHorizontal, Trash2 } from 'lucide-react';
import { RowKebabMenu } from './RowKebabMenu';

export interface AgentRowMenuProps {
  /** Start the row's inline rename. */
  onRename: () => void;
  /** Open the agent's model & framework panel (AgentLlmConfigPanel). */
  onOpenModelConfig: () => void;
  /** Delete the agent (the host confirms first). */
  onDelete: () => void;
  /** Fired on open/close so the host row can lift its z-index above the rows
   *  below (see RowKebabMenu). */
  onOpenChange?: (open: boolean) => void;
}

export function AgentRowMenu({ onRename, onOpenModelConfig, onDelete, onOpenChange }: AgentRowMenuProps) {
  const { t } = useTranslation();
  return (
    <RowKebabMenu
      ariaLabel={t('layout.agentRowMenu.options')}
      minWidthClass="min-w-[150px]"
      onOpenChange={onOpenChange}
      items={[
        { key: 'rename', icon: <Pencil className="w-3 h-3" />, label: t('layout.agentRowMenu.rename'), onSelect: onRename },
        {
          key: 'model',
          icon: <SlidersHorizontal className="w-3 h-3" />,
          label: t('layout.agentRowMenu.modelFramework'),
          onSelect: onOpenModelConfig,
        },
        { key: 'delete', icon: <Trash2 className="w-3 h-3" />, label: t('layout.agentRowMenu.delete'), danger: true, onSelect: onDelete },
      ]}
    />
  );
}
