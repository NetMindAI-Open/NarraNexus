/**
 * @file_name: TeamChatRow.tsx
 * @author:
 * @date: 2026-06-23
 * @description: One team's group-chat entry in the sidebar's TEAMS section.
 * A row sized like an agent row: a chevron expander, a carbon·silicon split
 * avatar, the team (group-chat) name with an inline-rename + ⋮ menu, the
 * agent count, and an active highlight when that team's group chat is open.
 * Expanding the chevron lists the team's member agents inline (UI/UX doc
 * 2026-08-06); clicking a member jumps to that agent's own chat.
 *
 * Extracted from AgentGroupSection so teams (group chats) live in their own
 * top section, separate from the flat AGENTS list.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import { GroupAvatar } from '@/components/nm';
import { TeamRowMenu } from './TeamRowMenu';
import { cn } from '@/lib/utils';

export interface TeamMemberEntry {
  agentId: string;
  name: string;
}

export interface TeamChatRowProps {
  teamId: string;
  teamName: string;
  agentCount: number;
  /** True when this team's group chat is the open view → row is highlighted. */
  active: boolean;
  /** Member agents shown when the row is expanded. */
  members: TeamMemberEntry[];
  /** The agent whose own chat is currently open — highlights its member row. */
  activeAgentId?: string | null;
  onOpen: (teamId: string) => void;
  /** Open one member agent's own chat. */
  onSelectMember: (agentId: string) => void;
  onRename: (teamId: string, name: string) => void;
  onDelete: (teamId: string) => void;
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
  members,
  activeAgentId,
  onOpen,
  onSelectMember,
  onRename,
  onDelete,
  onAddAgent,
  addingAgent,
}: TeamChatRowProps) {
  const { t } = useTranslation();
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(teamName);
  const [menuOpen, setMenuOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);

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
    <div className={cn(menuOpen && 'relative z-30')}>
      <div
        onClick={() => { if (!renaming) onOpen(teamId); }}
        title={t('layout.teamChatRow.groupChatTitle', { name: teamName })}
        className={cn(
          'group/gc w-full text-left pl-1.5 pr-3 py-1.5 cursor-pointer rounded-[var(--radius-lg)] transition-colors duration-150',
          !active && 'hover:bg-[var(--nm-row-hover)]',
        )}
        style={active ? { background: 'var(--nm-row-active)' } : undefined}
      >
        <div className="flex items-center gap-1.5">
          {/* Expander — toggles the member list; the row itself still opens
              the group chat. */}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
            title={t('layout.teamChatRow.toggleMembers')}
            aria-label={t('layout.teamChatRow.toggleMembers')}
            aria-expanded={expanded}
            className="shrink-0 flex h-4 w-4 items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink30)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]"
          >
            <ChevronRight
              className={cn('h-3 w-3 transition-transform duration-150', expanded && 'rotate-90')}
            />
          </button>
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
              /* Name line — trailing order mirrors an agent row exactly:
                 meta (member count) then the ⋮ pinned at the row's right
                 edge, so its position never depends on the name's length. */
              <div className="flex items-center gap-1">
                <span
                  className="min-w-0 truncate text-sm font-medium"
                  style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-sans)' }}
                >
                  {teamName}
                </span>

                {/* Member count — on the right, like an agent row's timestamp */}
                <span
                  className="ml-auto pl-2 text-[10px] shrink-0"
                  style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}
                >
                  {t('layout.teamChatRow.agentCount', { count: agentCount })}
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
                    onDelete={() => onDelete(teamId)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Member list — indented under the row; each member opens that
          agent's OWN chat (the group chat stays on the row itself). */}
      {expanded && (
        <div className="ml-[26px] border-l border-[var(--nm-hairline)] pl-2 py-0.5 space-y-px">
          {members.length === 0 ? (
            <div className="px-2 py-1 text-[11px] italic text-[var(--nm-ink30)]">
              {t('layout.teamChatRow.noMembers')}
            </div>
          ) : (
            members.map((m) => {
              const isActive = activeAgentId === m.agentId;
              return (
                <button
                  key={m.agentId}
                  type="button"
                  onClick={() => onSelectMember(m.agentId)}
                  className={cn(
                    'w-full flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1 text-left text-[12px] transition-colors',
                    isActive
                      ? 'bg-[var(--nm-row-active)] text-[var(--nm-ink)] font-medium'
                      : 'text-[var(--nm-ink70)] hover:bg-[var(--nm-row-hover)] hover:text-[var(--nm-ink)]',
                  )}
                >
                  <span
                    className="h-[5px] w-[5px] shrink-0 rounded-full"
                    style={{ background: isActive ? 'var(--color-silicon)' : 'var(--nm-ink30)' }}
                  />
                  <span className="min-w-0 truncate">{m.name}</span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
