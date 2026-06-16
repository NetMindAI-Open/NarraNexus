/**
 * @file_name: AgentGroupSection.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Renders one collapsible team section inside the grouped
 * agent list. Owns the section header (team name, member count, collapse
 * toggle, aggregated unread pill) and the agent rows beneath it.
 * Agent row markup is shared logic with AgentList's expanded rendering;
 * the inline rename state is passed down so AgentList owns all mutations.
 */

import { useState } from 'react';
import { Loader2, Check, X, ArrowRight, Globe, Plus } from 'lucide-react';
import type { AgentInfo } from '@/types';
import { RingAvatar } from '@/components/nm';
import { AgentRowMenu } from './AgentRowMenu';
import { aggregateSectionUnread } from './agentGroupUtils';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RowMeta {
  preview: string;
  time: string;
  unread: number;
}

export interface AgentGroupSectionProps {
  teamId: string | null;
  teamName: string;
  teamColor: string | null;

  agents: AgentInfo[];
  agentId: string | null;

  /** Whether the section is collapsed (agent rows hidden). */
  collapsed: boolean;
  onToggleCollapse: (teamId: string | null) => void;

  /**
   * Render agent rows without the section header. Used by the pure
   * no-teams scenario, where a single "Ungrouped" header is noise.
   */
  hideHeader?: boolean;

  onSelectAgent: (agentId: string) => void;

  /**
   * Called when the user clicks the hover-visible → arrow on a named team
   * section header. Not called for the Ungrouped section (teamId=null).
   */
  onNavigateToTeam?: (teamId: string) => void;

  /**
   * Called when the user clicks the hover-visible + button on a named team
   * section header to create a new agent directly inside that team (#43).
   * Not offered for the Ungrouped section (teamId=null).
   */
  onAddAgentToTeam?: (teamId: string) => void;
  /** True while an agent create is in flight — disables the per-team +. */
  addingAgent?: boolean;

  getRowMeta: (agentId: string) => RowMeta;
  getIsStreaming: (agentId: string) => boolean;
  completedAgentIds: string[];

  /** Logged-in user — rows derive isOwner from agent.created_by. */
  currentUserId: string | null;
  /** Threaded from AgentList's SHOW_AGENT_PUBLIC_TOGGLE feature flag. */
  showPublicToggle: boolean;

  // Inline rename state — owned by AgentList, threaded down here.
  editingAgentId: string | null;
  editingName: string;
  onEditNameChange: (name: string) => void;
  onSaveEdit: (targetAgentId: string, e: React.SyntheticEvent) => void;
  onCancelEdit: (e: React.SyntheticEvent) => void;
  savingName: boolean;

  onStartEdit: (agent: AgentInfo, e: React.MouseEvent) => void;
  onDelete: (agent: AgentInfo, e: React.MouseEvent) => void;
  onTogglePublic: (agent: AgentInfo, e: React.MouseEvent) => void;
  deletingAgentId: string | null;
}

// ---------------------------------------------------------------------------
// AgentGroupSection
// ---------------------------------------------------------------------------

/**
 * One collapsible section in the grouped sidebar.
 *
 * The section header always renders; agent rows are conditionally hidden
 * when collapsed=true. An aggregated unread pill appears in the header
 * when the section is collapsed and the total unread > 0, so the user
 * can see there is activity without expanding.
 *
 * Ungrouped section (teamId=null) renders a hollow dot instead of a
 * filled color swatch to visually distinguish it from named teams.
 */
export function AgentGroupSection({
  teamId,
  teamName,
  teamColor,
  agents,
  agentId,
  collapsed,
  onToggleCollapse,
  hideHeader = false,
  onSelectAgent,
  onNavigateToTeam,
  onAddAgentToTeam,
  addingAgent = false,
  getRowMeta,
  getIsStreaming,
  completedAgentIds,
  currentUserId,
  showPublicToggle,
  editingAgentId,
  editingName,
  onEditNameChange,
  onSaveEdit,
  onCancelEdit,
  savingName,
  onStartEdit,
  onDelete,
  onTogglePublic,
  deletingAgentId,
}: AgentGroupSectionProps) {
  const totalUnread = aggregateSectionUnread(agents, (aid) => getRowMeta(aid).unread);

  return (
    <div className="group/section relative">
      {/* Section header */}
      {!hideHeader && (
      <button
        aria-label={teamName}
        data-help-id="sidebar.team-section"
        onClick={() => onToggleCollapse(teamId)}
        className={cn(
          'w-full flex items-center gap-2 px-3 py-1.5',
          'text-left transition-colors hover:bg-[var(--nm-paper-warm)]',
        )}
      >
        {/* Color swatch or hollow dot for Ungrouped */}
        {teamId !== null ? (
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: teamColor ?? 'var(--nm-ink30)' }}
          />
        ) : (
          <span
            className="w-2 h-2 rounded-full shrink-0 border"
            style={{ borderColor: 'var(--nm-ink30)' }}
          />
        )}

        <span
          className="flex-1 min-w-0 text-[11px] font-mono uppercase tracking-wider truncate"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {teamName}
        </span>

        {/* Member count */}
        <span
          className="text-[10px] font-mono shrink-0"
          style={{ color: 'var(--nm-ink30)' }}
        >
          {agents.length}
        </span>

        {/* Aggregated unread pill — visible only when section is collapsed
            and there is at least one unread message. */}
        {collapsed && totalUnread > 0 && (
          <span
            className="inline-flex items-center justify-center text-[10px] font-semibold shrink-0"
            style={{
              minWidth: 18,
              height: 16,
              padding: '0 5px',
              borderRadius: 8,
              background: 'transparent',
              border: '1px solid var(--nm-ink30)',
              color: 'var(--nm-ink70)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {totalUnread > 99 ? '99+' : totalUnread}
          </span>
        )}

        {/* Collapse arrow */}
        <span
          className={cn(
            'text-[10px] shrink-0 transition-transform duration-150',
            collapsed ? 'rotate-0' : 'rotate-90',
          )}
          style={{ color: 'var(--nm-ink30)' }}
          aria-hidden
        >
          ▶
        </span>
      </button>
      )}

      {/* Navigate-to-team arrow — hover-visible, only for named teams */}
      {!hideHeader && teamId !== null && onNavigateToTeam && (
        <button
          aria-label="Go to team"
          onClick={(e) => {
            e.stopPropagation();
            onNavigateToTeam(teamId);
          }}
          className={cn(
            'absolute right-8 top-0 h-7 w-6 flex items-center justify-center',
            'opacity-0 group-hover/section:opacity-100 transition-opacity duration-150',
            'hover:bg-[var(--nm-paper-warm)] rounded-[var(--radius-xs)]',
          )}
          title="Go to team detail"
        >
          <ArrowRight className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
        </button>
      )}

      {/* Add-agent-to-team + — hover-visible, only for named teams (#43) */}
      {!hideHeader && teamId !== null && onAddAgentToTeam && (
        <button
          aria-label="Add agent to team"
          disabled={addingAgent}
          onClick={(e) => {
            e.stopPropagation();
            onAddAgentToTeam(teamId);
          }}
          className={cn(
            'absolute right-14 top-0 h-7 w-6 flex items-center justify-center',
            'opacity-0 group-hover/section:opacity-100 transition-opacity duration-150',
            'hover:bg-[var(--nm-paper-warm)] rounded-[var(--radius-xs)]',
            addingAgent && 'opacity-100 cursor-not-allowed',
          )}
          title="Add a new agent to this team"
        >
          <Plus
            className={cn('w-3 h-3', addingAgent && 'animate-pulse')}
            style={{ color: 'var(--nm-ink50)' }}
          />
        </button>
      )}

      {/* Agent rows — a headerless section cannot be collapsed */}
      {(hideHeader || !collapsed) && (
        <div className="space-y-0.5 px-1 pb-1">
          {agents.map((agent, index) => (
            <AgentRow
              key={agent.agent_id}
              agent={agent}
              activeAgentId={agentId}
              index={index}
              getRowMeta={getRowMeta}
              getIsStreaming={getIsStreaming}
              completedAgentIds={completedAgentIds}
              currentUserId={currentUserId}
              showPublicToggle={showPublicToggle}
              editingAgentId={editingAgentId}
              editingName={editingName}
              onEditNameChange={onEditNameChange}
              onSaveEdit={onSaveEdit}
              onCancelEdit={onCancelEdit}
              savingName={savingName}
              onSelectAgent={onSelectAgent}
              onStartEdit={onStartEdit}
              onDelete={onDelete}
              onTogglePublic={onTogglePublic}
              deletingAgentId={deletingAgentId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentRow (private to this file)
// ---------------------------------------------------------------------------

interface AgentRowProps {
  agent: AgentInfo;
  activeAgentId: string | null;
  index: number;
  getRowMeta: (aid: string) => RowMeta;
  getIsStreaming: (aid: string) => boolean;
  completedAgentIds: string[];
  currentUserId: string | null;
  showPublicToggle: boolean;
  editingAgentId: string | null;
  editingName: string;
  onEditNameChange: (name: string) => void;
  onSaveEdit: (targetAgentId: string, e: React.SyntheticEvent) => void;
  onCancelEdit: (e: React.SyntheticEvent) => void;
  savingName: boolean;
  onSelectAgent: (agentId: string) => void;
  onStartEdit: (agent: AgentInfo, e: React.MouseEvent) => void;
  onDelete: (agent: AgentInfo, e: React.MouseEvent) => void;
  onTogglePublic: (agent: AgentInfo, e: React.MouseEvent) => void;
  deletingAgentId: string | null;
}

/** Single agent row — mirrors the AgentList row but scoped to the group context. */
function AgentRow({
  agent,
  activeAgentId,
  index,
  getRowMeta,
  getIsStreaming,
  completedAgentIds,
  currentUserId,
  showPublicToggle,
  editingAgentId,
  editingName,
  onEditNameChange,
  onSaveEdit,
  onCancelEdit,
  savingName,
  onSelectAgent,
  onStartEdit,
  onDelete,
  onTogglePublic,
  deletingAgentId,
}: AgentRowProps) {
  const isSelected = activeAgentId === agent.agent_id;
  const completed = completedAgentIds.includes(agent.agent_id);
  const { preview, time, unread } = getRowMeta(agent.agent_id);
  const streaming = getIsStreaming(agent.agent_id) || !!agent.active_run;
  const displayName = agent.name || agent.agent_id;

  const rowBg = isSelected
    ? 'var(--nm-row-active)'
    : unread > 0
      ? 'var(--color-silicon-soft)'
      : 'transparent';
  const allowHover = !isSelected && unread === 0;

  const isEditing = editingAgentId === agent.agent_id;
  const isOwner = agent.created_by === currentUserId;
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      onClick={() => onSelectAgent(agent.agent_id)}
      className={cn(
        'w-full text-left px-3 py-2 cursor-pointer animate-slide-up',
        'rounded-[18px] transition-colors duration-150',
        'group',
        // animate-slide-up retains a transform (fill: forwards), making
        // every row a stacking context — lift the row while its kebab
        // panel is open so the panel paints above the rows below.
        menuOpen && 'relative z-30',
      )}
      style={{
        animationDelay: `${index * 50}ms`,
        background: rowBg,
      }}
      onMouseEnter={(e) => {
        if (allowHover) {
          (e.currentTarget as HTMLDivElement).style.background = 'var(--nm-paper-warm)';
        }
      }}
      onMouseLeave={(e) => {
        if (allowHover) {
          (e.currentTarget as HTMLDivElement).style.background = 'transparent';
        }
      }}
    >
      <div className="flex items-start gap-2.5">
        {/* Avatar */}
        <div className="relative shrink-0">
          <AvatarWithStreaming label={displayName.slice(0, 2)} streaming={streaming} />
          {completed && !isSelected && (
            <div
              className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full allow-circle"
              style={{
                background: 'var(--color-warning)',
                border: '2px solid var(--nm-card)',
              }}
            />
          )}
        </div>

        {/* Right side */}
        <div className="flex-1 min-w-0">
          {isEditing ? (
            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <input
                type="text"
                value={editingName}
                onChange={(e) => onEditNameChange(e.target.value)}
                className="flex-1 min-w-0 px-2 py-0.5 text-sm font-mono text-[var(--nm-ink)] bg-[var(--nm-paper-warm)] border border-[var(--nm-ink)] rounded-[var(--radius-xs)] focus:outline-none"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSaveEdit(agent.agent_id, e);
                  if (e.key === 'Escape') onCancelEdit(e);
                }}
              />
              <button
                onClick={(e) => onSaveEdit(agent.agent_id, e)}
                disabled={savingName}
                className="p-1 shrink-0 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                title="Save (Enter)"
              >
                <Check className={cn('w-3.5 h-3.5', savingName && 'animate-pulse')} style={{ color: 'var(--color-success)' }} />
              </button>
              <button
                onClick={onCancelEdit}
                className="p-1 shrink-0 rounded-[var(--radius-xs)] hover:bg-[var(--nm-paper-warm)] transition-colors"
                title="Cancel (Esc)"
              >
                <X className="w-3.5 h-3.5" style={{ color: 'var(--color-error)' }} />
              </button>
            </div>
          ) : (
            <>
              {/* Row 1: name + kebab + time */}
              <div className="flex items-center gap-1">
                <span
                  className={cn('text-sm truncate', isSelected ? 'font-semibold' : 'font-medium')}
                  style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-sans)' }}
                >
                  {displayName}
                </span>
                {agent.is_public && !isOwner && (
                  <span title={`Public · by ${agent.created_by}`} className="shrink-0">
                    <Globe className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
                  </span>
                )}

                {/* Kebab menu — shown on hover or when selected */}
                <div
                  className={cn(
                    'shrink-0',
                    isSelected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
                    'transition-opacity duration-150',
                  )}
                  onClick={(e) => e.stopPropagation()}
                >
                  <AgentRowMenu
                    agentId={agent.agent_id}
                    agentName={displayName}
                    onOpenChange={setMenuOpen}
                    isOwner={isOwner}
                    isPublic={!!agent.is_public}
                    showPublicToggle={showPublicToggle}
                    onStartEdit={(e) => onStartEdit(agent, e)}
                    onDelete={(e) => { if (deletingAgentId !== agent.agent_id) onDelete(agent, e); }}
                    onTogglePublic={(e) => onTogglePublic(agent, e)}
                  />
                </div>

                <span
                  className="ml-auto pl-2 text-[10px] shrink-0"
                  style={{
                    color: unread > 0 ? 'var(--color-silicon)' : 'var(--nm-ink50)',
                    fontWeight: unread > 0 ? 500 : 400,
                    fontFamily: 'var(--font-mono)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {time}
                </span>
              </div>

              {/* Row 2: preview + unread pill */}
              <div className="flex items-center gap-2 mt-0.5">
                <p
                  className="flex-1 min-w-0 text-xs truncate leading-snug"
                  style={{ color: 'var(--nm-ink70)' }}
                >
                  {preview || (
                    <span style={{ color: 'var(--nm-ink30)' }}>No messages yet</span>
                  )}
                </p>
                {unread > 0 && (
                  <span
                    className="inline-flex items-center justify-center text-[10px] font-semibold shrink-0"
                    style={{
                      minWidth: 20,
                      height: 18,
                      padding: '0 6px',
                      borderRadius: 9,
                      background: 'transparent',
                      border: '1px solid var(--nm-ink30)',
                      color: 'var(--nm-ink70)',
                      fontFamily: 'var(--font-mono)',
                    }}
                  >
                    {unread > 99 ? '99+' : unread}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AvatarWithStreaming — shared with AgentList's collapsed avatar rail so
// both renderings keep the identical streaming-halo treatment.
// ---------------------------------------------------------------------------

export function AvatarWithStreaming({
  label,
  streaming,
  size = 'md',
}: {
  label: string;
  streaming: boolean;
  size?: 'sm' | 'md';
}) {
  if (streaming) {
    return (
      <div className="relative inline-flex items-center justify-center">
        <span
          className="absolute inset-0 rounded-full animate-ping pointer-events-none"
          style={{ border: '2px solid var(--color-yellow-500)' }}
        />
        <RingAvatar species="silicon" label={label} size={size} />
        <Loader2 className="absolute w-3 h-3 animate-spin text-[var(--color-yellow-500)]" />
      </div>
    );
  }
  return <RingAvatar species="silicon" label={label} size={size} />;
}
