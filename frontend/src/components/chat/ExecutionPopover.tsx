/**
 * @file_name: ExecutionPopover.tsx
 * @author:
 * @date: 2026-06-11
 * @description: The Processing chip in the chat header, now clickable —
 * opens a compact popover listing the run's pipeline steps live
 * (the execution view that used to live in the retired RuntimePanel,
 * resurrected per Owner request as a click-to-peek panel).
 *
 * Pure presentation: steps stream into chatStore exactly as before;
 * this component only renders them. Visible only while streaming —
 * the chip IS the trigger.
 */

import { Sparkles, CheckCircle2, Loader2, Circle, XCircle } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import type { Step } from '@/types';
import { cn } from '@/lib/utils';

export interface ExecutionPopoverProps {
  steps: Step[];
}

function StepStatusIcon({ status }: { status: Step['status'] }) {
  if (status === 'completed') {
    return <CheckCircle2 className="w-3 h-3 shrink-0" style={{ color: 'var(--color-success)' }} aria-label="completed" />;
  }
  if (status === 'running') {
    return <Loader2 className="w-3 h-3 shrink-0 animate-spin" style={{ color: 'var(--color-warning)' }} aria-label="running" />;
  }
  if (status === 'failed') {
    return <XCircle className="w-3 h-3 shrink-0" style={{ color: 'var(--color-error)' }} aria-label="failed" />;
  }
  return <Circle className="w-3 h-3 shrink-0" style={{ color: 'var(--nm-ink30)' }} aria-label="pending" />;
}

export function ExecutionPopover({ steps }: ExecutionPopoverProps) {
  const completed = steps.filter((s) => s.status === 'completed').length;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Show execution steps"
          className={cn(
            'flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em]',
            'cursor-pointer rounded-[var(--radius-xs)] px-1.5 py-0.5 -mx-1.5',
            'transition-colors hover:bg-[var(--nm-paper-warm)]',
          )}
          style={{
            color: 'var(--color-warning)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          <Sparkles className="w-3 h-3 animate-pulse" aria-hidden />
          Processing
          {steps.length > 0 && (
            <span className="tabular-nums normal-case tracking-normal opacity-80">
              · {completed}/{steps.length}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="end"
        className="w-[340px] p-0"
      >
        <div
          className="px-4 py-2.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] border-b"
          style={{ color: 'var(--text-tertiary)', borderColor: 'var(--nm-hairline)' }}
        >
          Execution
        </div>
        <div className="max-h-[320px] overflow-y-auto py-1">
          {steps.length === 0 ? (
            <div
              className="px-4 py-4 text-xs"
              style={{ color: 'var(--text-tertiary)' }}
            >
              Waiting for the first step…
            </div>
          ) : (
            <ul>
              {steps.map((s) => {
                const isSub = s.step.includes('.');
                return (
                  <li
                    key={s.id}
                    className={cn(
                      'flex items-start gap-2 px-4 py-1.5',
                      isSub && 'pl-8',
                    )}
                  >
                    <span className="mt-0.5">
                      <StepStatusIcon status={s.status} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className="block text-xs leading-snug truncate"
                        style={{
                          color:
                            s.status === 'running'
                              ? 'var(--text-primary)'
                              : 'var(--text-secondary)',
                        }}
                      >
                        <span
                          className="font-[family-name:var(--font-mono)] text-[10px] mr-1.5"
                          style={{ color: 'var(--text-tertiary)' }}
                        >
                          {s.step}
                        </span>
                        {s.title}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
