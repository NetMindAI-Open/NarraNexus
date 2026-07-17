/**
 * @file_name: BookmarkStrip.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Right-edge vertical bookmark strip — atomic-tab IA.
 *
 * Owner-decided structure: the smallest unit is an atomic tab (one tab =
 * one panel; centered icon + small caption so labels are readable without
 * tooltips). Categories from STRIP_CATEGORIES group the tabs; the groups
 * are divided by a hairline only (the mono text headers were dropped for a
 * cleaner, icon-only strip). Hover/active highlight in carbon (orange);
 * resting icons stay neutral so species color reads as accent, not noise.
 * Live signals from bookmarkStore render as a status overlay on the owning
 * tab: spinner (running), carbon pulse + count (attention), yellow dot (info).
 *
 * 64px wide; scrolls vertically if the window is short.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Loader2, ChevronsUpDown, Check } from 'lucide-react';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { useConfigStore } from '@/stores/configStore';
import { useChatStore } from '@/stores/chatStore';
import {
  STRIP_CATEGORIES,
  deriveTabStatus,
  type AtomicTabId,
  type AtomicTabDef,
  type TabStatus,
} from './tabs';
import { cn } from '@/lib/utils';

export interface BookmarkStripProps {
  agentId: string;
  activeTab: AtomicTabId | null;
  onOpen: (tab: AtomicTabId) => void;
}

export function BookmarkStrip({ agentId, activeTab, onOpen }: BookmarkStripProps) {
  const { t } = useTranslation();
  const agentState = useBookmarkStore((s) => s.agents[agentId]);

  return (
    <div
      className="flex flex-col w-16 shrink-0 select-none overflow-y-auto overflow-x-hidden py-1"
      style={{ borderLeft: '1px solid var(--nm-hairline)', scrollbarWidth: 'none' }}
      data-help-id="bookmarks.strip"
    >
      {/* Agent identity header — pins "this whole rail is <agent>'s" on top,
          and doubles as the quick agent switcher (click → pick another). */}
      <AgentRailHeader agentId={agentId} />

      {STRIP_CATEGORIES.map((category, ci) => (
        <div key={category.label} className="flex flex-col items-stretch">
          {/* Every group (after the first) is separated by a hairline. Brand-
              spine groups (Narra/Nexus) additionally show a colored title. */}
          {ci > 0 && (
            <div className="mx-2 my-1.5 border-t border-[var(--nm-hairline)]" aria-hidden />
          )}
          {category.title && (
            <div className="px-1 pt-1.5 pb-1 text-center" aria-hidden>
              <span
                className="block text-[8px] font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] leading-none"
                style={{
                  color:
                    category.accent === 'silicon'
                      ? 'var(--color-silicon)'
                      : 'var(--color-carbon)',
                }}
              >
                {category.titleKey ? t(category.titleKey) : category.title}
              </span>
            </div>
          )}

          {category.tabs.map((tab) => (
            <AtomicTab
              key={tab.id}
              tab={tab}
              active={activeTab === tab.id}
              status={deriveTabStatus(agentState, tab.id)}
              onOpen={onOpen}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentRailHeader — identity header + quick agent switcher
// ---------------------------------------------------------------------------

/**
 * The rail's top header: a tiny mono "agent" eyebrow + the agent name in
 * sentence case (an identity heading, not another uppercase tab caption).
 * Clicking it opens a popover to switch which agent the rail (and chat) is
 * scoped to — the same select action as the left sidebar's agent list, so
 * the user can hop agents without leaving the right edge.
 *
 * Hidden until the agent record (and name) is loaded. The popover is a Radix
 * portal so it escapes the strip's `overflow-x-hidden` / 64px width.
 */
function AgentRailHeader({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const agents = useConfigStore((s) => s.agents);
  const setAgentId = useConfigStore((s) => s.setAgentId);
  const setActiveAgent = useChatStore((s) => s.setActiveAgent);
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);

  const agentName = agents.find((a) => a.agent_id === agentId)?.name?.trim();
  if (!agentName) return null;

  const handleSelect = (id: string) => {
    setOpen(false);
    if (id !== agentId) {
      setAgentId(id);
      setActiveAgent(id);
    }
    if (location.pathname !== '/app/chat' && location.pathname !== '/app') {
      navigate('/app/chat');
    }
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-help-id="bookmarks.agent"
            aria-label={t('bookmarks.strip.currentAgentAria', { name: agentName })}
            title={t('bookmarks.strip.switchAgent')}
            className="group/agent w-full px-1 pt-2 pb-1.5 text-center cursor-pointer outline-none"
          >
            <span className="block mb-1 text-[7.5px] font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.13em] leading-none text-[var(--text-tertiary)]">
              {t('bookmarks.strip.agentEyebrow')}
            </span>
            <span className="flex items-center justify-center gap-0.5 px-0.5">
              <span className="truncate text-[12px] font-semibold leading-tight text-[var(--text-primary)]">
                {agentName}
              </span>
              <ChevronsUpDown
                className="w-2.5 h-2.5 shrink-0 text-[var(--text-tertiary)] group-hover/agent:text-[var(--color-carbon)] transition-colors"
                aria-hidden
              />
            </span>
          </button>
        </PopoverTrigger>
        <PopoverContent
          side="left"
          align="start"
          sideOffset={4}
          className="w-auto min-w-[160px] max-w-[240px] p-1"
        >
          <div className="px-2 pt-1 pb-1.5 text-[8px] font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            {t('bookmarks.strip.switchAgent')}
          </div>
          <div className="flex flex-col">
            {agents.map((a) => {
              const isCurrent = a.agent_id === agentId;
              const label = (a.name || a.agent_id).trim();
              return (
                <button
                  key={a.agent_id}
                  type="button"
                  onClick={() => handleSelect(a.agent_id)}
                  aria-current={isCurrent ? 'true' : undefined}
                  className={cn(
                    'flex items-center gap-2 w-full px-2 py-1.5 rounded-[var(--radius-sm)] text-left transition-colors',
                    'hover:bg-[var(--bg-elevated)]',
                  )}
                >
                  <span
                    className="w-1.5 h-1.5 shrink-0 rounded-full allow-circle"
                    style={{ background: 'var(--color-silicon)' }}
                    aria-hidden
                  />
                  <span
                    className={cn(
                      'flex-1 truncate text-[13px] leading-tight',
                      isCurrent
                        ? 'font-semibold text-[var(--color-carbon)]'
                        : 'text-[var(--text-primary)]',
                    )}
                  >
                    {label}
                  </span>
                  {isCurrent && (
                    <Check
                      className="w-3 h-3 shrink-0 text-[var(--color-carbon)]"
                      aria-hidden
                    />
                  )}
                </button>
              );
            })}
          </div>
        </PopoverContent>
      </Popover>
      <div className="mx-2 mb-0.5 border-t border-[var(--nm-hairline)]" aria-hidden />
    </>
  );
}

// ---------------------------------------------------------------------------
// AtomicTab
// ---------------------------------------------------------------------------

interface AtomicTabProps {
  tab: AtomicTabDef;
  active: boolean;
  status: TabStatus;
  onOpen: (tab: AtomicTabId) => void;
}

function AtomicTab({ tab, active, status, onOpen }: AtomicTabProps) {
  const { t } = useTranslation();
  const Icon = tab.icon;
  const label = t(tab.labelKey);
  const stripLabel = tab.stripLabelKey ? t(tab.stripLabelKey) : tab.stripLabel;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            aria-expanded={active}
            data-help-id={`bookmarks.${tab.id}`}
            onClick={() => onOpen(tab.id)}
            className={cn(
              'group relative flex flex-col items-center justify-center gap-0.5 h-[50px] w-full shrink-0',
              'cursor-pointer transition-colors duration-150',
              // No background highlight — only the icon + caption (and the
              // active edge rule) light up carbon.
            )}
            style={
              // Bookmark tongue: the active tab shows a 2px carbon rule on the
              // outer edge — the bit of the bookmark you can see from the
              // page edge.
              active ? { boxShadow: 'inset -2px 0 0 var(--color-carbon)' } : undefined
            }
          >
            <Icon
              className={cn(
                'w-5 h-5 transition-colors',
                status.status === 'attention'
                  ? 'text-[var(--color-carbon)] animate-pulse'
                  : active
                    ? 'text-[var(--color-carbon)]'
                    : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
              aria-hidden
            />

            {/* Status overlay — top-right corner of the tab */}
            {status.status === 'running' && (
              <Loader2
                className="absolute top-1 right-2 w-2.5 h-2.5 animate-spin"
                style={{ color: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}
            {status.status === 'attention' && status.badge !== undefined && status.badge > 0 && (
              <span
                aria-live="polite"
                className="absolute top-0.5 right-1.5 flex items-center justify-center min-w-[13px] h-[13px] px-0.5 rounded-full text-[8px] font-bold tabular-nums leading-none"
                style={{ background: 'var(--color-carbon)', color: 'var(--color-gray-50)' }}
              >
                {status.badge > 99 ? '99+' : status.badge}
              </span>
            )}
            {status.status === 'info' && (
              <span
                className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full allow-circle"
                style={{ background: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}

            {/* Caption — readable without a tooltip */}
            <span
              className={cn(
                'block max-w-full px-0.5 truncate text-center text-[8px] font-[family-name:var(--font-mono)] uppercase tracking-[0.06em] leading-none transition-colors',
                active
                  ? 'text-[var(--color-carbon)]'
                  : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
            >
              {stripLabel ?? label}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
