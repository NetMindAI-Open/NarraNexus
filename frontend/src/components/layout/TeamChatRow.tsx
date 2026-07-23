/**
 * @file_name: TeamChatRow.tsx
 * @author:
 * @date: 2026-06-23
 * @description: One team's group-chat entry in the sidebar's TEAMS section.
 * A row sized like an agent row: a carbon·silicon split avatar, the team
 * (group-chat) name with an inline-rename + ⋮ menu (Rename / Delete), the
 * agent count, and an active highlight when that team's group chat is open.
 *
 * Extracted from AgentGroupSection so teams (group chats) live in their own
 * top section, separate from the flat AGENTS list.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GroupAvatar } from '@/components/nm';
import { TeamRowMenu } from './TeamRowMenu';
import { cn } from '@/lib/utils';

export interface TeamChatRowProps {
  teamId: string;
  teamName: string;
  agentCount: number;
  /** True when this team's group chat is the open view → row is highlighted. */
  active: boolean;
  onOpen: (teamId: string) => void;
  onRename: (teamId: string, name: string) => void;
  onDelete: (teamId: string) => void;
  /** Clear the team's group-chat / shared files (keeps the team + members). */
  onClearData: (teamId: string) => void;
  /** Create a new agent already assigned to this team (#43). */
  onAddAgent: (teamId: string) => void;
  /** True while an agent create is in flight — disables the Add-agent item. */
  addingAgent?: boolean;
}

export function TeamChatRow({
  teamId,
  teamName,
  agentCount,
  active,
  onOpen,
  onRename,
  onDelete,
  onClearData,
  onAddAgent,
  addingAgent,
}: TeamChatRowProps) {
  const { t } = useTranslation();
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(teamName);
  const [menuOpen, setMenuOpen] = useState(false);

  const commitRename = () => {
    const next = nameDraft.trim();
    setRenaming(false);
    if (next && next !== teamName) onRename(teamId, next);
  };

  const initials = teamName
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <div
      onClick={() => { if (!renaming) onOpen(teamId); }}
      title={t('layout.teamChatRow.groupChatTitle', { name: teamName })}
      className={cn(
        'group/gc w-full text-left px-3 py-1.5 cursor-pointer rounded-[var(--radius-lg)] transition-colors duration-150',
        !active && 'hover:bg-[var(--nm-paper-warm)]',
        menuOpen && 'relative z-30',
      )}
      style={active ? { background: 'var(--nm-row-active)' } : undefined}
    >
      <div className="flex items-center gap-2.5">
        <GroupAvatar
          size="sm"
          members={[{ species: 'carbon' }, { species: 'silicon' }]}
          label={initials}
          className="shrink-0"
        />
        <div className="flex-1 min-w-0">
          {renaming ? (
            <input
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
                if (e.key === 'Escape') { e.preventDefault(); setNameDraft(teamName); setRenaming(false); }
              }}
              onBlur={commitRename}
              className="w-full px-2 py-0.5 text-sm text-[var(--nm-ink)] bg-[var(--nm-paper-warm)] border border-[var(--nm-ink)] rounded-[var(--radius-xs)] focus:outline-none"
            />
          ) : (
            /* Name line: the ⋮ menu sits right next to the name (like an agent row). */
            <div className="flex items-center gap-1">
              <span
                className="min-w-0 truncate text-sm font-medium"
                style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-sans)' }}
              >
                {teamName}
              </span>
              <div
                className={cn(
                  'shrink-0 transition-opacity',
                  menuOpen ? 'opacity-100' : 'opacity-0 group-hover/gc:opacity-100',
                )}
              >
                <TeamRowMenu
                  onOpenChange={setMenuOpen}
                  onAddAgent={() => onAddAgent(teamId)}
                  addingAgent={addingAgent}
                  onRename={() => { setNameDraft(teamName); setRenaming(true); }}
                  onClearData={() => onClearData(teamId)}
                  onDelete={() => onDelete(teamId)}
                />
              </div>

              {/* Member count — on the right, like an agent row's timestamp */}
              <span
                className="ml-auto pl-2 text-[10px] shrink-0"
                style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}
              >
                {t('layout.teamChatRow.agentCount', { count: agentCount })}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
