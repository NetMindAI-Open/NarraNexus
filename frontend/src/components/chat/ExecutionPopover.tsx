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
import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation();
  if (status === 'completed') {
    return <CheckCircle2 className="w-3 h-3 shrink-0" style={{ color: 'var(--color-success)' }} aria-label={t('chat.execution.statusCompleted')} />;
  }
  if (status === 'running') {
    return <Loader2 className="w-3 h-3 shrink-0 animate-spin" style={{ color: 'var(--color-warning)' }} aria-label={t('chat.execution.statusRunning')} />;
  }
  if (status === 'failed') {
    return <XCircle className="w-3 h-3 shrink-0" style={{ color: 'var(--color-error)' }} aria-label={t('chat.execution.statusFailed')} />;
  }
  return <Circle className="w-3 h-3 shrink-0" style={{ color: 'var(--nm-ink30)' }} aria-label={t('chat.execution.statusPending')} />;
}

/** Safely read a string field out of a Step's free-form details bag. */
function detailStr(details: Record<string, unknown> | undefined, key: string): string {
  const v = details?.[key];
  return typeof v === 'string' ? v : '';
}

export function ExecutionPopover({ steps }: ExecutionPopoverProps) {
  const { t } = useTranslation();
  // The chip shows the CURRENT stage by name, not "X/Y". The pipeline is a
  // stream of an unknown number of steps (the agent loop's tool-call count is
  // decided by the LLM at runtime), so steps.length is only "steps seen so
  // far", never a real total — the old "· completed/steps.length" always read
  // as X/(X+1) and meant nothing. The latest running step (or the last step)
  // is what's actually happening now.
  const current =
    [...steps].reverse().find((s) => s.status === 'running') ?? steps[steps.length - 1];
  const currentStage = current?.title ?? '';

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t('chat.execution.showSteps')}
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
          {t('chat.execution.processing')}
          {currentStage && (
            <span className="normal-case tracking-normal opacity-80 truncate max-w-[160px]">
              · {currentStage}
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
          {t('chat.execution.heading')}
        </div>
        <div className="max-h-[320px] overflow-y-auto py-1">
          {steps.length === 0 ? (
            <div
              className="px-4 py-4 text-xs"
              style={{ color: 'var(--text-tertiary)' }}
            >
              {t('chat.execution.waiting')}
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
                      {/* Detail already flows here in Step.description / .details
                          (e.g. the narrative match summary + why it was chosen)
                          — surface it instead of dropping it. Wraps, not
                          truncated, so the reason is readable. */}
                      {s.description && s.description !== s.title && (
                        <span
                          className="block text-[11px] leading-snug mt-0.5 break-words"
                          style={{ color: 'var(--text-tertiary)' }}
                        >
                          {s.description}
                        </span>
                      )}
                      {detailStr(s.details, 'selection_reason') && (
                        <span
                          className="block text-[11px] leading-snug mt-0.5 break-words italic"
                          style={{ color: 'var(--text-tertiary)' }}
                        >
                          {detailStr(s.details, 'selection_reason')}
                        </span>
                      )}
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
