/**
 * @file_name: BrowserLookOutput.tsx
 * @author:
 * @date: 2026-09-24
 * @description: The browser_look output row (ui.toolRenderers): what the agent looked at, in one line, with a way into the live browser.
 *
 * The screenshot itself went straight to the model and is not stored, so
 * there is nothing to thumbnail here; the row says what was captured (image
 * size, the cropped area when not the whole viewport, the page title) and
 * offers the live browser panel, where the page actually is. It keeps the
 * generic output row's look — a process row in the agent's document takes
 * no surface of its own (design_system.md §2.6.1) — and the raw output stays
 * one click away, collapsed like every other output (iron rule #16).
 */
import { memo, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Eye, MonitorPlay } from 'lucide-react';

import type { ToolRendererProps } from '@/platform/registries';
import { parseBrowserLookOutput } from '@/lib/browserLook';
import { useUIStore } from '@/stores/uiStore';
import { cn } from '@/lib/utils';

export const BrowserLookOutput = memo(function BrowserLookOutput({ output, isStreaming }: ToolRendererProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const openPanel = useUIStore((s) => s.openPanel);
  const summary = useMemo(() => parseBrowserLookOutput(output), [output]);
  if (!summary) return null;

  const muted = { color: 'var(--nm-ink50)' };
  return (
    <div
      className={cn('pl-4 text-[10px]', isStreaming && 'animate-fade-in')}
      style={{ ...muted, fontFamily: 'var(--font-mono)' }}
      data-testid="browser-look-output"
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <button
          type="button"
          onClick={() => setExpanded((p) => !p)}
          aria-expanded={expanded}
          className="flex min-w-0 items-center gap-1.5 text-left"
        >
          {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
          <Eye className="h-3 w-3 shrink-0" />
          <span className="shrink-0" style={{ color: summary.ok ? 'var(--nm-ink70)' : 'var(--color-error)' }}>
            {summary.ok ? t('browser.look.looked', 'Looked at the page') : t('browser.look.failed', "Couldn't look at the page")}
          </span>
          {summary.image && <span className="shrink-0">{`${summary.image.width}×${summary.image.height}`}</span>}
          {summary.region && (
            <span className="shrink-0">
              {t('browser.look.area', 'area {{width}}×{{height}}', summary.region)}
            </span>
          )}
          {summary.ok && summary.title && (
            <span className="truncate" title={summary.url}>{summary.title}</span>
          )}
          {!summary.ok && summary.message && (
            <span className="truncate" style={{ color: 'var(--nm-ink70)' }}>{summary.message}</span>
          )}
        </button>
        <button
          type="button"
          onClick={() => openPanel('browser')}
          className="ml-auto flex shrink-0 items-center gap-1 rounded-[var(--radius-sm)] px-1 transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]"
        >
          <MonitorPlay className="h-3 w-3" />
          {t('browser.look.open', 'Open browser')}
        </button>
      </div>
      {expanded && (
        // No max-h / overflow — single parent scroll surface only (same as the generic row).
        <pre className="mt-1 whitespace-pre-wrap break-all" style={{ color: 'var(--nm-ink70)' }}>
          {output}
        </pre>
      )}
    </div>
  );
});
