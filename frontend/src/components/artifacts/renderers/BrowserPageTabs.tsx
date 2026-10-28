/** @file_name: BrowserPageTabs.tsx
 * @description: Independent page selection with an explicit agent-follow mode.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Crosshair, Globe, Plus, X } from 'lucide-react';
import { IconButton } from '@/components/nm/button';
import { TextInput } from '@/components/nm/form';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { BrowserPage } from '@/types/browser';
import { cn } from '@/lib/utils';

interface Props {
  pages: BrowserPage[];
  selectedPageId: string | null;
  activePageId: string | null;
  followingActive: boolean;
  canControl: boolean;
  busy: boolean;
  send: (message: Record<string, unknown>) => void;
}

export function BrowserPageTabs({ pages, selectedPageId, activePageId, followingActive, canControl, busy, send }: Props) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const reveal = () => list.querySelector('[aria-selected="true"]')?.parentElement?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(list);
    return () => observer.disconnect();
  }, [selectedPageId, canControl]);
  if (!pages.length) return null;
  const current = pages.find(page => page.id === selectedPageId);
  const select = (page: BrowserPage) => {
    if (page.id !== selectedPageId) send({ type: 'select_page', page_id: page.id });
  };
  return <TooltipProvider delayDuration={300}>
    <div className="flex min-w-0 shrink-0 items-center border-b border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)]">
      <div ref={listRef} role="tablist" aria-label={t('browser.pages', 'Browser tabs')} className="flex min-w-0 flex-1 overflow-x-auto">
        {pages.map((page, index) => <div key={page.id} className={cn('flex w-44 shrink-0 items-center border-r border-[var(--nm-hairline)]',
          page.id === selectedPageId && 'bg-[var(--nm-paper)]')}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" role="tab" disabled={busy} aria-selected={page.id === selectedPageId} tabIndex={page.id === selectedPageId ? 0 : -1}
                className={cn('flex h-10 min-w-0 flex-1 items-center gap-2 border-b-2 border-transparent px-2 text-xs text-[var(--nm-ink70)]',
                  page.id === selectedPageId && 'border-[var(--nm-ink)] text-[var(--nm-ink)]')}
                onClick={() => select(page)} onKeyDown={event => {
                  const next = event.key === 'ArrowRight' ? (index + 1) % pages.length
                    : event.key === 'ArrowLeft' ? (index - 1 + pages.length) % pages.length
                      : event.key === 'Home' ? 0 : event.key === 'End' ? pages.length - 1 : null;
                  if (next === null) return;
                  event.preventDefault();
                  select(pages[next]);
                  (listRef.current?.querySelectorAll('[role="tab"]')[next] as HTMLElement)?.focus();
                }}>
                <Globe className="h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-left">{page.title || (page.url === 'about:blank' ? t('browser.newTab', 'New tab') : page.url)}</span>
                {page.id === activePageId && <span className="shrink-0 text-[10px] text-[var(--nm-ink)]">{t('browser.agentTab', 'Agent')}</span>}
              </button>
            </TooltipTrigger>
            <TooltipContent><div className="break-words">{page.title}</div><div className="break-all">{page.url}</div></TooltipContent>
          </Tooltip>
          {canControl && <Tooltip>
            <TooltipTrigger asChild><IconButton size="sm" appearance="plain" title={undefined} className="mr-1 shrink-0" label={t('browser.closeTab', 'Close tab')}
              disabled={busy} onClick={() => send({ type: 'close_page', page_id: page.id })}><X className="h-3 w-3" /></IconButton></TooltipTrigger>
            <TooltipContent>{t('browser.closeTab', 'Close tab')}</TooltipContent>
          </Tooltip>}
        </div>)}
      </div>
      <Tooltip>
        <TooltipTrigger asChild><span className="shrink-0"><IconButton size="sm" appearance="plain" title={undefined}
          label={t('browser.newTab', 'New tab')} disabled={!canControl || busy} onClick={() => send({ type: 'new_page' })}>
          <Plus className="h-3.5 w-3.5" />
        </IconButton></span></TooltipTrigger>
        <TooltipContent>{canControl ? t('browser.newTab', 'New tab') : t('browser.takeControlToNavigate', 'Take control to open a page')}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild><IconButton size="sm" appearance="plain" title={undefined} className="mx-1 shrink-0" label={t('browser.followAgent', 'Follow agent')}
          aria-pressed={followingActive} disabled={followingActive || busy} onClick={() => send({ type: 'follow_active' })}>
          <Crosshair className="h-3.5 w-3.5" />
        </IconButton></TooltipTrigger>
        <TooltipContent>{t('browser.followAgent', 'Follow agent')}</TooltipContent>
      </Tooltip>
    </div>
    {current && <BrowserAddress key={current.id} page={current} canControl={canControl} busy={busy} send={send} />}
  </TooltipProvider>;
}

function BrowserAddress({ page, canControl, busy, send }: Pick<Props, 'canControl' | 'busy' | 'send'> & { page: BrowserPage }) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const editing = useRef(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const address = page.url === 'about:blank' ? '' : page.url;
  useEffect(() => {
    if (!canControl || !editing.current) { setDraft(null); setInvalid(false); }
  }, [page.url, canControl]);
  useEffect(() => {
    if (page.url === 'about:blank' && canControl && !busy) inputRef.current?.focus();
  }, [page.id, page.url, canControl, busy]);

  return <form data-testid="browser-page-url" className="shrink-0 border-b border-[var(--nm-hairline)] p-2" onSubmit={event => {
    event.preventDefault();
    if (!canControl || busy) return;
    const value = (draft ?? address).trim();
    try {
      const hasScheme = /^[a-z][a-z\d+.-]*:/i.test(value) && !/^[^/:]+:\d+(?:[/?#]|$)/.test(value);
      const url = new URL(hasScheme ? value : `https://${value}`);
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) throw new Error('Invalid web address');
      setInvalid(false);
      setDraft(url.href);
      editing.current = false;
      inputRef.current?.blur();
      send({ type: 'navigate', page_id: page.id, url: url.href });
    } catch {
      setInvalid(true);
    }
  }}>
    <div className="flex min-w-0 items-center gap-1">
      <TextInput ref={inputRef} className="h-8 min-w-0 flex-1 px-2 [&_input]:min-w-0 [&_input]:text-xs" inputMode="url"
        aria-label={t('browser.webAddress', 'Web address')} placeholder={t('browser.webAddress', 'Web address')}
        autoComplete="off" autoCapitalize="none" spellCheck={false} readOnly={!canControl || busy} value={draft ?? address}
        error={invalid} aria-invalid={invalid} onFocus={event => { editing.current = true; event.currentTarget.select(); }}
        onBlur={() => { editing.current = false; }} onChange={event => { setDraft(event.target.value); setInvalid(false); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) { if (event.key === 'Enter') event.preventDefault(); return; }
          if (event.key === 'Escape') { setDraft(null); setInvalid(false); inputRef.current?.blur(); }
        }} />
      <Tooltip>
        <TooltipTrigger asChild><span className="shrink-0"><IconButton type="submit" size="sm" appearance="plain" title={undefined}
          label={t('browser.go', 'Go')} disabled={!canControl || busy || !(draft ?? address).trim()}>
          <ArrowRight className="h-3.5 w-3.5" />
        </IconButton></span></TooltipTrigger>
        <TooltipContent>{canControl ? t('browser.go', 'Go') : t('browser.takeControlToNavigate', 'Take control to open a page')}</TooltipContent>
      </Tooltip>
    </div>
    {invalid && <p role="alert" className="mt-1 text-xs text-[var(--color-error)]">{t('browser.invalidAddress', 'Enter an HTTP or HTTPS address.')}</p>}
  </form>;
}
