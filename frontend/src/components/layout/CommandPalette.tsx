/**
 * CommandPalette — ⌘K quick-jump for the global top bar.
 *
 * A single flat list of commands: every agent (jump into its conversation)
 * plus the app's pages (Dashboard / Settings / System / Chat). Filter by
 * typing; arrow keys move the highlight; Enter runs it; Esc / backdrop closes.
 *
 * It is intentionally a *navigator*, not a kitchen-sink palette — no actions
 * with side effects beyond selecting an agent and routing, so there is nothing
 * to confirm or undo.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { LayoutDashboard, SlidersHorizontal, Server, MessagesSquare, CornerDownLeft } from 'lucide-react';
import { useConfigStore, useUIStore } from '@/stores';
import { RingAvatar } from '@/components/nm';
import { ALL_TABS } from '@/components/bookmarks';
import { cn } from '@/lib/utils';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface Cmd {
  id: string;
  label: string;
  hint: string;
  run: () => void;
  /** 'agent' renders a carbon/silicon ring; 'page' renders a lucide icon. */
  kind: 'agent' | 'page';
  icon?: typeof LayoutDashboard;
  avatar?: string;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const agents = useConfigStore((s) => s.agents);
  const setAgentId = useConfigStore((s) => s.setAgentId);
  const agentId = useConfigStore((s) => s.agentId);
  const requestPanel = useUIStore((s) => s.requestPanel);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<Cmd[]>(() => {
    const agentCmds: Cmd[] = agents.map((a) => ({
      id: `agent:${a.agent_id}`,
      label: a.name || a.agent_id,
      hint: t('layout.commandPalette.hintAgent'),
      kind: 'agent',
      avatar: (a.name || a.agent_id).slice(0, 2),
      run: () => {
        setAgentId(a.agent_id);
        navigate('/app/chat');
      },
    }));
    const pageHint = t('layout.commandPalette.hintPage');
    const pageCmds: Cmd[] = [
      { id: 'page:chat', label: t('layout.commandPalette.pageChat'), hint: pageHint, kind: 'page', icon: MessagesSquare, run: () => navigate('/app/chat') },
      { id: 'page:dashboard', label: t('layout.commandPalette.pageDashboard'), hint: pageHint, kind: 'page', icon: LayoutDashboard, run: () => navigate('/app/dashboard') },
      { id: 'page:settings', label: t('layout.commandPalette.pageSettings'), hint: pageHint, kind: 'page', icon: SlidersHorizontal, run: () => navigate('/app/settings') },
      { id: 'page:system', label: t('layout.commandPalette.pageSystem'), hint: pageHint, kind: 'page', icon: Server, run: () => navigate('/app/system') },
    ];
    // Context panels (awareness/jobs/…) — only meaningful with an agent
    // selected. This is the mobile entry point now that the right strip hides.
    const panelCmds: Cmd[] = agentId
      ? ALL_TABS.map((tab) => ({
          id: `panel:${tab.id}`,
          label: t(tab.labelKey),
          hint: t('layout.commandPalette.hintPanel'),
          kind: 'page',
          icon: tab.icon,
          run: () => {
            navigate('/app/chat');
            requestPanel(tab.id);
          },
        }))
      : [];
    return [...agentCmds, ...pageCmds, ...panelCmds];
  }, [agents, navigate, setAgentId, agentId, requestPanel, t]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q));
  }, [commands, query]);

  // Reset + focus on open.
  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      // rAF so the input exists + the overlay paints before focusing.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Keep the highlight in range as the filter narrows.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  if (!open) return null;

  const runCmd = (cmd: Cmd | undefined) => {
    if (cmd) {
      cmd.run();
      onClose();
    }
  };
  const runActive = () => runCmd(filtered[active]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      runActive();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh] px-4"
      style={{ background: 'var(--nm-backdrop)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-[560px] overflow-hidden rounded-[var(--radius-lg)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] shadow-[var(--nm-elev-3)]"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        {/* search field — slight editorial radius; carbon ring on focus,
            harsh global focus outline suppressed via .nx-cmdk-input */}
        <div className="p-2.5">
          <div className="flex items-center gap-2.5 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] px-3 py-2 transition-colors focus-within:border-[var(--color-carbon)]">
            <span className="font-[family-name:var(--font-mono)] text-[11px] tracking-[0.1em] text-[var(--text-tertiary)]">⌘K</span>
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('layout.commandPalette.placeholder')}
              className="nx-cmdk-input flex-1 bg-transparent text-sm text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)] outline-none"
            />
          </div>
        </div>

        {/* results */}
        <div className="max-h-[44vh] overflow-y-auto px-1.5 pb-1.5">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-[var(--text-tertiary)]">{t('layout.commandPalette.noMatches')}</div>
          ) : (
            filtered.map((c, i) => (
              <button
                key={c.id}
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => runCmd(c)}
                className={cn(
                  'flex w-full items-center gap-3 rounded-[var(--radius-md)] px-2.5 py-2 text-left transition-colors',
                  i === active ? 'bg-[var(--color-carbon-soft)]' : 'bg-transparent',
                )}
              >
                {c.kind === 'agent' ? (
                  <RingAvatar species="silicon" label={c.avatar || '??'} size="xs" className="shrink-0" />
                ) : (
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center text-[var(--text-tertiary)]">
                    {c.icon ? <c.icon className="h-4 w-4" /> : null}
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate text-sm text-[var(--nm-ink)]">{c.label}</span>
                <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
                  {c.hint}
                </span>
                {i === active && <CornerDownLeft className="h-3.5 w-3.5 text-[var(--color-carbon)]" />}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
