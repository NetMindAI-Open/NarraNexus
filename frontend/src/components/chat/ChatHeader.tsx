/**
 * @file_name: ChatHeader.tsx
 * @author:
 * @date: 2026-08-06
 * @description: The v4 chat header — the agent's name is the protagonist.
 *
 * Left: sidebar-expand button (only while the sidebar is collapsed) and the
 * agent ring avatar + name, which together form ONE button that navigates to
 * the agent's profile page. Right: the Chat / Inner Thoughts segmented
 * toggle, entry icons for Jobs / Inbox / Artifacts (with live badges from
 * the bookmark registry), the cost popover, the Model & framework button
 * (owner only) and a ⋯ detail menu listing the remaining agent panels
 * (Workspace / Channels / Skills / MCP / Smart Home).
 *
 * The Model & framework button is an OWNER-REQUIRED entry (2026-09-11): #383
 * removed it as "reachable from the profile page", and the Owner could no
 * longer find where to change an agent's framework. It opens the same
 * AgentLlmConfigPanel the profile page and the sidebar row menu open — do
 * not remove it as a duplicate door.
 *
 * Awareness and Network/Memory were dropped from the ⋯ menu (2026-08-27) —
 * they are reachable from the profile page. The agent-switcher dropdown
 * that used to hang off the name is gone for the same reason: clicking the
 * name means "who is this", and switching agents belongs to the sidebar.
 *
 * This component is ENTRY POINTS ONLY: every ⋯ item opens the same bookmark
 * drawer panels that the retired right-edge BookmarkStrip used to open
 * (via uiStore.requestPanel → ChatView's drawer). Panel internals are
 * unchanged by design — the v4 redesign relocated the doors, not the rooms.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { MoreVertical, ListTodo, Inbox, MonitorPlay, PanelLeft, Sparkles, SlidersHorizontal } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { IconButton } from '@/components/nm/button';
import { CostPopover } from '@/components/cost/CostPopover';
import { ExecutionPopover } from './ExecutionPopover';
import {
  allTabs,
  ArtifactsGlyph,
  deriveTabStatus,
  markTabOpened,
  tabDescKey,
  visibleTabs,
  type AtomicTabId,
  type BuiltinTabId,
} from '@/components/bookmarks';
import { useUIStore, useArtifactStore, useStudioStore, selectStudioOpen, selectStudioResumable } from '@/stores';
import { useDismissOnOutside } from '@/hooks';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { Step } from '@/types';
import { CHAT_HEADER_ACTIONS, PANELS, useRegistryEntries, visibleSlotEntries } from '@/platform/registries';
import { useWhenContext } from '@/platform/whenContext';

/** Detail-menu layout: config panels only. Awareness and the Network/Memory
 *  pair are dropped here — they now live on the agent's Profile page and
 *  don't need a second door.
 *
 *  Hardcoded ids, NOT derived from `allTabs()` (which is the unfiltered,
 *  registry-derived list — `tabDef` below only looks defs up by id). The
 *  creation studio's `builder` tab is not in this list either: it is
 *  rendered as a CONDITIONAL first item via `builderOffered`, i.e.
 *  `visibleTabs(ctx)` — the ONE place the `conditional` rule is applied,
 *  shared with the ⌘K palette. A permanent entry would offer a panel the
 *  conversation does not drive; but with the drawer closed this menu is
 *  the desktop's way back into a studio the user collapsed (the drawer's
 *  title switcher offers it too, via `visibleCategories`, while a drawer is
 *  open) — without it, "collapse" would silently mean "end". */
const DETAIL_GROUP_A: BuiltinTabId[] = ['workspace', 'channels', 'skills', 'mcp', 'smarthome'];

function tabDef(id: AtomicTabId) {
  return allTabs().find((t) => t.id === id)!;
}

export interface ChatHeaderProps {
  agentId: string | null;
  agentName: string;
  isStreaming: boolean;
  currentSteps: Step[];
  chatTab: 'conversation' | 'inner';
  onChatTabChange: (tab: 'conversation' | 'inner') => void;
  /** Opens the per-agent model & framework panel (AgentLlmConfigPanel).
   *  Omitted → no button: the host passes it only to the agent's owner (the
   *  llm-config routes 403 anyone else). */
  onOpenAgentConfig?: () => void;
}

export function ChatHeader({
  agentId,
  agentName,
  isStreaming,
  currentSteps,
  chatTab,
  onChatTabChange,
  onOpenAgentConfig,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [detailOpen, setDetailOpen] = useState(false);
  const headerActions = visibleSlotEntries(useRegistryEntries(CHAT_HEADER_ACTIONS), useWhenContext({ conversationKind: 'chat', agentId }));
  const browserOffered = useRegistryEntries(PANELS).some((entry) => entry.id === 'browser');
  const detailRef = useDismissOnOutside<HTMLDivElement>(detailOpen, () => setDetailOpen(false));

  // `from: 'chat'` is what makes the profile's breadcrumb say "back to Chat"
  // instead of "back to Agents" — the profile has two callers.
  const goToProfile = () => {
    if (!agentId) return;
    navigate(`/app/agents/${encodeURIComponent(agentId)}`, { state: { from: 'chat' } });
  };

  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const requestPanel = useUIStore((s) => s.requestPanel);
  const studioOpen = useStudioStore(selectStudioOpen(agentId));
  const studioResumable = useStudioStore(selectStudioResumable(agentId));
  const builderOffered = visibleTabs({ studioOpen, studioResumable }).some((t) => t.id === 'builder');

  const bookmarkState = useBookmarkStore((s) => (agentId ? s.agents[agentId] : undefined));
  const jobsStatus = deriveTabStatus(bookmarkState, 'jobs');
  const inboxStatus = deriveTabStatus(bookmarkState, 'inbox');

  const artifactCount = useArtifactStore((s) => s.artifacts.length);

  const openPanel = (id: AtomicTabId) => {
    if (!agentId) return;
    setDetailOpen(false);
    markTabOpened(agentId, id);
    requestPanel(id);
  };

  const iconBtn =
    'relative inline-flex h-[30px] w-[30px] items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink50)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]';

  return (
    <div
      className="hidden md:flex items-center justify-between gap-3 px-4 min-h-[52px] shrink-0 border-b"
      style={{ borderColor: 'var(--nm-hairline)' }}
    >
      {/* Left — expand + agent identity. The identity block is one button:
          avatar and name open the same profile page, so there is no dead
          half. Shrinking is min-w-0 down the chain with truncate on the name. */}
      <div className="flex items-center gap-2.5 min-w-0">
        {sidebarCollapsed && (
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            title={t('sidebar.expandTitle')}
            aria-label={t('sidebar.expandTitle')}
            className={cn(iconBtn, 'shrink-0 h-7 w-7')}
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          onClick={goToProfile}
          title={t('chat.header.viewProfile')}
          aria-label={t('chat.header.viewProfile')}
          className="flex min-w-0 items-center gap-1.5 rounded-[var(--radius-sm)] px-1.5 py-0.5 transition-colors hover:bg-[var(--nm-paper-warm)]"
        >
          <RingAvatar
            species="silicon"
            label={(agentName || 'AI').slice(0, 2)}
            size="sm"
            className="shrink-0"
          />
          {/* Same family as the sidebar row that shows this same name — the
              header keeps its lead role via size + weight, not a second
              typeface (design_system.md §4.1: display is for large titles). */}
          <span className="font-[family-name:var(--font-sans)] text-base font-semibold text-[var(--nm-ink)] truncate max-w-[220px]">
            {agentName}
          </span>
        </button>
      </div>

      {/* Right — streaming chip, segmented toggle, entry icons, detail ⋯ */}
      <div className="flex items-center gap-3 shrink-0">
        {isStreaming && <ExecutionPopover steps={currentSteps} />}

        {/* Chat / Inner Thoughts segmented toggle (v4 #11 — the underline
            tab row folded into the header). State lives in ChatPanel. */}
        <div className="inline-flex items-center gap-0.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] p-0.5">
          {(['conversation', 'inner'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => onChatTabChange(tab)}
              className={cn(
                'rounded-[var(--radius-sm)] px-2.5 py-[3px] text-[11px] transition-colors',
                chatTab === tab
                  ? 'bg-[var(--nm-raised)] font-semibold text-[var(--nm-ink)]'
                  : 'font-medium text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]',
              )}
            >
              {tab === 'conversation' ? t('chat.conversation') : t('chat.innerThoughts')}
            </button>
          ))}
        </div>

        {/* Panel-entry icon buttons. Each carries a styled Radix hover tooltip
            (the drawer they open, named) INSTEAD of the browser's slow native
            `title`, plus an aria-label so the icon has an accessible name.
            One provider wraps the whole cluster (design_system.md §5). */}
        <TooltipProvider delayDuration={200}>
        <div className="flex items-center gap-0.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => openPanel('jobs')}
                aria-label={t('rail.jobs')}
                data-help-id="bookmarks.jobs"
                className={iconBtn}
              >
                <ListTodo className="h-4 w-4" />
                {jobsStatus.badge ? <HeaderBadge count={jobsStatus.badge} /> : null}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t('rail.jobs')}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => openPanel('inbox')}
                aria-label={t('rail.inbox')}
                data-help-id="bookmarks.inbox"
                className={iconBtn}
              >
                <Inbox className="h-4 w-4" />
                {inboxStatus.badge ? <HeaderBadge count={inboxStatus.badge} /> : null}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t('rail.inbox')}</TooltipContent>
          </Tooltip>
          {/* Artifacts — opens the drawer panel like every other entry (the
              resizable side column is retired; Owner 2026-08-06). */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => openPanel('artifacts')}
                aria-label={t('rail.artifacts')}
                data-help-id="layout.artifacts"
                className={iconBtn}
              >
                <ArtifactsGlyph className="h-4 w-4" strokeWidth={1.8} />
                {artifactCount > 0 ? <HeaderBadge count={artifactCount} /> : null}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t('rail.artifacts')}</TooltipContent>
          </Tooltip>
          {browserOffered && (
            <Tooltip>
              <TooltipTrigger asChild>
                <IconButton
                  label={t('rail.browser', 'Browser')}
                  title={undefined}
                  size="sm"
                  appearance="plain"
                  disabled={!agentId}
                  onClick={() => openPanel('browser')}
                  className={iconBtn}
                >
                  <MonitorPlay className="h-4 w-4" />
                </IconButton>
              </TooltipTrigger>
              <TooltipContent side="bottom">{t('rail.browser', 'Browser')}</TooltipContent>
            </Tooltip>
          )}
          <span data-help-id="chat.cost">
            <CostPopover />
          </span>

          {/* Model & framework — Owner-required door (see file header). */}
          {onOpenAgentConfig && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={onOpenAgentConfig}
                  aria-label={t('chat.header.modelFramework')}
                  className={iconBtn}
                >
                  <SlidersHorizontal className="h-4 w-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">{t('chat.header.modelFramework')}</TooltipContent>
            </Tooltip>
          )}

          {/* Detail ⋯ menu — every agent panel, one door. */}
          <div ref={detailRef} className="relative">
            <button
              type="button"
              onClick={() => setDetailOpen((v) => !v)}
              title={t('chat.header.detailMenuTitle')}
              aria-label={t('chat.header.detailMenuTitle')}
              data-help-id="chat.detail-menu"
              className={cn(iconBtn, detailOpen && 'bg-[var(--nm-raised)] text-[var(--nm-ink)]')}
            >
              <MoreVertical className="h-4 w-4" />
            </button>
            {detailOpen && (
              <div
                className={cn(
                  'absolute right-0 top-full z-50 mt-2 w-[236px] p-1.5',
                  'rounded-[var(--radius-md)] border shadow-[0_8px_24px_rgba(0,0,0,0.14)]',
                  'bg-[var(--nm-card)] border-[var(--nm-hairline)]',
                )}
              >
                {builderOffered && <DetailItem id="builder" onOpen={openPanel} />}
                {DETAIL_GROUP_A.map((id) => (
                  <DetailItem key={id} id={id} onOpen={openPanel} />
                ))}
                {/* The divider only makes sense when there is something after it — with no
                    plugins contributing ui.chatHeaderActions entries, an unconditional divider
                    left a dangling rule at the bottom of the menu for every user (M-1). */}
                {headerActions.length > 0 && (
                  <div data-testid="chat-header-plugin-divider" className="my-1 mx-1 border-t border-[var(--nm-hairline)]" />
                )}
                {/* Plugin actions (ui.chatHeaderActions), gated by `when`. */}
                {headerActions.map((entry) => {
                  const Icon = entry.value.icon ?? Sparkles;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      data-slot-action={entry.id}
                      onClick={() => {
                        setDetailOpen(false);
                        void entry.value.run({ agentId });
                      }}
                      className="w-full flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-[7px] text-left text-[13px] font-medium text-[var(--nm-ink)] transition-colors hover:bg-[var(--nm-paper-warm)]"
                    >
                      <Icon className="h-[15px] w-[15px] text-[var(--nm-ink70)]" />
                      {entry.value.labelIsKey ? t(entry.value.label) : entry.value.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
        </TooltipProvider>
      </div>
    </div>
  );
}

function HeaderBadge({ count }: { count: number }) {
  return (
    <span className="absolute right-[3px] top-[2px] flex h-3 min-w-3 items-center justify-center rounded-full bg-[var(--nm-ink)] px-0.5 text-[9px] font-bold leading-none text-[var(--nm-paper)]">
      {count > 99 ? '99+' : count}
    </span>
  );
}

function DetailItem({ id, onOpen }: { id: AtomicTabId; onOpen: (id: AtomicTabId) => void }) {
  const { t } = useTranslation();
  const def = tabDef(id);
  const Icon = def.icon;
  // "Social Network" reads as just "Network" here — reuse the strip's
  // short label key.
  const labelKey = id === 'social' && def.stripLabelKey ? def.stripLabelKey : def.labelKey;
  return (
    <button
      type="button"
      onClick={() => onOpen(id)}
      // Hover title = the same one-sentence explainer the drawer header's
      // ? icon shows — orientation before the click, not only after.
      title={t(tabDescKey(id), '')}
      className="w-full flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-[7px] text-left text-[13px] font-medium text-[var(--nm-ink)] transition-colors hover:bg-[var(--nm-paper-warm)]"
    >
      <Icon className="h-[15px] w-[15px] text-[var(--nm-ink70)]" />
      {t(labelKey, def.label)}
    </button>
  );
}
